"""Tests for validated concept storage through ``ContextProvider.store_concept``."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.memory.context_provider import ContextProvider
from cortex.memory.fact_store import FactStore
from cortex.memory.interfaces import ConceptRepository, FactRepository
from cortex.memory.models import Concept, ConceptDerivation, Fact, FactType
from cortex.validation.derivation_service import ConceptDerivationService
from cortex.validation.logic_engine import LogicEngine
from cortex.validation.models import DerivationOutcome
from cortex.validation.service import ConceptValidationService


def _derivation(
    *,
    concept: Concept | None = None,
    proof_chain: list[str] | None = None,
    source_fact_ids: list[uuid.UUID] | None = None,
) -> ConceptDerivation:
    return ConceptDerivation(
        concept=concept or Concept(symbolic_repr="concept.test"),
        proof_chain=proof_chain if proof_chain is not None else ["derived(user, x)"],
        source_fact_ids=source_fact_ids or [],
    )


def _fact_repo() -> MagicMock:
    return MagicMock(spec=FactRepository)


def _concept_repo() -> MagicMock:
    repo = MagicMock(spec=ConceptRepository)
    repo.store = AsyncMock(side_effect=lambda concept: concept)
    return repo


def _derivation_service(outcome: DerivationOutcome) -> MagicMock:
    service = MagicMock(spec=ConceptDerivationService)
    service.load_facts = MagicMock()
    service.derive_and_validate = AsyncMock(return_value=outcome)
    return service


def _provider(
    *,
    fact_repo: MagicMock,
    concept_repo: MagicMock | None,
    derivation_service: MagicMock | None,
) -> ContextProvider:
    return ContextProvider(
        fact_repository=fact_repo,
        fact_store=MagicMock(spec=FactStore),
        concept_repository=concept_repo,
        concept_derivation_service=derivation_service,
    )


def _fake_llm(content: str | None = None) -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    payload = content or json.dumps({"proof_chain": ["location(user, office)"]})
    llm.chat = AsyncMock(
        return_value=ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=payload))
    )
    return llm


class TestValidatedStorage:
    async def test_valid_concept_is_stored_and_only_active_source_facts_are_loaded(self):
        """Witness: active + retracted + missing ids; load_facts must receive exactly the
        active fact, so passing every id through (or passing none) fails."""
        active = Fact(type=FactType.LOCATION, symbolic_repr="location.home")
        retracted = Fact(
            type=FactType.LOCATION,
            symbolic_repr="location.office",
            retracted_at=datetime.now(UTC),
        )
        missing_id = uuid.uuid4()
        derivation = _derivation(source_fact_ids=[active.id, retracted.id, missing_id])
        fact_repo = _fact_repo()
        fact_repo.get = AsyncMock(
            side_effect={active.id: active, retracted.id: retracted, missing_id: None}.get
        )
        validated = Concept(
            symbolic_repr="concept.commuter",
            natural_lang_repr="Commutes by train",
            validated=True,
        )
        concept_repo = _concept_repo()
        stored = Concept(symbolic_repr="concept.commuter", validated=True)
        concept_repo.store = AsyncMock(return_value=stored)
        service = _derivation_service(
            DerivationOutcome(status="valid", concept=validated, attempts=0)
        )
        provider = _provider(
            fact_repo=fact_repo,
            concept_repo=concept_repo,
            derivation_service=service,
        )

        result = await provider.store_concept(derivation)

        fact_repo.get.assert_has_awaits(
            [call(active.id), call(retracted.id), call(missing_id)]
        )
        service.load_facts.assert_called_once_with([active])
        service.derive_and_validate.assert_awaited_once_with(derivation)
        concept_repo.store.assert_awaited_once_with(validated)
        assert result is stored

    async def test_rejected_concept_returns_none_and_is_not_stored(self):
        """Witness: a rejected outcome with a non-None concept must still return None and
        never call store; storing the concept would violate ADR-0007."""
        fact_repo = _fact_repo()
        fact_repo.get = AsyncMock(return_value=None)
        concept_repo = _concept_repo()
        service = _derivation_service(
            DerivationOutcome(
                status="rejected",
                concept=Concept(symbolic_repr="concept.rejected"),
                rejection_reason="conflict with location.home",
                attempts=3,
            )
        )
        provider = _provider(
            fact_repo=fact_repo,
            concept_repo=concept_repo,
            derivation_service=service,
        )

        result = await provider.store_concept(_derivation())

        assert result is None
        service.load_facts.assert_called_once_with([])
        service.derive_and_validate.assert_awaited_once()
        concept_repo.store.assert_not_awaited()

    async def test_valid_concept_without_a_concept_repository_is_returned_unstored(self):
        """Witness: no repository still returns the validated concept (not None), so a
        storage-less composition does not silently drop valid derivations."""
        fact_repo = _fact_repo()
        fact_repo.get = AsyncMock(return_value=None)
        validated = Concept(symbolic_repr="concept.commuter", validated=True)
        service = _derivation_service(
            DerivationOutcome(status="valid", concept=validated, attempts=0)
        )
        provider = _provider(
            fact_repo=fact_repo,
            concept_repo=None,
            derivation_service=service,
        )

        result = await provider.store_concept(_derivation())

        assert result is validated
        assert result.validated is True


class TestLegacyStorage:
    async def test_no_derivation_service_stores_the_legacy_concept_unvalidated(self):
        """Witness: build_concept's old path must keep working: the concept is stored and
        returned even though validated=False, and no fact lookup happens."""
        concept = Concept(symbolic_repr="concept.legacy", validated=False)
        derivation = _derivation(concept=concept)
        fact_repo = _fact_repo()
        concept_repo = _concept_repo()
        stored = Concept(symbolic_repr="concept.legacy", validated=False)
        concept_repo.store = AsyncMock(return_value=stored)
        provider = _provider(
            fact_repo=fact_repo,
            concept_repo=concept_repo,
            derivation_service=None,
        )

        result = await provider.store_concept(derivation)

        fact_repo.get.assert_not_awaited()
        concept_repo.store.assert_awaited_once_with(concept)
        assert result is stored

    async def test_no_derivation_service_and_no_repository_returns_the_concept(self):
        """Witness: the legacy path returns the unvalidated concept without storage."""
        concept = Concept(symbolic_repr="concept.legacy")
        provider = _provider(
            fact_repo=_fact_repo(),
            concept_repo=None,
            derivation_service=None,
        )

        result = await provider.store_concept(_derivation(concept=concept))

        assert result is concept


class TestRealValidationPipeline:
    async def test_real_pipeline_stores_a_validated_concept(self):
        """Witness: LogicEngine + ConceptValidationService + ConceptDerivationService with
        a fake LLM accept location(user, home) against fact location.home and store
        validated=True; a bypassed pipeline would store validated=False."""
        fact = Fact(type=FactType.LOCATION, symbolic_repr="location.home")
        derivation = _derivation(
            concept=Concept(symbolic_repr="concept.home"),
            proof_chain=["location(user, home)"],
            source_fact_ids=[fact.id],
        )
        fact_repo = _fact_repo()
        fact_repo.get = AsyncMock(return_value=fact)
        concept_repo = _concept_repo()
        service = ConceptDerivationService(
            ConceptValidationService(LogicEngine()), _fake_llm()
        )
        provider = _provider(
            fact_repo=fact_repo,
            concept_repo=concept_repo,
            derivation_service=service,
        )

        result = await provider.store_concept(derivation)

        assert result is not None
        assert result.validated is True
        assert result.symbolic_repr == "concept.home"
        stored = concept_repo.store.await_args.args[0]
        assert stored.validated is True

    async def test_real_pipeline_does_not_store_a_conflicting_concept(self):
        """Witness: proof location(user, office) conflicts with fact location.home; after
        three revisions the provider returns None and never stores."""
        fact = Fact(type=FactType.LOCATION, symbolic_repr="location.home")
        derivation = _derivation(
            concept=Concept(symbolic_repr="concept.office"),
            proof_chain=["location(user, office)"],
            source_fact_ids=[fact.id],
        )
        fact_repo = _fact_repo()
        fact_repo.get = AsyncMock(return_value=fact)
        concept_repo = _concept_repo()
        llm = _fake_llm()
        service = ConceptDerivationService(ConceptValidationService(LogicEngine()), llm)
        provider = _provider(
            fact_repo=fact_repo,
            concept_repo=concept_repo,
            derivation_service=service,
        )

        result = await provider.store_concept(derivation)

        assert result is None
        assert llm.chat.await_count == 3
        concept_repo.store.assert_not_awaited()

