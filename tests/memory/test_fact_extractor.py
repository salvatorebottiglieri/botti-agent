"""Tests for FactExtractor."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.events.base import BaseEvent
from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.memory.fact_extractor import FactExtractor, _parse_llm_facts
from cortex.memory.models import FactType


class TestExtractFromEventType:
    """extract_from_event_type maps each event type to the correct fact."""

    @pytest.mark.parametrize(
        ("event_type", "payload", "expected_type", "expected_symbolic"),
        [
            ("location", {"place": "home"}, FactType.LOCATION, "location.home"),
            ("location", {"latitude": 37.77, "longitude": -122.41}, FactType.LOCATION, "location.37.77,-122.41"),
            ("payment", {"merchant_name": "Tesco", "amount": 12.5, "currency": "GBP"}, FactType.PAYMENT, "payment.Tesco"),
            ("activity", {"activity": "walking", "duration_seconds": 300}, FactType.ACTIVITY, "activity.walking"),
            ("calendar", {"title": "Team Sync"}, FactType.CALENDAR, "calendar.team_sync"),
            ("call_log", {"direction": "incoming", "contact_name": "Alice"}, FactType.CALL_LOG, "call_log.incoming"),
            ("app_usage", {"app_name": "WhatsApp"}, FactType.APP_USAGE, "app_usage.WhatsApp"),
        ],
    )
    def test_extract_from_event_type(self, event_type, payload, expected_type, expected_symbolic):
        """Each event type produces a fact of the right type and symbolic repr."""
        facts = FactExtractor().extract_from_event_type(event_type, payload)

        assert len(facts) == 1
        assert facts[0].type == expected_type
        assert facts[0].symbolic_repr == expected_symbolic

    def test_call_log_natural_lang_repr_uses_contact_name(self):
        """A call_log with contact_name renders the contact in natural language."""
        facts = FactExtractor().extract_from_event_type(
            "call_log", {"direction": "incoming", "contact_name": "Alice"}
        )

        assert len(facts) == 1
        assert facts[0].natural_lang_repr == "incoming call with Alice"

    def test_location_coords_only_derives_place(self):
        """A location without a place derives the symbolic repr from coordinates."""
        facts = FactExtractor().extract_from_event_type(
            "location", {"latitude": 37.77, "longitude": -122.41}
        )

        assert len(facts) == 1
        assert facts[0].symbolic_repr == "location.37.77,-122.41"
        assert facts[0].confidence == 0.9

    def test_unknown_event_type_returns_empty(self):
        """Unknown event types yield no facts."""
        assert FactExtractor().extract_from_event_type("unknown.type", {}) == []


class TestExtractFromEvent:
    """extract_from_event delegates to extract_from_event_type."""

    def test_extract_from_event_delegates(self):
        """A location BaseEvent produces a location fact."""
        event = BaseEvent.create("location", {"place": "home"})

        facts = FactExtractor().extract_from_event(event)

        assert len(facts) == 1
        assert facts[0].type == FactType.LOCATION
        assert facts[0].symbolic_repr == "location.home"


class TestExtractFromText:
    """extract_from_text uses the LLM to structure free text into facts."""

    @pytest.mark.asyncio
    async def test_extract_from_text_returns_structured_facts(self):
        """LLM JSON output is parsed into structured Fact objects."""
        llm = MagicMock(spec=LLMClient)
        llm.chat = AsyncMock(
            return_value=ChatResult(
                message=ChatMessage(
                    role=Role.ASSISTANT,
                    content=json.dumps(
                        {
                            "facts": [
                                {
                                    "type": "location",
                                    "symbolic_repr": "location.home",
                                    "natural_lang_repr": "At home",
                                    "confidence": 0.95,
                                    "payload": {"place": "home"},
                                },
                                {
                                    "type": "user_preference",
                                    "symbolic_repr": "user_preference.coffee",
                                    "natural_lang_repr": "Prefers coffee",
                                    "confidence": 0.8,
                                    "payload": {},
                                },
                            ]
                        }
                    ),
                )
            )
        )

        facts = await FactExtractor(llm_client=llm).extract_from_text("I am at home and I like coffee.")

        assert len(facts) == 2
        assert facts[0].type == FactType.LOCATION
        assert facts[0].symbolic_repr == "location.home"
        assert facts[0].natural_lang_repr == "At home"
        assert facts[0].confidence == 0.95
        assert facts[0].payload == {"place": "home"}
        assert facts[1].type == FactType.USER_PREFERENCE
        llm.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extract_from_text_accepts_bare_list(self):
        """A bare JSON list is accepted as the fact list."""
        llm = MagicMock(spec=LLMClient)
        llm.chat = AsyncMock(
            return_value=ChatResult(
                message=ChatMessage(
                    role=Role.ASSISTANT,
                    content=json.dumps([{"type": "activity", "symbolic_repr": "activity.walking"}]),
                )
            )
        )

        facts = await FactExtractor(llm_client=llm).extract_from_text("walking")

        assert len(facts) == 1
        assert facts[0].type == FactType.ACTIVITY
        assert facts[0].symbolic_repr == "activity.walking"

    @pytest.mark.asyncio
    async def test_extract_from_text_accepts_fenced_json(self):
        """Markdown-fenced JSON blocks are stripped before parsing."""
        llm = MagicMock(spec=LLMClient)
        llm.chat = AsyncMock(
            return_value=ChatResult(
                message=ChatMessage(
                    role=Role.ASSISTANT,
                    content='```json\n{"facts": [{"type": "calendar", "symbolic_repr": "calendar.meeting"}]}\n```',
                )
            )
        )

        facts = await FactExtractor(llm_client=llm).extract_from_text("meeting tomorrow")

        assert len(facts) == 1
        assert facts[0].type == FactType.CALENDAR

    @pytest.mark.asyncio
    async def test_extract_from_text_without_llm_client_raises(self):
        """Without an LLM client, extract_from_text raises ValueError."""
        with pytest.raises(ValueError):
            await FactExtractor().extract_from_text("hello")

    @pytest.mark.asyncio
    async def test_extract_from_text_malformed_json_raises(self):
        """Malformed LLM JSON raises ValueError."""
        llm = MagicMock(spec=LLMClient)
        llm.chat = AsyncMock(
            return_value=ChatResult(message=ChatMessage(role=Role.ASSISTANT, content="not json at all"))
        )

        with pytest.raises(ValueError):
            await FactExtractor(llm_client=llm).extract_from_text("hello")


class TestParseLlmFacts:
    """_parse_llm_facts tolerates noisy LLM output."""

    def test_non_numeric_confidence_falls_back_to_default(self):
        """A non-numeric confidence string defaults to 0.5."""
        facts = _parse_llm_facts(
            json.dumps({"facts": [{"type": "activity", "symbolic_repr": "activity.walking", "confidence": "high"}]})
        )

        assert facts[0].confidence == 0.5

    def test_confidence_clamped_to_unit_range(self):
        """Out-of-range confidence values are clamped to [0.0, 1.0]."""
        facts = _parse_llm_facts(
            json.dumps(
                {
                    "facts": [
                        {"type": "activity", "symbolic_repr": "activity.a", "confidence": 1.7},
                        {"type": "activity", "symbolic_repr": "activity.b", "confidence": -0.3},
                    ]
                }
            )
        )

        assert facts[0].confidence == 1.0
        assert facts[1].confidence == 0.0

    def test_non_dict_payload_becomes_empty(self):
        """A non-dict payload is discarded in favour of {}."""
        facts = _parse_llm_facts(
            json.dumps({"facts": [{"type": "activity", "symbolic_repr": "activity.walking", "payload": "oops"}]})
        )

        assert facts[0].payload == {}
