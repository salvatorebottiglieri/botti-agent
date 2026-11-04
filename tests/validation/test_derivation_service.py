"""Tests for the LLM revision loop in ``ConceptDerivationService``."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.memory.models import Concept, ConceptDerivation, Fact, FactType
from cortex.validation.derivation_service import ConceptDerivationService
from cortex.validation.logic_engine import LogicEngine
from cortex.validation.models import DerivationOutcome, ValidationResult
from cortex.validation.service import ConceptValidationService


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


def _revision(proof_chain: list[object], **fields: object) -> str:
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


def _prompt_of(llm: MagicMock) -> str:
    messages: list[ChatMessage] = llm.chat.await_args.args[0]
    return "\n".join(message.content or "" for message in messages)


class TestValidFirstTry:
    async def test_valid_first_try_returns_validated_concept_without_llm_call(self):
        """Witness: attempts=0 and no chat call distinguish one validation from a revision loop."""
        source_fact_id = uuid.uuid4()
        concept = Concept(
            symbolic_repr="concept.commuter",
            natural_lang_repr="Commutes by train",
        )
        derivation = _derivation(
            concept=concept,
            proof_chain=["commutes(user, train)"],
            source_fact_ids=[source_fact_id],
        )
        validator = ScriptedValidationService([ValidationResult(valid=True)])
        llm = _llm_returning()
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(derivation)

        assert outcome.status == "valid"
        assert outcome.attempts == 0
        assert outcome.rejection_reason is None
        assert outcome.concept is not None
        assert outcome.concept.validated is True
        assert outcome.concept.id == concept.id
        assert outcome.concept.created_at == concept.created_at
        assert validator.seen == [derivation]
        llm.chat.assert_not_awaited()


class TestRealValidator:
    async def test_valid_derivation_against_real_engine_is_returned_validated(self):
        """Witness: the real LogicEngine accepts location(user, home) against location.home
        with no LLM call, so the service is wired to the #27 validator, not a stub."""
        fact = Fact(type=FactType.LOCATION, symbolic_repr="location.home")
        engine = LogicEngine()
        engine.load_facts([fact])
        llm = _llm_returning()
        service = ConceptDerivationService(ConceptValidationService(engine), llm)
        derivation = _derivation(proof_chain=["location(user, home)"])

        outcome = await service.derive_and_validate(derivation)

        assert outcome.status == "valid"
        assert outcome.attempts == 0
        assert outcome.concept is not None
        assert outcome.concept.validated is True
        llm.chat.assert_not_awaited()


class TestRevisionLoop:
    async def test_rejection_prompt_carries_conflicts_and_fix_and_revision_is_validated(self):
        """Witness: natural_lang_repr "User is at home" (not symbolic "location.home") and
        the "Use the home location" fix must reach the prompt; attempts=1 pins the count."""
        conflicting_fact = Fact(
            symbolic_repr="location.home",
            natural_lang_repr="User is at home",
        )
        original = Concept(
            symbolic_repr="concept.office_worker",
            natural_lang_repr="Works from the office",
            confidence=0.4,
        )
        derivation = _derivation(
            concept=original,
            proof_chain=["location(user, office)"],
            source_fact_ids=[uuid.uuid4()],
        )
        validator = ScriptedValidationService(
            [
                ValidationResult(
                    valid=False,
                    conflicting_facts=[conflicting_fact],
                    suggested_fix="Use the home location",
                ),
                ValidationResult(valid=True),
            ]
        )
        llm = _llm_returning(
            _revision(
                ["location(user, home)", "commutes(user, home)"],
                symbolic_repr="concept.remote_worker",
                natural_lang_repr="Works from home",
                confidence=0.9,
            )
        )
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(derivation, max_retries=3)

        assert outcome.status == "valid"
        assert outcome.attempts == 1
        assert outcome.concept is not None
        assert outcome.concept.validated is True
        assert outcome.concept.symbolic_repr == "concept.remote_worker"
        assert outcome.concept.natural_lang_repr == "Works from home"
        assert outcome.concept.confidence == 0.9
        assert outcome.concept.proof_chain == "location(user, home)\ncommutes(user, home)"
        assert validator.seen[1].proof_chain == ["location(user, home)", "commutes(user, home)"]

        llm.chat.assert_awaited_once()
        prompt = _prompt_of(llm)
        assert "Your proposed concept was rejected by the logic validator." in prompt
        assert "User is at home" in prompt
        assert "Use the home location" in prompt
        assert "Please propose a revised concept that is consistent with the facts." in prompt
        assert "location(user, office)" in prompt

    async def test_retry_prompts_carry_the_previous_revision_and_its_rejection(self):
        """Witness: prompt 2 carries fix 1 and revision v1's symbolic repr, not v0's; reusing
        proposed_derivation shows v0 and reusing the initial result shows fix 0."""
        original = Concept(
            symbolic_repr="concept.v0",
            natural_lang_repr="Version zero",
        )
        derivation = _derivation(concept=original)
        revisions = [
            _revision(
                [f"location(user, place{index})"],
                symbolic_repr=f"concept.v{index}",
                natural_lang_repr=f"Version {index}",
            )
            for index in (1, 2, 3)
        ]
        validator = ScriptedValidationService(
            [
                ValidationResult(valid=False, suggested_fix="fix 0"),
                ValidationResult(valid=False, suggested_fix="fix 1"),
                ValidationResult(valid=False, suggested_fix="fix 2"),
                ValidationResult(valid=True),
            ]
        )
        llm = _llm_returning(*revisions)
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(derivation)

        assert outcome.status == "valid"
        assert outcome.attempts == 3
        assert llm.chat.await_count == 3

        prompts = [
            "\n".join(message.content or "" for message in call.args[0])
            for call in llm.chat.await_args_list
        ]
        assert "Suggested fix: fix 0" in prompts[0]
        assert "concept.v0" in prompts[0]
        assert "Suggested fix: fix 1" in prompts[1]
        assert "concept.v1" in prompts[1]
        assert "concept.v0" not in prompts[1]
        assert "Suggested fix: fix 2" in prompts[2]
        assert "concept.v2" in prompts[2]

    async def test_optional_fields_fall_back_and_metadata_is_preserved(self):
        """Witness: a markdown-fenced payload is parsed, and confidence 0.42 (not the 0.5
        default), two source ids and a two-clause chain distinguish fallback-and-join from
        resetting metadata."""
        concept_id = uuid.uuid4()
        created_at = datetime(2025, 1, 2, 3, 4, tzinfo=UTC)
        source_fact_ids = [uuid.uuid4(), uuid.uuid4()]
        original = Concept(
            id=concept_id,
            symbolic_repr="concept.original",
            natural_lang_repr="Original concept",
            derivation_method="llm_inference",
            proof_chain="old clause",
            payload={"origin": "test"},
            confidence=0.42,
            source_facts=list(source_fact_ids),
            created_at=created_at,
        )
        derivation = _derivation(
            concept=original,
            proof_chain=["old(user, x)"],
            source_fact_ids=source_fact_ids,
        )
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise"), ValidationResult(valid=True)]
        )
        payload = _revision(["new(user, x)", "done(user, x)"])
        llm = _llm_returning(f"```json\n{payload}\n```")
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(derivation)

        assert outcome.concept is not None
        revised = outcome.concept
        assert revised.id == concept_id
        assert revised.created_at == created_at
        assert revised.symbolic_repr == "concept.original"
        assert revised.natural_lang_repr == "Original concept"
        assert revised.confidence == 0.42
        assert revised.derivation_method == "llm_inference"
        assert revised.payload == {"origin": "test"}
        assert revised.source_facts == source_fact_ids
        assert revised.proof_chain == "new(user, x)\ndone(user, x)"
        assert revised.validated is True
        assert validator.seen[1].source_fact_ids == source_fact_ids

    async def test_revised_concept_source_facts_come_from_the_derivation(self):
        """Witness: concept.source_facts=[A] but derivation ids [B]; the revised concept carries
        [B], the fact set validation used, not [A]."""
        concept_fact_id = uuid.uuid4()
        derivation_fact_id = uuid.uuid4()
        original = Concept(
            symbolic_repr="concept.original",
            source_facts=[concept_fact_id],
        )
        derivation = _derivation(
            concept=original,
            source_fact_ids=[derivation_fact_id],
        )
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise"), ValidationResult(valid=True)]
        )
        llm = _llm_returning(_revision(["new(user, x)"]))
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(derivation)

        assert outcome.concept is not None
        assert outcome.concept.source_facts == [derivation_fact_id]
        assert validator.seen[1].source_fact_ids == [derivation_fact_id]


class TestOptionalFieldFallbacks:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(1.7, 1.0), (-0.3, 0.0)],
        ids=["above-one", "below-zero"],
    )
    async def test_confidence_is_clamped_to_unit_interval(
        self, raw: float, expected: float
    ):
        """Witness: 1.7 becomes 1.0 and -0.3 becomes 0.0; passthrough would keep the raw value."""
        original = Concept(symbolic_repr="concept.original", confidence=0.42)
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise"), ValidationResult(valid=True)]
        )
        llm = _llm_returning(_revision(["new(user, x)"], confidence=raw))
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(concept=original))

        assert outcome.concept is not None
        assert outcome.concept.confidence == expected

    async def test_non_finite_confidence_falls_back_to_the_original(self):
        """Witness: NaN falls back to 0.42; float() passthrough or clamping alone keeps NaN."""
        original = Concept(symbolic_repr="concept.original", confidence=0.42)
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise"), ValidationResult(valid=True)]
        )
        llm = _llm_returning(_revision(["new(user, x)"], confidence=float("nan")))
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(concept=original))

        assert outcome.concept is not None
        assert outcome.concept.confidence == 0.42

    async def test_empty_optional_strings_fall_back_to_the_original(self):
        """Witness: "" is missing, so both strings stay the original; passthrough would blank them."""
        original = Concept(
            symbolic_repr="concept.original",
            natural_lang_repr="Original concept",
        )
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise"), ValidationResult(valid=True)]
        )
        llm = _llm_returning(
            _revision(["new(user, x)"], symbolic_repr="", natural_lang_repr="")
        )
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(concept=original))

        assert outcome.concept is not None
        assert outcome.concept.symbolic_repr == "concept.original"
        assert outcome.concept.natural_lang_repr == "Original concept"


class TestPromptContract:
    async def test_system_message_pins_strict_json_and_proof_chain(self):
        """Witness: the system turn names strict JSON and proof_chain; a prompt without the
        contract cannot produce a parseable revision."""
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise"), ValidationResult(valid=True)]
        )
        llm = _llm_returning(_revision(["new(user, x)"]))
        service = ConceptDerivationService(validator, llm)

        await service.derive_and_validate(_derivation())

        messages: list[ChatMessage] = llm.chat.await_args.args[0]
        assert messages[0].role == Role.SYSTEM
        system = messages[0].content or ""
        assert "strict JSON" in system
        assert "proof_chain" in system


class TestExhaustion:
    async def test_always_invalid_rejects_after_exactly_max_retries_llm_calls(self):
        """Witness: max_retries=3 yields 4 validations (1 initial + 3 revisions), not 3;
        the reason carries the last fix ("fix 3"), not the initial one ("fix 0")."""
        conflicting_fact = Fact(natural_lang_repr="User is at home")
        results = [
            ValidationResult(
                valid=False,
                conflicting_facts=[conflicting_fact],
                suggested_fix=f"fix {index}",
            )
            for index in range(4)
        ]
        validator = ScriptedValidationService(results)
        llm = _llm_returning(
            *[_revision([f"location(user, place{index})"]) for index in range(3)]
        )
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(), max_retries=3)

        assert outcome.status == "rejected"
        assert outcome.concept is None
        assert outcome.attempts == 3
        assert outcome.rejection_reason is not None
        assert "3" in outcome.rejection_reason
        assert "fix 3" in outcome.rejection_reason
        assert "User is at home" in outcome.rejection_reason
        llm.chat.assert_awaited()
        assert llm.chat.await_count == 3
        assert len(validator.seen) == 4

    async def test_default_max_retries_is_three(self):
        """Witness: no max_retries argument yields three LLM calls; a default of 2 yields two."""
        results = [
            ValidationResult(valid=False, suggested_fix=f"fix {index}")
            for index in range(4)
        ]
        validator = ScriptedValidationService(results)
        llm = _llm_returning(
            *[_revision([f"location(user, place{index})"]) for index in range(3)]
        )
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation())

        assert outcome.status == "rejected"
        assert outcome.concept is None
        assert outcome.attempts == 3
        assert outcome.rejection_reason is not None
        assert "after 3 revision attempt(s)" in outcome.rejection_reason
        assert llm.chat.await_count == 3
        assert len(validator.seen) == 4

    async def test_zero_retries_validates_once_and_never_calls_llm(self):
        """Witness: max_retries=0 rejects with attempts=0 and no chat call, not the default 3."""
        conflicting_fact = Fact(natural_lang_repr="User is at home")
        validator = ScriptedValidationService(
            [
                ValidationResult(
                    valid=False,
                    conflicting_facts=[conflicting_fact],
                    suggested_fix="do not invent a location",
                )
            ]
        )
        llm = _llm_returning()
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(), max_retries=0)

        assert outcome.status == "rejected"
        assert outcome.concept is None
        assert outcome.attempts == 0
        assert len(validator.seen) == 1
        assert outcome.rejection_reason is not None
        assert "do not invent a location" in outcome.rejection_reason
        assert "User is at home" in outcome.rejection_reason
        llm.chat.assert_not_awaited()

    async def test_negative_max_retries_raises_before_any_work(self):
        """Witness: -1 raises ValueError and touches neither the validator nor the LLM."""
        validator = ScriptedValidationService([])
        llm = _llm_returning()
        service = ConceptDerivationService(validator, llm)

        with pytest.raises(ValueError, match="max_retries"):
            await service.derive_and_validate(_derivation(), max_retries=-1)

        assert validator.seen == []
        llm.chat.assert_not_awaited()


class TestMalformedRevisions:
    @pytest.mark.parametrize(
        "content",
        [
            "not json at all",
            json.dumps({"symbolic_repr": "concept.unproven"}),
            json.dumps({"proof_chain": []}),
            json.dumps({"proof_chain": [1, 2]}),
        ],
        ids=["non-json", "missing-proof-chain", "empty-proof-chain", "non-string-clauses"],
    )
    async def test_malformed_revision_is_an_attempt_not_a_crash(self, content: str):
        """Witness: a malformed payload is never validated (seen stays 1); a naive parser
        would either crash (missing key) or validate an empty chain (seen becomes 2)."""
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise the proof")]
        )
        llm = _llm_returning(content)
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(), max_retries=1)

        assert outcome.status == "rejected"
        assert outcome.attempts == 1
        assert outcome.concept is None
        assert outcome.rejection_reason is not None
        assert "revision" in outcome.rejection_reason.lower()
        assert "revise the proof" in outcome.rejection_reason
        assert len(validator.seen) == 1
        llm.chat.assert_awaited_once()

    async def test_provider_exception_is_counted_and_rejected_after_budget(self):
        """Witness: two raised calls yield attempts=2 and a reason with the provider error;
        an abort-on-first-exception implementation would report attempts=1."""
        validator = ScriptedValidationService(
            [ValidationResult(valid=False, suggested_fix="revise the proof")]
        )
        llm = MagicMock(spec=LLMClient)
        llm.chat = AsyncMock(side_effect=RuntimeError("provider down"))
        service = ConceptDerivationService(validator, llm)

        outcome = await service.derive_and_validate(_derivation(), max_retries=2)

        assert outcome.status == "rejected"
        assert outcome.concept is None
        assert outcome.attempts == 2
        assert outcome.rejection_reason is not None
        assert "provider down" in outcome.rejection_reason
        assert llm.chat.await_count == 2


class TestDerivationOutcome:
    def test_defaults(self):
        outcome = DerivationOutcome(status="valid")

        assert outcome.concept is None
        assert outcome.rejection_reason is None
        assert outcome.attempts == 0
