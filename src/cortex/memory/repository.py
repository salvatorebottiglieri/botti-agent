"""PostgreSQL implementation of memory repositories."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.memory.interfaces import ConceptRepository, FactRepository
from cortex.memory.models import Concept, Fact, FactType

# ivfflat's default probes=1 under-returns candidates; probe 10 lists per query.
_ANN_PROBES = 10
# The ANN stage is a candidate generator; symbolic re-ranking narrows it down.
_ANN_CANDIDATE_LIMIT = 50
_RECENCY_WINDOW_HOURS = 24.0
_RECENCY_BOOST = 0.1
_CONFIDENCE_BOOST = 0.1


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
        query: str,
        *,
        limit: int = 10,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None,
    ) -> list[Fact]:
        """Search for facts by lexical ILIKE match."""
        match_clause = (
            "(symbolic_repr ILIKE $1 OR natural_lang_repr ILIKE $1)"
            " AND retracted_at IS NULL"
        )

        filters = ""
        params: list[Any] = [f"%{query}%"]
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

        params.append(limit)

        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE {match_clause}
                      {filters}
                ORDER BY confidence DESC, created_at DESC
                LIMIT ${next_param}
                """,
                *params,
            )
            return [self._row_to_fact(row) for row in rows]

    async def search_semantic(
        self,
        query_embedding: list[float],
        *,
        limit: int = 10,
        fact_types: list[FactType] | None = None,
        min_confidence: float | None = None,
    ) -> list[Fact]:
        """Search semantically: ANN candidates, then symbolic re-ranking.

        The candidate pool is fetched once; FactType and confidence filters
        and the recency/confidence boosts are applied in memory, so the DB is
        never re-queried for the final ranking.
        """
        candidates = await self._ann_search(query_embedding)
        ranked = self._re_rank(candidates, fact_types, min_confidence)
        return ranked[:limit]

    async def _ann_search(self, query_embedding: list[float]) -> list[tuple[Fact, float]]:
        """Return the top ANN candidates with their cosine similarity."""
        async with DbSession() as db:
            await db.execute(f"SET ivfflat.probes = {_ANN_PROBES}")
            rows = await db.fetch(
                f"""
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, embedding, created_at, retracted_at,
                       last_accessed_at, access_count,
                       1 - (embedding <=> $1::vector) AS similarity
                FROM facts
                WHERE embedding IS NOT NULL AND retracted_at IS NULL
                ORDER BY embedding <=> $1::vector
                LIMIT {_ANN_CANDIDATE_LIMIT}
                """,
                _vector_literal(query_embedding),
            )
            return [(self._row_to_fact(row), float(row["similarity"])) for row in rows]

    def _re_rank(
        self,
        candidates: list[tuple[Fact, float]],
        fact_types: list[FactType] | None,
        min_confidence: float | None,
    ) -> list[Fact]:
        """Apply symbolic filters and boosts to ANN candidates, best first.

        ``score = cosine_similarity + recency_boost + confidence_boost``;
        equal scores fall back to the newer ``created_at``.
        """
        now = datetime.now(UTC)
        ranked: list[tuple[float, datetime, Fact]] = []

        for fact, similarity in candidates:
            if fact_types is not None and fact.type not in fact_types:
                continue
            if min_confidence is not None and fact.confidence < min_confidence:
                continue

            created_at = fact.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            recency_boost = (
                _RECENCY_BOOST
                if now - created_at <= timedelta(hours=_RECENCY_WINDOW_HOURS)
                else 0.0
            )
            score = similarity + recency_boost + _CONFIDENCE_BOOST * fact.confidence
            ranked.append((score, created_at, fact))

        ranked.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
        return [fact for _, _, fact in ranked]

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
        """Store embeddings for existing facts, one UPDATE per pair.

        The ivfflat index is built before bulk embedding; run
        ``REINDEX INDEX idx_facts_embedding;`` after a large backfill so the
        index reflects the new vectors.
        """
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
