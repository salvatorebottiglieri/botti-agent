"""The rules section of the Reasoner's system prompt (issue #130).

`MemoryContext.rules` reaches the prompt only through `render_rules`, so that
the staging marker and the one-line declaration instruction are written in one
place.
"""

from unittest.mock import MagicMock
from uuid import uuid4

from cortex.agentic.models import Context, MemoryContext
from cortex.agentic.reasoner import Reasoner
from cortex.rules.models import Rule, RuleState


def _reasoner() -> Reasoner:
    return Reasoner(llm_client=MagicMock(), tool_registry=MagicMock())


def _system_texts(messages) -> list[str]:
    return [message.content for message in messages if message.role.value == "system"]


class TestRulesSection:
    def test_staged_rule_reaches_the_prompt_marked_provisional(self):
        """Witness: a reasoner that only renders facts never emits the rule; a
        renderer that drops the marker loses the declaration instruction the
        staged rule depends on."""
        staged = Rule(
            condition="the user asks to book a table",
            action="ask for confirmation before booking",
            state=RuleState.STAGING,
        )
        context = Context(session_id=uuid4(), memory=MemoryContext(rules=[staged]))

        messages = _reasoner()._build_prompt(context)

        system_texts = _system_texts(messages)
        rules_section = next(
            (text for text in system_texts if "book a table" in text), None
        )
        assert rules_section is not None
        assert "ask for confirmation before booking" in rules_section
        assert "provisional" in rules_section.lower()

    def test_rules_section_follows_the_facts_section(self):
        """Witness: inserting the rules before the facts reorders the prompt and
        loses the facts-first precedence the context has today."""
        from cortex.memory.models import Fact

        fact = Fact(symbolic_repr="location.home", natural_lang_repr="At home")
        rule = Rule(
            condition="the user asks to book a table",
            action="ask for confirmation before booking",
            state=RuleState.DEFINITIVE,
        )
        context = Context(
            session_id=uuid4(),
            memory=MemoryContext(facts=[fact], rules=[rule]),
        )

        system_texts = _system_texts(_reasoner()._build_prompt(context))

        facts_index = next(i for i, text in enumerate(system_texts) if "At home" in text)
        rules_index = next(i for i, text in enumerate(system_texts) if "book a table" in text)
        assert rules_index == facts_index + 1

    def test_no_rules_means_no_rules_section(self):
        """Witness: an empty rule list must not add an empty system message."""
        context = Context(session_id=uuid4(), memory=MemoryContext())

        messages = _reasoner()._build_prompt(context)

        assert not any("Rules learned" in text for text in _system_texts(messages))
