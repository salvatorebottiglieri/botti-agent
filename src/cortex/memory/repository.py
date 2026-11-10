"""PostgreSQL implementation of memory repositories."""

from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.memory.interfaces import ConceptRepository, FactRepository
from cortex.memory.models import Concept, Fact, FactType


def _vector_literal(values: list[float]) -> str:
    """Render a vector as the text literal pgvector accepts over asyncpg.

    asyncpg has no pgvector codec; the call sites cast the parameter
    (``$n::vector``) and let Postgres parse this representation.
    """
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def _parse_vector(value: Any) -> list[float] | None:
    """Parse a pgvector value into ``list[float]``.

    Accepts SQL NULL, pgvector's text representation (``"[1,2.5]"``) and an
    already-parsed sequence from a driver that registers a codec.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            stripped = stripped[1:]
        if stripped.endswith("]"):
            stripped = stripped[:-1]
        return [float(part) for part in stripped.split(",") if part.strip()]
    return [float(component) for component in value]


class PostgresFactRepository(FactRepository):
    """PostgreSQL implementation of fact storage."""

    async def store(self, fact: Fact) -> Fact:
        """Store a new fact."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                INSERT INTO facts (type, mutability, symbolic_repr, natural_lang_repr,
                                   payload, confidence, embedding, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7::vector, NOW())
                RETURNING id, type, mutability, symbolic_repr, natural_lang_repr,
                          payload, confidence, embedding, created_at, retracted_at,
                          last_accessed_at, access_count
                """,
                fact.type.value if hasattr(fact.type, 'value') else fact.type,
                fact.mutability.value if hasattr(fact.mutability, 'value') else fact.mutability,
                fact.symbolic_repr,
                fact.natural_lang_repr,
                fact.payload,
                fact.confidence,
                _vector_literal(fact.embedding) if fact.embedding is not None else None,
            )
            return self._row_to_fact(row)

    async def store_batch(self, facts: list[Fact]) -> list[Fact]:
        """Store multiple facts in a batch."""
        results = []
        for fact in facts:
            results.append(await self.store(fact))
        return results

    async def get(self, fact_id: UUID) -> Fact | None:
        """Get a fact by ID."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts WHERE id = $1
                """,
                fact_id,
            )
            if row is None:
                return None
            return self._row_to_fact(row)

    async def retract(self, fact_id: UUID, reason: str | None = None) -> None:
        """Retract (soft-delete) a fact."""
        async with DbSession() as db:
            await db.execute(
                """
                UPDATE facts SET retracted_at = NOW()
                WHERE id = $1
                """,
                fact_id,
            )

    async def update(self, fact_id: UUID, updates: dict[str, Any]) -> Fact | None:
        """Update a fact's fields."""
        # Build dynamic update query
        set_clauses = []
        values: list[Any] = []
        i = 1

        for key, value in updates.items():
            if key == "embedding":
                set_clauses.append(f"embedding = ${i}::vector")
                values.append(_vector_literal(value) if value is not None else None)
                i += 1
            elif key in ("payload", "confidence", "natural_lang_repr"):
                set_clauses.append(f"{key} = ${i}")
                values.append(value)
                i += 1

        if not set_clauses:
            return await self.get(fact_id)

        values.append(fact_id)
        query = f"""
            UPDATE facts SET {', '.join(set_clauses)}
            WHERE id = ${i}
            RETURNING id, type, mutability, symbolic_repr, natural_lang_repr,
                      payload, confidence, embedding, created_at, retracted_at,
                      last_accessed_at, access_count
        """

        async with DbSession() as db:
            row = await db.fetchrow(query, *values)
            if row is None:
                return None
            return self._row_to_fact(row)

    async def search(
        self,
        query: str = "",
        *,
        limit: int | None = None,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[Fact]:
        """Search for facts by text query or embedding.

        ``query`` is ignored when ``query_embedding`` is given: the search is
        an ANN lookup over stored embeddings ordered by cosine distance. With
        no embedding it falls back to the lexical ILIKE path. Default limit is
        50 for ANN and 10 for ILIKE.
        """
        effective_limit = (
            limit if limit is not None else (50 if query_embedding is not None else 10)
        )

        if query_embedding is not None:
            match_clause = "embedding IS NOT NULL AND retracted_at IS NULL"
            order_clause = "embedding <=> $1::vector"
            params: list[Any] = [_vector_literal(query_embedding)]
        else:
            match_clause = (
                "(symbolic_repr ILIKE $1 OR natural_lang_repr ILIKE $1)"
                " AND retracted_at IS NULL"
            )
            order_clause = "confidence DESC, created_at DESC"
            params = [f"%{query}%"]

        filters = ""
        next_param = 2
        if fact_types:
            type_values = [t.value if hasattr(t, 'value') else t for t in fact_types]
            placeholders = ", ".join(
                f"${index}" for index in range(next_param, next_param + len(type_values))
            )
            filters += f" AND type IN ({placeholders})"
            params.extend(type_values)
            next_param += len(type_values)

        if min_confidence is not None:
            filters += f" AND confidence >= ${next_param}"
            params.append(min_confidence)
            next_param += 1

        params.append(effective_limit)

        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE {match_clause}
                      {filters}
                ORDER BY {order_clause}
                LIMIT ${next_param}
                """,
                *params,
            )
            return [self._row_to_fact(row) for row in rows]

    async def get_by_type(
        self,
        fact_type: FactType,
        *,
        limit: int = 50,
        active_only: bool = True,
    ) -> list[Fact]:
        """Get facts by type."""
        type_val = fact_type.value if hasattr(fact_type, 'value') else fact_type
        retracted_filter = "AND retracted_at IS NULL" if active_only else ""

        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE type = $1 {retracted_filter}
                ORDER BY created_at DESC
                LIMIT $2
                """,
                type_val,
                limit,
            )
            return [self._row_to_fact(row) for row in rows]

    async def get_recent(self, *, limit: int = 20) -> list[Fact]:
        """Get recent facts."""
        async with DbSession() as db:
            rows = await db.fetch(
                """
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE retracted_at IS NULL
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [self._row_to_fact(row) for row in rows]

    async def list_by_session(
        self,
        session_id: UUID,
        fact_type: FactType | None = None,
        *,
        limit: int = 100,
    ) -> list[Fact]:
        """List active facts for a session, optionally filtered by type."""
        type_filter = ""
        params: list[Any] = [str(session_id), limit]
        if fact_type is not None:
            type_filter = "AND type = $2"
            params.insert(1, fact_type.value if hasattr(fact_type, "value") else fact_type)
        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE payload->>'session_id' = $1 AND retracted_at IS NULL {type_filter}
                ORDER BY created_at DESC
                LIMIT ${len(params)}
                """,
                *params,
            )
            return [self._row_to_fact(row) for row in rows]

    async def record_access(self, fact_id: UUID) -> None:
        """Record that a fact was accessed."""
        async with DbSession() as db:
            await db.execute(
                """
                UPDATE facts
                SET last_accessed_at = NOW(), access_count = access_count + 1
                WHERE id = $1
                """,
                fact_id,
            )

    async def get_by_symbolic_repr(self, symbolic_repr: str) -> Fact | None:
        """Get a fact by its symbolic representation."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE symbolic_repr = $1 AND retracted_at IS NULL
                """,
                symbolic_repr,
            )
            if row is None:
                return None
            return self._row_to_fact(row)

    async def backfill_embeddings(
        self,
        fact_ids: list[UUID],
        embeddings: list[list[float]],
    ) -> None:
        """Store embeddings for existing facts, one UPDATE per pair."""
        if len(fact_ids) != len(embeddings):
            raise ValueError(
                "fact_ids and embeddings must have the same length: "
                f"{len(fact_ids)} ids, {len(embeddings)} embeddings"
            )

        async with DbSession() as db:
            for fact_id, embedding in zip(fact_ids, embeddings, strict=True):
                await db.execute(
                    "UPDATE facts SET embedding = $2::vector WHERE id = $1",
                    fact_id,
                    _vector_literal(embedding),
                )

    def _row_to_fact(self, row: Any) -> Fact:
        """Convert a database row to a Fact model."""
        return Fact(
            id=row["id"],
            type=row["type"],
            mutability=row["mutability"],
            symbolic_repr=row["symbolic_repr"],
            natural_lang_repr=row["natural_lang_repr"],
            payload=row["payload"] or {},
            embedding=_parse_vector(row["embedding"]),
            confidence=row["confidence"],
            created_at=row["created_at"],
            retracted_at=row["retracted_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"] or 0,
        )


class PostgresConceptRepository(ConceptRepository):
    """PostgreSQL implementation of concept storage."""

    async def store(self, concept: Concept) -> Concept:
        """Store a new concept.

        The model's id is written explicitly: ``concept.validated`` is emitted
        before storage, so a DB-generated id would make the event point at a
        row that does not exist (#153).
        """
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                INSERT INTO concepts (id, symbolic_repr, natural_lang_repr, derivation_method,
                                     proof_chain, source_facts, confidence, validated)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id, symbolic_repr, natural_lang_repr, derivation_method,
                          proof_chain, source_facts, confidence, validated, created_at
                """,
                concept.id,
                concept.symbolic_repr,
                concept.natural_lang_repr,
                concept.derivation_method,
                concept.proof_chain,
                [str(f) for f in concept.source_facts],
                concept.confidence,
                concept.validated,
            )
            return self._row_to_concept(row)

    async def get(self, concept_id: UUID) -> Concept | None:
        """Get a concept by ID."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                SELECT id, symbolic_repr, natural_lang_repr, derivation_method,
                       proof_chain, source_facts, confidence, validated, created_at
                FROM concepts WHERE id = $1
                """,
                concept_id,
            )
            if row is None:
                return None
            return self._row_to_concept(row)

    async def retract(self, concept_id: UUID, reason: str | None = None) -> None:
        """Retract a concept."""
        async with DbSession() as db:
            await db.execute(
                "DELETE FROM concepts WHERE id = $1",
                concept_id,
            )

    async def get_by_symbolic_repr(self, symbolic_repr: str) -> Concept | None:
        """Get a concept by its symbolic representation."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                SELECT id, symbolic_repr, natural_lang_repr, derivation_method,
                       proof_chain, source_facts, confidence, validated, created_at
                FROM concepts WHERE symbolic_repr = $1
                """,
                symbolic_repr,
            )
            if row is None:
                return None
            return self._row_to_concept(row)

    async def get_validated(self, *, limit: int = 50) -> list[Concept]:
        """Get all validated concepts."""
        async with DbSession() as db:
            rows = await db.fetch(
                """
                SELECT id, symbolic_repr, natural_lang_repr, derivation_method,
                       proof_chain, source_facts, confidence, validated, created_at
                FROM concepts
                WHERE validated = true
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
            return [self._row_to_concept(row) for row in rows]

    async def invalidate_from_fact(self, fact_id: UUID) -> None:
        """Invalidate concepts that depend on a fact."""
        async with DbSession() as db:
            await db.execute(
                """
                UPDATE concepts SET validated = false
                WHERE $1 = ANY(source_facts)
                """,
                str(fact_id),
            )

    def _row_to_concept(self, row: Any) -> Concept:
        """Convert a database row to a Concept model."""
        return Concept(
            id=row["id"],
            symbolic_repr=row["symbolic_repr"],
            natural_lang_repr=row["natural_lang_repr"],
            derivation_method=row["derivation_method"],
            proof_chain=row["proof_chain"],
            source_facts=[UUID(f) for f in (row["source_facts"] or [])],
            confidence=row["confidence"],
            validated=row["validated"],
            created_at=row["created_at"],
        )
