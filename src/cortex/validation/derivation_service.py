"""LLM revision loop around the synchronous concept validator."""

from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import replace
from typing import Any
from uuid import UUID

from cortex.events.base import BaseEvent
from cortex.events.bus import EventBus
from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, Role
from cortex.memory.models import Concept, ConceptDerivation, Fact
from cortex.validation.models import DerivationOutcome, ValidationResult
from cortex.validation.service import ConceptValidationService

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You revise concept derivations rejected by a symbolic logic validator.\n"
    "Respond with strict JSON only, no markdown, in exactly this shape:\n"
    '{"symbolic_repr": "...", "natural_lang_repr": "...", "confidence": 0.0, '
    '"proof_chain": ["..."]}\n'
    "proof_chain must be a non-empty list of Datalog clause strings. "
    "symbolic_repr, natural_lang_repr and confidence are optional and fall back to "
    "the original concept."
)

_REJECTION_TEMPLATE = (
    "Your proposed concept was rejected by the logic validator.\n"
    "Conflicting facts: {conflicts}\n"
    "Suggested fix: {suggested_fix}\n"
    "Please propose a revised concept that is consistent with the facts."
)


class ConceptDerivationService:
    """Validates LLM-proposed concepts and asks the LLM to revise rejected ones."""

    def __init__(
        self,
        validation_service: ConceptValidationService,
        llm_client: LLMClient,
        event_bus: EventBus | None = None,
    ) -> None:
        self._validation_service = validation_service
        self._llm_client = llm_client
        self._event_bus = event_bus

    def load_facts(self, facts: list[Fact]) -> None:
        """Replace the validator's loaded axioms with ``facts``."""
        self._validation_service.load_facts(facts)

    async def derive_and_validate(
        self,
        proposed_derivation: ConceptDerivation,
        max_retries: int = 3,
    ) -> DerivationOutcome:
        """Validate a derivation, revising it via the LLM up to ``max_retries`` times.

        ``max_retries`` counts LLM revision calls; the initial validation is not
        an attempt. A malformed response or a provider exception counts as a
        failed attempt: it is recorded and the loop continues.
        """
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")

        result = self._validation_service.validate(proposed_derivation)
        if result.valid:
            outcome = _valid_outcome(proposed_derivation.concept, attempts=0)
            await self._emit_outcome(outcome, proposed_derivation)
            return outcome

        attempts = 0
        last_result = result
        last_error: str | None = None
        current_derivation = proposed_derivation
        for attempt in range(1, max_retries + 1):
            attempts = attempt
            messages = _revision_messages(current_derivation, last_result)
            try:
                chat_result = await self._llm_client.chat(messages)
                revised = _parse_revision(
                    chat_result.message.content, current_derivation
                )
            except Exception as exc:  # provider/transport failures must not escape
                last_error = f"revision attempt {attempt} failed: {exc}"
                continue

            current_derivation = revised
            last_error = None
            last_result = self._validation_service.validate(revised)
            if last_result.valid:
                outcome = _valid_outcome(revised.concept, attempts=attempt)
                await self._emit_outcome(outcome, revised)
                return outcome

        outcome = DerivationOutcome(
            status="rejected",
            concept=None,
            rejection_reason=_rejection_reason(last_result, attempts, last_error),
            attempts=attempts,
        )
        await self._emit_outcome(outcome, current_derivation)
        return outcome

    async def _emit_outcome(
        self,
        outcome: DerivationOutcome,
        derivation: ConceptDerivation,
    ) -> None:
        """Emit the terminal event for ``outcome``; never changes the outcome."""
        if outcome.status == "valid":
            concept = outcome.concept
            if concept is None:
                return
            await self._emit(
                "concept.validated",
                _validated_payload(concept, derivation.source_fact_ids, outcome.attempts),
            )
            return

        await self._emit(
            "concept.rejected",
            _rejected_payload(
                derivation,
                outcome.rejection_reason,
                outcome.attempts,
            ),
        )

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish one event, logging and swallowing failures (best effort)."""
        if self._event_bus is None:
            return
        try:
            await self._event_bus.publish(
                BaseEvent.create(event_type, payload, source_module="validation")
            )
        except Exception as exc:  # observational only: the outcome stands
            logger.warning(f"Failed to publish {event_type} event: {exc}")


def _validated_payload(
    concept: Concept,
    source_fact_ids: list[UUID],
    attempts: int,
) -> dict[str, Any]:
    """Build the ``concept.validated`` payload."""
    return {
        "concept_id": str(concept.id),
        "symbolic_repr": concept.symbolic_repr,
        "natural_lang_repr": concept.natural_lang_repr,
        "confidence": concept.confidence,
        "source_facts": _source_fact_strings(source_fact_ids),
        "attempts": attempts,
        "timestamp": time.time(),
    }


def _rejected_payload(
    derivation: ConceptDerivation,
    rejection_reason: str | None,
    attempts: int,
) -> dict[str, Any]:
    """Build the ``concept.rejected`` payload from the last attempted concept."""
    concept = derivation.concept
    return {
        "concept_id": str(concept.id),
        "symbolic_repr": concept.symbolic_repr,
        "rejection_reason": rejection_reason or "concept rejected",
        "source_facts": _source_fact_strings(derivation.source_fact_ids),
        "attempts": attempts,
        "timestamp": time.time(),
    }


def _source_fact_strings(source_fact_ids: list[UUID]) -> list[str]:
    """Serialise source fact ids for the JSON event payload."""
    return [str(fact_id) for fact_id in source_fact_ids]


def _valid_outcome(concept: Concept, attempts: int) -> DerivationOutcome:
    """Build the outcome for a concept the validator accepted."""
    return DerivationOutcome(
        status="valid",
        concept=replace(concept, validated=True),
        attempts=attempts,
    )


def _revision_messages(
    derivation: ConceptDerivation,
    result: ValidationResult,
) -> list[ChatMessage]:
    """Build the system contract and the rejection context for a revision."""
    context = _REJECTION_TEMPLATE.format(
        conflicts=_conflicts_text(result),
        suggested_fix=result.suggested_fix or "none",
    )
    original = derivation.concept
    proof_chain = "\n".join(f"- {clause}" for clause in derivation.proof_chain) or "- none"
    user_content = (
        f"{context}\n\n"
        "Original concept:\n"
        f"Symbolic: {original.symbolic_repr}\n"
        f"Natural language: {original.natural_lang_repr}\n"
        f"Confidence: {original.confidence}\n"
        "Original proof chain:\n"
        f"{proof_chain}"
    )
    return [
        ChatMessage(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
        ChatMessage(role=Role.USER, content=user_content),
    ]


def _parse_revision(
    content: str | None,
    original: ConceptDerivation,
) -> ConceptDerivation:
    """Parse the LLM's strict JSON revision into a new derivation.

    Raises ``ValueError`` for anything that is not an object with a non-empty
    string ``proof_chain``.
    """
    if content is None:
        raise ValueError("LLM returned no revision content")

    text = _strip_markdown_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed revision JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Revision JSON must be an object")

    raw_chain = data.get("proof_chain")
    if not isinstance(raw_chain, list) or not raw_chain:
        raise ValueError("Revision proof_chain must be a non-empty list")
    chain = [clause for clause in raw_chain if isinstance(clause, str)]
    if len(chain) != len(raw_chain):
        raise ValueError("Revision proof_chain must contain only strings")

    concept = replace(
        original.concept,
        symbolic_repr=_optional_str(
            data, "symbolic_repr", original.concept.symbolic_repr
        ),
        natural_lang_repr=_optional_str(
            data, "natural_lang_repr", original.concept.natural_lang_repr
        ),
        confidence=_optional_confidence(data, original.concept.confidence),
        proof_chain="\n".join(chain),
        source_facts=original.source_fact_ids,
        validated=False,
    )
    return ConceptDerivation(
        concept=concept,
        proof_chain=chain,
        source_fact_ids=original.source_fact_ids,
    )


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    return text


def _optional_str(data: dict[str, Any], key: str, fallback: str) -> str:
    value = data.get(key)
    if value is None or value == "":
        return fallback
    if not isinstance(value, str):
        raise ValueError(f"Revision {key} must be a string")
    return value


def _optional_confidence(data: dict[str, Any], fallback: float) -> float:
    value = data.get("confidence")
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Revision confidence must be a number")
    confidence = float(value)
    if not math.isfinite(confidence):
        return fallback
    return min(1.0, max(0.0, confidence))


def _conflicts_text(result: ValidationResult) -> str:
    if not result.conflicting_facts:
        return "none"
    return "; ".join(fact.natural_lang_repr for fact in result.conflicting_facts)


def _rejection_reason(
    result: ValidationResult,
    attempts: int,
    last_error: str | None,
) -> str:
    parts = [
        f"Concept rejected after {attempts} revision attempt(s)",
        f"conflicting facts: {_conflicts_text(result)}",
        f"suggested fix: {result.suggested_fix or 'none'}",
    ]
    if last_error is not None:
        parts.append(last_error)
    return "; ".join(parts)
