"""Tests for FactStore."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.memory.fact_store import FactStore
from cortex.memory.interfaces import FactRepository
from cortex.memory.models import Fact, FactMutability, FactType


class TestFactStoreCRUD:
    """CRUD operations delegate to the underlying repository."""

    @pytest.fixture
    def repo(self):
        """Create a mock fact repository."""
        repo = MagicMock(spec=FactRepository)
        repo.get_by_symbolic_repr = AsyncMock(return_value=None)
        return repo

    @pytest.fixture
    def store(self, repo):
        """Create a FactStore over the mock repository."""
        return FactStore(repo)

    @pytest.mark.asyncio
    async def test_add_fact_stores_and_returns(self, store, repo):
        """add_fact stores a new fact and returns it."""
        fact = Fact(symbolic_repr="location.home", natural_lang_repr="At home")
        repo.store.return_value = fact

        result = await store.add_fact(fact)

        repo.get_by_symbolic_repr.assert_awaited_once_with("location.home")
        repo.store.assert_awaited_once_with(fact)
        assert result == fact

    @pytest.mark.asyncio
    async def test_get_fact_returns_repo_get(self, store, repo):
        """get_fact delegates to the repository."""
        fact_id = uuid4()
        fact = Fact(id=fact_id, symbolic_repr="location.home")
        repo.get.return_value = fact

        result = await store.get_fact(fact_id)

        repo.get.assert_awaited_once_with(fact_id)
        assert result == fact

    @pytest.mark.asyncio
    async def test_get_fact_missing_returns_none(self, store, repo):
        """get_fact returns None when the fact does not exist."""
        fact_id = uuid4()
        repo.get.return_value = None

        result = await store.get_fact(fact_id)

        repo.get.assert_awaited_once_with(fact_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_update_fact_delegates(self, store, repo):
        """update_fact passes updates through to the repository."""
        fact_id = uuid4()
        updates = {"natural_lang_repr": "Updated", "confidence": 0.9}
        updated = Fact(id=fact_id, natural_lang_repr="Updated", confidence=0.9)
        repo.update.return_value = updated

        result = await store.update_fact(fact_id, updates)

        repo.update.assert_awaited_once_with(fact_id, updates)
        assert result == updated

    @pytest.mark.asyncio
    async def test_retract_fact_returns_true_when_exists(self, store, repo):
        """retract_fact retracts an existing fact and returns True."""
        fact_id = uuid4()
        repo.get.return_value = Fact(id=fact_id)

        result = await store.retract_fact(fact_id, reason="Outdated")

        repo.get.assert_awaited_once_with(fact_id)
        repo.retract.assert_awaited_once_with(fact_id, "Outdated")
        assert result is True

    @pytest.mark.asyncio
    async def test_retract_fact_returns_false_when_missing(self, store, repo):
        """retract_fact returns False and does not retract a missing fact."""
        fact_id = uuid4()
        repo.get.return_value = None

        result = await store.retract_fact(fact_id, reason="Outdated")

        repo.get.assert_awaited_once_with(fact_id)
        repo.retract.assert_not_called()
        assert result is False


class TestFactStoreDedup:
    """add_fact deduplicates by symbolic representation."""

    @pytest.fixture
    def repo(self):
        """Create a mock fact repository."""
        return MagicMock(spec=FactRepository)

    @pytest.fixture
    def store(self, repo):
        """Create a FactStore over the mock repository."""
        return FactStore(repo)

    @pytest.mark.asyncio
    async def test_mutable_duplicate_updates_existing(self, store, repo):
        """A mutable fact with an existing symbolic_repr updates the existing fact."""
        existing = Fact(
            id=uuid4(),
            symbolic_repr="location.home",
            mutability=FactMutability.MUTABLE,
        )
        repo.get_by_symbolic_repr.return_value = existing
        updated = Fact(
            id=existing.id,
            symbolic_repr="location.home",
            natural_lang_repr="At work now",
            payload={"place": "work"},
            confidence=0.9,
        )
        repo.update.return_value = updated

        fact = Fact(
            symbolic_repr="location.home",
            natural_lang_repr="At work now",
            payload={"place": "work"},
            confidence=0.9,
        )
        result = await store.add_fact(fact)

        repo.store.assert_not_called()
        repo.update.assert_awaited_once_with(
            existing.id,
            {
                "natural_lang_repr": "At work now",
                "payload": {"place": "work"},
                "confidence": 0.9,
            },
        )
        assert result == updated

    @pytest.mark.asyncio
    async def test_static_duplicate_returns_existing_without_insert(self, store, repo):
        """A STATIC fact with an existing symbolic_repr returns it without inserting."""
        existing = Fact(
            id=uuid4(),
            symbolic_repr="user.birthdate",
            mutability=FactMutability.STATIC,
            natural_lang_repr="Born 1990",
        )
        repo.get_by_symbolic_repr.return_value = existing

        fact = Fact(
            symbolic_repr="user.birthdate",
            mutability=FactMutability.STATIC,
            natural_lang_repr="Born 1990",
        )
        result = await store.add_fact(fact)

        repo.store.assert_not_called()
        repo.update.assert_not_called()
        assert result == existing

    @pytest.mark.asyncio
    async def test_existing_static_with_mutable_incoming_returns_existing(self, store, repo):
        """An incoming MUTABLE fact does not overwrite an existing STATIC fact."""
        existing = Fact(
            id=uuid4(),
            symbolic_repr="user.birthdate",
            mutability=FactMutability.STATIC,
            natural_lang_repr="Born 1990",
        )
        repo.get_by_symbolic_repr.return_value = existing

        fact = Fact(
            symbolic_repr="user.birthdate",
            mutability=FactMutability.MUTABLE,
            natural_lang_repr="Born 1991",
            payload={"year": 1991},
        )
        result = await store.add_fact(fact)

        repo.update.assert_not_called()
        repo.store.assert_not_called()
        assert result == existing

    @pytest.mark.asyncio
    async def test_mutable_duplicate_update_failure_raises(self, store, repo):
        """A failed update of an existing mutable fact raises."""
        existing = Fact(id=uuid4(), symbolic_repr="location.home")
        repo.get_by_symbolic_repr.return_value = existing
        repo.update.return_value = None

        with pytest.raises(Exception, match="Fact update not return the new fact"):
            await store.add_fact(Fact(symbolic_repr="location.home"))


class TestFactStoreListFacts:
    """list_facts delegates session-filtering to the repository."""

    @pytest.fixture
    def repo(self):
        """Create a mock fact repository."""
        return MagicMock(spec=FactRepository)

    @pytest.fixture
    def store(self, repo):
        """Create a FactStore over the mock repository."""
        return FactStore(repo)

    @pytest.mark.asyncio
    async def test_list_facts_delegates_to_repo(self, store, repo):
        """list_facts delegates to repo.list_by_session with session and limit."""
        session_id = uuid4()
        facts = [
            Fact(symbolic_repr="fact.a", payload={"session_id": str(session_id)}),
        ]
        repo.list_by_session.return_value = facts

        result = await store.list_facts(session_id)

        repo.list_by_session.assert_awaited_once_with(
            session_id=session_id, fact_type=None, limit=500
        )
        assert result == facts

    @pytest.mark.asyncio
    async def test_list_facts_passes_fact_type(self, store, repo):
        """list_facts with a fact_type passes it through to repo.list_by_session."""
        session_id = uuid4()
        facts = [
            Fact(
                type=FactType.PAYMENT,
                symbolic_repr="payment.tesco",
                payload={"session_id": str(session_id)},
            ),
        ]
        repo.list_by_session.return_value = facts

        result = await store.list_facts(session_id, FactType.PAYMENT)

        repo.list_by_session.assert_awaited_once_with(
            session_id=session_id, fact_type=FactType.PAYMENT, limit=500
        )
        assert result == facts

    @pytest.mark.asyncio
    async def test_list_facts_returns_all_repo_facts(self, store, repo):
        """list_facts returns every fact the repository returns — no in-memory truncation."""
        session_id = uuid4()
        facts = [
            Fact(symbolic_repr=f"fact.{i}", payload={"session_id": str(session_id)})
            for i in range(600)
        ]
        repo.list_by_session.return_value = facts

        result = await store.list_facts(session_id)

        repo.list_by_session.assert_awaited_once_with(
            session_id=session_id, fact_type=None, limit=500
        )
        assert len(result) == 600
        assert result == facts
