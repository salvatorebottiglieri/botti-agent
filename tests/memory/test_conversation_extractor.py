"""Tests for ConversationFactExtractor."""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from cortex.llm.base import LLMClient
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.memory.conversation_extractor import ConversationFactExtractor
from cortex.memory.fact_extractor import FactExtractor
from cortex.memory.fact_store import FactStore
from cortex.memory.models import Fact, FactMutability, FactType


def _llm_returning(content: str | None) -> MagicMock:
    """An LLM client stub whose chat() returns ``content``."""
    llm = MagicMock(spec=LLMClient)
    llm.chat = AsyncMock(
        return_value=ChatResult(message=ChatMessage(role=Role.ASSISTANT, content=content))
    )
    return llm


def _store(*, fail_for: set[str] | None = None) -> MagicMock:
    """A FactStore stub that returns the fact it was given."""
    store = MagicMock(spec=FactStore)
    fail_for = fail_for or set()

    async def add_fact(fact: Fact, **_: object) -> Fact:
        if fact.symbolic_repr in fail_for:
            raise RuntimeError("storage unavailable")
        return fact

    store.add_fact = AsyncMock(side_effect=add_fact)
    return store


class TestExtractAndStore:
    """The conversation seam extracts, annotates and stores facts."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", ["", "   ", "\n\t "])
    async def test_blank_text_returns_empty_without_calling_the_llm(self, text):
        """Blank/whitespace text short-circuits before extraction."""
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock()

        result = await ConversationFactExtractor(extractor, _store()).extract_and_store(text)

        assert result == []
        extractor.extract_from_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_static_birth_place_fact_is_stored_with_confidence_one(self):
        """The witness: 'born in Rome' is STATIC, confidence forced to 1.0."""
        session_id = uuid4()
        llm = _llm_returning(
            json.dumps(
                {
                    "facts": [
                        {
                            "type": "user_fact",
                            "symbolic_repr": "user_fact.birth_place",
                            "natural_lang_repr": "Born in Rome",
                            "confidence": 0.42,
                            "mutability": "static",
                            "payload": {"place": "Rome"},
                        }
                    ]
                }
            )
        )
        store = _store()

        stored = await ConversationFactExtractor(
            FactExtractor(llm_client=llm), store
        ).extract_and_store("I was born in Rome.", session_id=session_id)

        assert len(stored) == 1
        fact = stored[0]
        assert fact.mutability is FactMutability.STATIC
        assert fact.confidence == 1.0
        assert fact.type is FactType.USER_FACT
        assert fact.symbolic_repr == "user_fact.birth_place"
        assert fact.natural_lang_repr == "Born in Rome"
        assert fact.payload == {
            "place": "Rome",
            "source": "conversation",
            "session_id": str(session_id),
        }
        store.add_fact.assert_awaited_once()
        assert store.add_fact.await_args.kwargs == {
            "source_type": "llm",
            "source_id": f"conversation:{session_id}",
        }

    @pytest.mark.asyncio
    async def test_attitude_and_behavior_are_distinct_facts(self):
        """'likes coffee' and 'drinks coffee' stay two facts with distinct reprs."""
        llm = _llm_returning(
            json.dumps(
                {
                    "facts": [
                        {
                            "type": "user_preference",
                            "symbolic_repr": "user_preference.likes_coffee",
                            "natural_lang_repr": "Likes coffee",
                            "confidence": 0.8,
                            "mutability": "mutable",
                            "payload": {"object": "coffee"},
                        },
                        {
                            "type": "activity",
                            "symbolic_repr": "activity.drinks_coffee",
                            "natural_lang_repr": "Drinks coffee",
                            "confidence": 0.7,
                            "mutability": "ephemeral",
                            "payload": {"object": "coffee"},
                        },
                    ]
                }
            )
        )
        store = _store()

        stored = await ConversationFactExtractor(
            FactExtractor(llm_client=llm), store
        ).extract_and_store("I like coffee and I drink coffee every morning.")

        assert len(stored) == 2
        reprs = {fact.symbolic_repr for fact in stored}
        assert reprs == {"user_preference.likes_coffee", "activity.drinks_coffee"}
        assert stored[0].type is FactType.USER_PREFERENCE
        assert stored[1].type is FactType.ACTIVITY
        # Every extracted fact carries the same fields as a sensory fact.
        for fact in stored:
            assert fact.symbolic_repr
            assert fact.natural_lang_repr
            assert 0.0 <= fact.confidence <= 1.0
            assert isinstance(fact.payload, dict)

    @pytest.mark.asyncio
    async def test_malformed_llm_json_returns_empty_and_does_not_raise(self, caplog):
        """Malformed JSON degrades to no facts; the chat path never crashes."""
        store = _store()

        with caplog.at_level("WARNING", logger="cortex.memory.conversation_extractor"):
            result = await ConversationFactExtractor(
                FactExtractor(llm_client=_llm_returning("not json at all")), store
            ).extract_and_store("hello")

        assert result == []
        store.add_fact.assert_not_awaited()
        assert any("extraction failed" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", [None, "", '{"facts": []}'])
    async def test_empty_llm_output_returns_empty_and_does_not_raise(self, content):
        """No content, empty content, or no facts all degrade to an empty result."""
        store = _store()

        result = await ConversationFactExtractor(
            FactExtractor(llm_client=_llm_returning(content)), store
        ).extract_and_store("hello")

        assert result == []
        store.add_fact.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_extractor_exception_returns_empty_and_does_not_raise(self, caplog):
        """An extractor/provider error degrades to no facts."""
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock(side_effect=RuntimeError("provider down"))
        store = _store()

        with caplog.at_level("WARNING", logger="cortex.memory.conversation_extractor"):
            result = await ConversationFactExtractor(extractor, store).extract_and_store("hello")

        assert result == []
        store.add_fact.assert_not_awaited()
        assert any("extraction failed" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_empty_symbolic_repr_is_skipped(self):
        """Facts without a symbolic representation are not stored."""
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock(
            return_value=[
                Fact(symbolic_repr="", natural_lang_repr="no key"),
                Fact(
                    type=FactType.USER_PREFERENCE,
                    symbolic_repr="user_preference.coffee",
                    natural_lang_repr="Likes coffee",
                ),
            ]
        )
        store = _store()

        stored = await ConversationFactExtractor(extractor, store).extract_and_store("I like coffee")

        assert [fact.symbolic_repr for fact in stored] == ["user_preference.coffee"]
        store.add_fact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_failure_on_one_fact_does_not_drop_the_rest(self, caplog):
        """One storage failure is logged; remaining facts are still stored."""
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock(
            return_value=[
                Fact(symbolic_repr="user_preference.tea"),
                Fact(symbolic_repr="user_preference.broken"),
                Fact(symbolic_repr="user_preference.coffee"),
            ]
        )
        store = _store(fail_for={"user_preference.broken"})

        with caplog.at_level("WARNING", logger="cortex.memory.conversation_extractor"):
            stored = await ConversationFactExtractor(extractor, store).extract_and_store(
                "I like tea and coffee"
            )

        assert [fact.symbolic_repr for fact in stored] == [
            "user_preference.tea",
            "user_preference.coffee",
        ]
        assert store.add_fact.await_count == 3
        assert any("Storing conversation fact" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_without_a_session_id_no_session_payload_is_added(self):
        """A session-less call only adds the conversation source marker."""
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock(
            return_value=[Fact(symbolic_repr="user_preference.coffee")]
        )
        store = _store()

        stored = await ConversationFactExtractor(extractor, store).extract_and_store(
            "I like coffee"
        )

        assert stored[0].payload == {"source": "conversation"}
        assert store.add_fact.await_args.kwargs == {
            "source_type": "llm",
            "source_id": "conversation",
        }

    @pytest.mark.asyncio
    async def test_llm_payload_cannot_spoof_provenance(self):
        """LLM-supplied source/session values are overwritten with the real ones."""
        session_id = uuid4()
        extractor = MagicMock(spec=FactExtractor)
        extractor.extract_from_text = AsyncMock(
            return_value=[
                Fact(
                    symbolic_repr="user_preference.coffee",
                    payload={"source": "sensor", "session_id": "spoofed"},
                )
            ]
        )
        store = _store()

        stored = await ConversationFactExtractor(extractor, store).extract_and_store(
            "I like coffee", session_id=session_id
        )

        assert stored[0].payload == {
            "source": "conversation",
            "session_id": str(session_id),
        }
