"""Fact extraction from raw events and free text."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from cortex.events.base import BaseEvent
from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, Role
from cortex.memory.interfaces import FactExtractor as FactExtractorABC
from cortex.memory.models import Fact, FactMutability, FactType

_SYSTEM_PROMPT = (
    "You are a fact extraction engine. Extract structured facts from the given text.\n"
    "Valid fact types: user_preference, user_fact, location, time, activity, calendar, "
    "weather, device_status, entity, relationship, custom.\n"
    'Respond with JSON of the shape {"facts": [...]} where each item has the fields: '
    "type, symbolic_repr, natural_lang_repr, confidence, payload.\n"
    'Return {"facts": []} if no facts; no markdown.'
)


def _slugify(text: str) -> str:
    """Convert text to a slug for symbolic representation."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _parse_llm_facts(content: str) -> list[Fact]:
    """Parse LLM JSON output into structured Fact objects.

    Accepts either ``{"facts": [...]}`` or a bare ``[...]``. Raises
    ``ValueError`` on malformed JSON.
    """
    text = content.strip()
    if text.startswith("```"):
        # Strip markdown fenced code blocks (```json ... ``` or ``` ... ```)
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed JSON from LLM: {e}") from e

    if isinstance(data, dict):
        items = data.get("facts")
        if items is None:
            raise ValueError("LLM JSON must contain a 'facts' key")
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("LLM JSON must be an object with a 'facts' key or a bare list")

    if not isinstance(items, list):
        raise ValueError("LLM JSON 'facts' value must be a list")

    facts: list[Fact] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"Each fact item must be an object, got {type(item).__name__}")
        try:
            fact_type = FactType(item["type"]) if item.get("type") else FactType.CUSTOM
        except ValueError:
            # Unknown type string from the LLM falls back to CUSTOM
            fact_type = FactType.CUSTOM
        try:
            confidence = float(item.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        raw_payload = item.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        facts.append(
            Fact(
                type=fact_type,
                symbolic_repr=item.get("symbolic_repr") or "",
                natural_lang_repr=item.get("natural_lang_repr") or "",
                confidence=confidence,
                payload=payload,
            )
        )
    return facts


class FactExtractor(FactExtractorABC):
    """
    Extract structured facts from raw events and free text.

    Event extraction is rule-based; text extraction is LLM-powered.
    No storage and no context bundling — callers own the resulting facts.
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client

    # ─── Event extraction (rules) ────────────────────────────────────────

    def extract_from_event(self, event: BaseEvent) -> list[Fact]:
        """Extract facts from an event by dispatching on its type."""
        return self.extract_from_event_type(event.type, event.payload)

    def extract_from_event_type(self, event_type: str, payload: dict[str, Any]) -> list[Fact]:
        """Extract facts from a raw event type and payload.

        Unknown event types yield no facts.
        """
        handler = _EVENT_BUILDERS.get(event_type)
        return handler(self, payload) if handler else []

    def _facts_from_location(self, payload: dict[str, Any]) -> list[Fact]:
        lat = payload.get("latitude")
        lon = payload.get("longitude")
        place = payload.get("place") or (
            f"{lat},{lon}" if lat is not None and lon is not None else None
        )
        return [
            Fact(
                type=FactType.LOCATION,
                mutability=FactMutability.MUTABLE,
                symbolic_repr=f"location.{place or 'current'}",
                natural_lang_repr=f"At {place or 'unknown location'}",
                payload={
                    "latitude": lat,
                    "longitude": lon,
                    "place": place,
                },
                confidence=0.9 if place else 0.6,
            )
        ]

    def _facts_from_payment(self, payload: dict[str, Any]) -> list[Fact]:
        merchant = payload.get("merchant_name") or payload.get("merchant")
        return [
            Fact(
                type=FactType.PAYMENT,
                mutability=FactMutability.MUTABLE,
                symbolic_repr=f"payment.{merchant or 'unknown'}",
                natural_lang_repr=(
                    f"Paid {payload.get('amount')} {payload.get('currency')} at {merchant or 'unknown'}"
                ),
                payload=dict(payload),
                confidence=0.9,
            )
        ]

    def _facts_from_activity(self, payload: dict[str, Any]) -> list[Fact]:
        activity = payload.get("activity")
        duration = payload.get("duration_seconds", payload.get("duration"))
        return [
            Fact(
                type=FactType.ACTIVITY,
                mutability=FactMutability.EPHEMERAL,
                symbolic_repr=f"activity.{activity or 'unknown'}",
                natural_lang_repr=f"Currently {activity or 'doing something'}",
                payload={"duration": duration},
                confidence=0.8,
            )
        ]

    def _facts_from_calendar(self, payload: dict[str, Any]) -> list[Fact]:
        title = payload.get("title")
        start = payload.get("start_time")
        return [
            Fact(
                type=FactType.CALENDAR,
                mutability=FactMutability.MUTABLE,
                symbolic_repr=f"calendar.{_slugify(title or 'event')}",
                natural_lang_repr=f"Event: {title or 'Unknown event'}",
                payload={"start_time": start, **payload},
                confidence=0.9,
            )
        ]

    def _facts_from_call_log(self, payload: dict[str, Any]) -> list[Fact]:
        direction = payload.get("direction")
        contact = payload.get("contact_name") or payload.get("contact")
        return [
            Fact(
                type=FactType.CALL_LOG,
                mutability=FactMutability.MUTABLE,
                symbolic_repr=f"call_log.{direction or 'unknown'}",
                natural_lang_repr=f"{direction or 'unknown'} call with {contact or 'unknown'}",
                payload=dict(payload),
                confidence=0.9,
            )
        ]

    def _facts_from_app_usage(self, payload: dict[str, Any]) -> list[Fact]:
        app = payload.get("app_name") or payload.get("app_id")
        return [
            Fact(
                type=FactType.APP_USAGE,
                mutability=FactMutability.EPHEMERAL,
                symbolic_repr=f"app_usage.{app or 'unknown'}",
                natural_lang_repr=f"Used {app or 'unknown'}",
                payload=dict(payload),
                confidence=0.9,
            )
        ]

    # ─── Text extraction (LLM) ───────────────────────────────────────────

    async def extract_from_text(self, text: str) -> list[Fact]:
        """Extract facts from free text via the LLM.

        Raises ``ValueError`` when no LLM client is configured or the LLM
        returns malformed JSON.
        """
        if self._llm_client is None:
            raise ValueError("FactExtractor requires an llm_client for extract_from_text")

        messages = [
            ChatMessage(role=Role.SYSTEM, content=_SYSTEM_PROMPT),
            ChatMessage(role=Role.USER, content=text),
        ]
        result = await self._llm_client.chat(messages)
        content = result.message.content
        if content is None:
            raise ValueError("LLM returned no content for fact extraction")
        return _parse_llm_facts(content)


# Module-level builders table: single source of truth for the sensory event
# vocabulary FactExtractor dispatches on (also exported as SUPPORTED_EVENT_TYPES).
_EVENT_BUILDERS: dict[str, Callable[[FactExtractor, dict[str, Any]], list[Fact]]] = {
    "location": FactExtractor._facts_from_location,
    "payment": FactExtractor._facts_from_payment,
    "activity": FactExtractor._facts_from_activity,
    "calendar": FactExtractor._facts_from_calendar,
    "call_log": FactExtractor._facts_from_call_log,
    "app_usage": FactExtractor._facts_from_app_usage,
}
SUPPORTED_EVENT_TYPES = frozenset(_EVENT_BUILDERS)
