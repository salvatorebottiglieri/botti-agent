"""Tests for concept lifecycle event emission in ``ConceptDerivationService``."""

from __future__ import annotations

import json
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

from cortex.events.base import BaseEvent
from cortex.events.bus import EventBus
from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.memory.models import Concept, ConceptDerivation
from cortex.validation.derivation_service import ConceptDerivationService
from cortex.validation.logic_engine import LogicEngine
from cortex.validation.models import ValidationResult
from cortex.validation.service import ConceptValidationService

_VALIDATED_PAYLOAD_KEYS = {
    "concept_id",
    "symbolic_repr",
    "natural_lang_repr",
    "confidence",
    "source_facts",
    "attempts",
    "timestamp",
}
_REJECTED_PAYLOAD_KEYS = {
    "concept_id",
    "symbolic_repr",
    "rejection_reason",
    "source_facts",
    "attempts",
    "timestamp",
}


class ScriptedValidationService(ConceptValidationService):
    """A validation service that returns pre-scripted results in order."""

    def __init__(self, results: list[ValidationResult]) -> None:
        super().__init__(LogicEngine())
        self._results = list(results)
        self.seen: list[ConceptDerivation] = []

    def validate(self, concept_derivation: ConceptDerivation) -> ValidationResult:
        self.seen.append(concept_derivation)
        return self._results.pop(0)


def _chat_result(content: str) -> ChatResult:
    return ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=content))


def _revision(proof_chain: list[str], **fields: object) -> str:
    return json.dumps({"proof_chain": proof_chain, **fields})


def _llm_returning(*contents: str) -> MagicMock:
    llm = MagicMock(spec=LLMClient)
    llm.chat = AsyncMock(side_effect=[_chat_result(content) for content in contents])
    return llm


def _derivation(
    *,
    concept: Concept | None = None,
    proof_chain: list[str] | None = None,
    source_fact_ids: list[uuid.UUID] | None = None,
) -> ConceptDerivation:
    return ConceptDerivation(
        concept=concept or Concept(),
        proof_chain=proof_chain if proof_chain is not None else ["derived(user, x)"],
        source_fact_ids=source_fact_ids or [],
    )


def _recording_bus() -> tuple[MagicMock, list[BaseEvent]]:
    events: list[BaseEvent] = []
    bus = MagicMock(spec=EventBus)
    bus.publish = AsyncMock(side_effect=events.append)
    return bus, events


class TestValidatedEvent:
    async def test_first_try_valid_emits_one_validated_event_with_the_full_payload(self):
        """Witness: attempts=0 and the exact payload key set pin the first-try branch;
        a loop-only emission would emit nothing here."""
        source_fact_ids = [uuid.uuid4(), uuid.uuid4()]
        concept = Concept(
            symbolic_repr="concept.commuter",
            natural_lang_repr="Commutes by train",
            confidence=0.7,
        )
        derivation = _derivation(concept=concept, source_fact_ids=source_fact_ids)
        validator = ScriptedValidationService([ValidationResult(valid=True)])
        bus, events = _recording_bus()
        service = ConceptDerivationService(validator, _llm_returning(), event_bus=bus)

        before = time.time()
        outcome = await service.derive_and_validate(derivation)
        after = time.time()

        assert outcome.status == "valid"
        assert len(events) == 1
        event = events[0]
        assert event.type == "concept.validated"
        assert event.metadata.source_module == "validation"
        assert set(event.payload) == _VALIDATED_PAYLOAD_KEYS
        assert event.payload["concept_id"] == str(concept.id)
        assert event.payload["symbolic_repr"] == "concept.commuter"
        assert event.payload["natural_lang_repr"] == "Commutes by train"
        assert event.payload["confidence"] == 0.7
        assert event.payload["source_facts"] == [str(fact_id) for fact_id in source_fact_ids]
        assert event.payload["attempts"] == 0
        assert isinstance(event.payload["timestamp"], float)
        assert before <= event.payload["timestamp"] <= after
        bus.publish.assert_awaited_once()

    async def test_revised_valid_emits_one_validated_event_for_the_revision(self):
        """Witness: symbolic_repr "concept.remote_worker" and attempts=1 prove the event
        carries the revision, not the rejected original ("concept.office_worker")."""
        source_fact_id = uuid.uuid4()
        original = Concept(
            symbolic_repr="concept.office_worker",
            natural_lang_repr="Works from the office",
            confidence=0.4,
        )
        derivation = _derivation(concept=original, source_fact_ids=[source_fact_id])
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="use home"), ValidationResult(valid=True)]
        )
        llm = _llm_returning(
            _revision(
                ["location(user, home)"],
                symbolic_repr="concept.remote_worker",
                natural_lang_repr="Works from home",
                confidence=0.9,
            )
        )
        bus, events = _recording_bus()
        service = ConceptDerivationService(validator, llm, event_bus=bus)

        outcome = await service.derive_and_validate(derivation)

        assert outcome.status == "valid"
        assert len(events) == 1
        event = events[0]
        assert event.type == "concept.validated"
        assert event.payload["concept_id"] == str(original.id)
        assert event.payload["symbolic_repr"] == "concept.remote_worker"
        assert event.payload["natural_lang_repr"] == "Works from home"
        assert event.payload["confidence"] == 0.9
        assert event.payload["source_facts"] == [str(source_fact_id)]
        assert event.payload["attempts"] == 1
        assert [e.type for e in events] == ["concept.validated"]


class TestRejectedEvent:
    async def test_rejection_after_the_budget_emits_one_rejected_event(self):
        """Witness: symbolic_repr "concept.v3" and attempts=3 identify the last revision as
        the event subject; the original is "concept.v0" and a pre-loop emission would
        carry attempts=0."""
        source_fact_id = uuid.uuid4()
        original = Concept(
            symbolic_repr="concept.v0",
            natural_lang_repr="Version zero",
            confidence=0.5,
        )
        derivation = _derivation(concept=original, source_fact_ids=[source_fact_id])
        validator = ScriptedValidationService(
            [
                ValidationResult(valid=False, suggested_fix=f"fix {index}")
                for index in range(4)
            ]
        )
        revisions = [
            _revision(
                [f"location(user, place{index})"],
                symbolic_repr=f"concept.v{index}",
                natural_lang_repr=f"Version {index}",
            )
            for index in (1, 2, 3)
        ]
        bus, events = _recording_bus()
        service = ConceptDerivationService(
            validator, _llm_returning(*revisions), event_bus=bus
        )

        before = time.time()
        outcome = await service.derive_and_validate(derivation, max_retries=3)
        after = time.time()

        assert outcome.status == "rejected"
        assert outcome.attempts == 3
        assert len(events) == 1
        event = events[0]
        assert event.type == "concept.rejected"
        assert event.metadata.source_module == "validation"
        assert set(event.payload) == _REJECTED_PAYLOAD_KEYS
        assert event.payload["concept_id"] == str(original.id)
        assert event.payload["symbolic_repr"] == "concept.v3"
        assert event.payload["rejection_reason"] == outcome.rejection_reason
        assert event.payload["source_facts"] == [str(source_fact_id)]
        assert event.payload["attempts"] == 3
        assert isinstance(event.payload["timestamp"], float)
        assert before <= event.payload["timestamp"] <= after
        assert [e.type for e in events] == ["concept.rejected"]

    async def test_zero_retries_emits_rejected_for_the_original_concept(self):
        """Witness: attempts=0 and symbolic_repr "concept.original" with no LLM call show
        the rejected event is emitted even when the revision budget is empty."""
        concept = Concept(symbolic_repr="concept.original", natural_lang_repr="Original")
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="do not invent")]
        )
        bus, events = _recording_bus()
        service = ConceptDerivationService(validator, _llm_returning(), event_bus=bus)

        outcome = await service.derive_and_validate(_derivation(concept=concept), max_retries=0)

        assert outcome.status == "rejected"
        assert len(events) == 1
        assert events[0].type == "concept.rejected"
        assert events[0].payload["symbolic_repr"] == "concept.original"
        assert events[0].payload["attempts"] == 0


class TestBestEffortEmission:
    async def test_no_event_bus_is_a_silent_no_op(self):
        """Witness: event_bus=None still returns a validated concept and raises nothing;
        an unconditional publish would crash on ``None.publish``."""
        concept = Concept(symbolic_repr="concept.commuter")
        validator = ScriptedValidationService([ValidationResult(valid=True)])
        service = ConceptDerivationService(validator, _llm_returning())

        outcome = await service.derive_and_validate(_derivation(concept=concept))

        assert outcome.status == "valid"
        assert outcome.concept is not None
        assert outcome.concept.validated is True

    async def test_publish_failure_does_not_change_the_valid_outcome(self):
        """Witness: the outcome stays valid with attempts=0 and no exception escapes even
        though publish raises; emission must not be transactional with validation."""
        concept = Concept(symbolic_repr="concept.commuter")
        validator = ScriptedValidationService([ValidationResult(valid=True)])
        bus = MagicMock(spec=EventBus)
        bus.publish = AsyncMock(side_effect=RuntimeError("event bus down"))
        service = ConceptDerivationService(validator, _llm_returning(), event_bus=bus)

        outcome = await service.derive_and_validate(_derivation(concept=concept))

        assert outcome.status == "valid"
        assert outcome.concept is not None
        assert outcome.concept.validated is True
        assert outcome.attempts == 0
        bus.publish.assert_awaited_once()


class TestRealEventBus:
    async def test_started_bus_delivers_the_validated_event_to_a_subscriber(self):
        """Witness: a real started EventBus delivers exactly one concept.validated event;
        a mock-only implementation would never reach a subscriber."""
        concept = Concept(symbolic_repr="concept.commuter")
        validator = ScriptedValidationService([ValidationResult(valid=True)])
        bus = EventBus()
        received: list[BaseEvent] = []

        async def handler(event: BaseEvent) -> None:
            received.append(event)

        await bus.start()
        await bus.subscribe("concept.validated", handler)
        try:
            service = ConceptDerivationService(
                validator, _llm_returning(), event_bus=bus
            )
            outcome = await service.derive_and_validate(_derivation(concept=concept))
        finally:
            await bus.stop()

        assert outcome.status == "valid"
        assert len(received) == 1
        assert received[0].type == "concept.validated"
        assert received[0].payload["concept_id"] == str(concept.id)
        assert received[0].payload["attempts"] == 0

    async def test_started_bus_delivers_the_rejected_event_to_a_subscriber(self):
        """Witness: a real started EventBus delivers exactly one concept.rejected event
        with attempts=0 and the original symbolic repr."""
        concept = Concept(symbolic_repr="concept.original")
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise")]
        )
        bus = EventBus()
        received: list[BaseEvent] = []

        async def handler(event: BaseEvent) -> None:
            received.append(event)

        await bus.start()
        await bus.subscribe("concept.rejected", handler)
        try:
            service = ConceptDerivationService(
                validator, _llm_returning(), event_bus=bus
            )
            outcome = await service.derive_and_validate(
                _derivation(concept=concept), max_retries=0
            )
        finally:
            await bus.stop()

        assert outcome.status == "rejected"
        assert len(received) == 1
        assert received[0].type == "concept.rejected"
        assert received[0].payload["symbolic_repr"] == "concept.original"
        assert received[0].payload["attempts"] == 0
