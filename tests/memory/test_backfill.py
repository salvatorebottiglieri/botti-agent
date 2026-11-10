"""Tests for the embedding backfill runner (ADR-0008, issue #159).

Invariants under test:
    * Null-embedding facts are selected in bounded, keyset-paginated batches
      and each batch is embedded from the natural-language repr, falling back
      to the symbolic repr.
    * A provider failure marks that fact failed and leaves it null, while the
      rest of the batch is still written; the run never aborts.
    * Even a batch whose embeds all fail advances the cursor, so the run
      terminates instead of re-reading the same rows forever.
    * Idempotency comes from the query: a second run sees only rows that are
      still null.
    * ``backfill_embeddings`` receives ids and vectors aligned in fact order,
      and only for the facts that embedded successfully.
"""

from uuid import UUID, uuid4

import pytest

from cortex.memory.backfill import BackfillStats, EmbeddingBackfillRunner
from cortex.memory.models import Fact


def _fact(*, natural: str = "", symbolic: str = "", fact_id: UUID | None = None) -> Fact:
    return Fact(
        id=fact_id or uuid4(),
        symbolic_repr=symbolic,
        natural_lang_repr=natural,
    )


class FakeFactRepository:
    """Bounded pages of null-embedding facts plus a recording backfill."""

    def __init__(self, pages: list[list[Fact]] | None = None) -> None:
        self._pages = list(pages or [])
        self.list_calls: list[tuple[int, UUID | None]] = []
        self.backfill_calls: list[tuple[list[UUID], list[list[float]]]] = []

    async def list_missing_embeddings(
        self, *, limit: int = 100, after_id: UUID | None = None
    ) -> list[Fact]:
        self.list_calls.append((limit, after_id))
        if not self._pages:
            return []
        return self._pages.pop(0)

    async def backfill_embeddings(
        self, fact_ids: list[UUID], embeddings: list[list[float]]
    ) -> None:
        self.backfill_calls.append((list(fact_ids), list(embeddings)))


class MutableNullFactRepository:
    """Small in-memory model of ``embedding IS NULL AND id > after_id``.

    ``backfill_embeddings`` removes the written rows from the null set, which
    is what a second run against the real query would observe.
    """

    def __init__(self, facts: list[Fact]) -> None:
        self.null_facts: dict[UUID, Fact] = {fact.id: fact for fact in facts}
        self.list_calls: list[tuple[int, UUID | None]] = []
        self.backfill_calls: list[tuple[list[UUID], list[list[float]]]] = []

    async def list_missing_embeddings(
        self, *, limit: int = 100, after_id: UUID | None = None
    ) -> list[Fact]:
        self.list_calls.append((limit, after_id))
        eligible = sorted(self.null_facts.values(), key=lambda fact: fact.id)
        if after_id is not None:
            eligible = [fact for fact in eligible if fact.id > after_id]
        return eligible[:limit]

    async def backfill_embeddings(
        self, fact_ids: list[UUID], embeddings: list[list[float]]
    ) -> None:
        self.backfill_calls.append((list(fact_ids), list(embeddings)))
        for fact_id in fact_ids:
            del self.null_facts[fact_id]


class FakeLLMClient:
    """Embedding stand-in: per-text vectors, per-text failures, recorded calls."""

    def __init__(
        self,
        vectors: dict[str, list[float]] | None = None,
        error_texts: set[str] | None = None,
    ) -> None:
        self._vectors = vectors or {}
        self._error_texts = error_texts or set()
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        if text in self._error_texts:
            raise RuntimeError(f"provider down for {text!r}")
        return list(self._vectors[text])


class TestBackfillBatch:
    async def test_fills_one_batch_with_natural_text_and_aligned_vectors(self):
        """Witness: embedding the symbolic repr instead of the natural one (or
        zipping mismatched ids/vectors) writes the wrong vector for the wrong
        fact."""
        natural_fact = _fact(natural="The user lives in Rome", symbolic="location.home")
        symbolic_fact = _fact(natural="", symbolic="location.office")
        repo = FakeFactRepository([[natural_fact, symbolic_fact]])
        client = FakeLLMClient(
            vectors={
                "The user lives in Rome": [0.11, 0.22],
                "location.office": [0.33, 0.44],
            }
        )

        stats = await EmbeddingBackfillRunner(repo, client, batch_size=3).run()

        assert client.texts == ["The user lives in Rome", "location.office"]
        assert repo.backfill_calls == [
            (
                [natural_fact.id, symbolic_fact.id],
                [[0.11, 0.22], [0.33, 0.44]],
            )
        ]
        assert repo.list_calls == [(3, None)]
        assert stats == BackfillStats(scanned=2, updated=2, failed=0)

    async def test_pages_until_a_short_page_and_advances_after_id(self):
        """Witness: a fixed LIMIT/OFFSET or a cursor that never advances
        re-reads the first page or skips rows. 3 facts, batch 2 -> two pages,
        the second keyed after the last id of the first."""
        first, second, third = uuid4(), uuid4(), uuid4()
        page_one = [
            _fact(natural="one", fact_id=first),
            _fact(natural="two", fact_id=second),
        ]
        page_two = [_fact(natural="three", fact_id=third)]
        repo = FakeFactRepository([page_one, page_two])
        client = FakeLLMClient(
            vectors={"one": [1.0], "two": [2.0], "three": [3.0]}
        )

        stats = await EmbeddingBackfillRunner(repo, client, batch_size=2).run()

        assert repo.list_calls == [(2, None), (2, second)]
        assert [call[0] for call in repo.backfill_calls] == [
            [first, second],
            [third],
        ]
        assert stats == BackfillStats(scanned=3, updated=3, failed=0)

    async def test_partial_provider_failure_still_backfills_the_rest(self):
        """Witness: an exception escaping the loop aborts the run and loses the
        healthy fact; only the successful pair may be written."""
        failed = _fact(natural="provider fails on this one")
        healthy = _fact(natural="provider returns this one")
        repo = FakeFactRepository([[failed, healthy]])
        client = FakeLLMClient(
            vectors={"provider returns this one": [0.5, 0.6]},
            error_texts={"provider fails on this one"},
        )

        stats = await EmbeddingBackfillRunner(repo, client, batch_size=2).run()

        assert repo.backfill_calls == [([healthy.id], [[0.5, 0.6]])]
        assert stats == BackfillStats(scanned=2, updated=1, failed=1)

    async def test_all_failed_batch_advances_the_cursor_and_terminates(self):
        """Witness: not advancing after_id when every embed failed loops on the
        same batch forever; the next page must start after its last id."""
        first, second = uuid4(), uuid4()
        repo = FakeFactRepository(
            [
                [_fact(natural="boom one", fact_id=first), _fact(natural="boom two", fact_id=second)],
                [],
            ]
        )
        client = FakeLLMClient(error_texts={"boom one", "boom two"})

        stats = await EmbeddingBackfillRunner(repo, client, batch_size=2).run()

        assert repo.list_calls == [(2, None), (2, second)]
        assert repo.backfill_calls == []
        assert stats == BackfillStats(scanned=2, updated=0, failed=2)

    async def test_empty_table_scans_nothing_and_writes_nothing(self):
        repo = FakeFactRepository([])
        client = FakeLLMClient()

        stats = await EmbeddingBackfillRunner(repo, client).run()

        assert repo.list_calls == [(100, None)]
        assert repo.backfill_calls == []
        assert client.texts == []
        assert stats == BackfillStats(scanned=0, updated=0, failed=0)

    async def test_empty_text_fails_without_calling_the_provider(self):
        """Witness: embedding "" sends a useless request and may mask the row
        as embedded; it must be reported failed."""
        blank = _fact(natural="", symbolic="")
        repo = FakeFactRepository([[blank]])
        client = FakeLLMClient()

        stats = await EmbeddingBackfillRunner(repo, client).run()

        assert client.texts == []
        assert repo.backfill_calls == []
        assert stats == BackfillStats(scanned=1, updated=0, failed=1)

    async def test_second_run_only_sees_still_null_rows(self):
        """Witness: idempotency is the null predicate, not runner bookkeeping;
        after a successful first run the second scans nothing."""
        facts = [_fact(natural="alpha"), _fact(natural="beta")]
        repo = MutableNullFactRepository(facts)
        client = FakeLLMClient(vectors={"alpha": [0.1], "beta": [0.2]})
        runner = EmbeddingBackfillRunner(repo, client, batch_size=10)

        first = await runner.run()
        second = await runner.run()

        assert first == BackfillStats(scanned=2, updated=2, failed=0)
        assert second == BackfillStats(scanned=0, updated=0, failed=0)
        assert len(repo.backfill_calls) == 1
        assert repo.backfill_calls[0][0] == sorted(fact.id for fact in facts)

    def test_batch_size_below_one_is_rejected(self):
        repo = FakeFactRepository([])

        with pytest.raises(ValueError, match="batch_size"):
            EmbeddingBackfillRunner(repo, FakeLLMClient(), batch_size=0)
