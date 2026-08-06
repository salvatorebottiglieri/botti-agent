"""PostgreSQL implementation of memory repositories."""

from typing import Any
from uuid import UUID

from cortex.db.session import DbSession
from cortex.memory.interfaces import ConceptRepository, FactRepository
from cortex.memory.models import Concept, Fact, FactType


class PostgresFactRepository(FactRepository):
    """PostgreSQL implementation of fact storage."""

    async def store(self, fact: Fact) -> Fact:
        """Store a new fact."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                INSERT INTO facts (type, mutability, symbolic_repr, natural_lang_repr,
                                   payload, confidence, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                RETURNING id, type, mutability, symbolic_repr, natural_lang_repr,
                          payload, confidence, created_at, retracted_at,
                          last_accessed_at, access_count
                """,
                fact.type.value if hasattr(fact.type, 'value') else fact.type,
                fact.mutability.value if hasattr(fact.mutability, 'value') else fact.mutability,
                fact.symbolic_repr,
                fact.natural_lang_repr,
                fact.payload,
                fact.confidence,
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
                       payload, confidence, created_at, retracted_at,
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
        values = []
        i = 1

        for key, value in updates.items():
            if key in ("payload", "confidence", "natural_lang_repr"):
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
                      payload, confidence, created_at, retracted_at,
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
        """Search for facts by text query."""
        type_filter = ""
        params: list[Any] = [f"%{query}%", limit]

        if fact_types:
            type_values = [t.value if hasattr(t, 'value') else t for t in fact_types]
            placeholders = ", ".join(f"${i+2}" for i in range(len(type_values)))
            type_filter = f"AND type IN ({placeholders})"
            params.extend(type_values)

        if min_confidence is not None:
            type_count = len(fact_types) if fact_types else 0
            params.append(min_confidence)
            type_filter += f" AND confidence >= ${type_count + 3}"

        async with DbSession() as db:
            rows = await db.fetch(
                f"""
                SELECT id, type, mutability, symbolic_repr, natural_lang_repr,
                       payload, confidence, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE (symbolic_repr ILIKE $1 OR natural_lang_repr ILIKE $1)
                      AND retracted_at IS NULL
                      {type_filter}
                ORDER BY confidence DESC, created_at DESC
                LIMIT $2
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
                       payload, confidence, created_at, retracted_at,
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
                       payload, confidence, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE retracted_at IS NULL
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
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
                       payload, confidence, created_at, retracted_at,
                       last_accessed_at, access_count
                FROM facts
                WHERE symbolic_repr = $1 AND retracted_at IS NULL
                """,
                symbolic_repr,
            )
            if row is None:
                return None
            return self._row_to_fact(row)

    def _row_to_fact(self, row: Any) -> Fact:
        """Convert a database row to a Fact model."""
        return Fact(
            id=row["id"],
            type=row["type"],
            mutability=row["mutability"],
            symbolic_repr=row["symbolic_repr"],
            natural_lang_repr=row["natural_lang_repr"],
            payload=row["payload"] or {},
            confidence=row["confidence"],
            created_at=row["created_at"],
            retracted_at=row["retracted_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"] or 0,
        )


class PostgresConceptRepository(ConceptRepository):
    """PostgreSQL implementation of concept storage."""

    async def store(self, concept: Concept) -> Concept:
        """Store a new concept."""
        async with DbSession() as db:
            row = await db.fetchrow(
                """
                INSERT INTO concepts (symbolic_repr, natural_lang_repr, derivation_method,
                                     proof_chain, source_facts, confidence, validated)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id, symbolic_repr, natural_lang_repr, derivation_method,
                          proof_chain, source_facts, confidence, validated, created_at
                """,
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
