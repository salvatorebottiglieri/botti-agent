"""PromotionNotices — the agentic side of the `rule.promoted` edge (#173).

A promotion waits here until the next chat turn reads it: FIFO, one line per
turn, bounded, and deaf to anything that is not a promotion.
"""

from cortex.agentic.notices import PromotionNotices
from cortex.events.base import BaseEvent
from cortex.events.types import EventTypes


def _promotion(line: object = "Rule confirmed: a → b") -> BaseEvent:
    return BaseEvent.create(
        event_type=EventTypes.RULE_PROMOTED,
        payload={"line": line},
        source_module="rules",
    )


async def test_a_promoted_event_enqueues_its_line():
    notices = PromotionNotices()

    await notices.handle(_promotion("Rule confirmed: book a table → confirm first"))

    assert notices.take() == "Rule confirmed: book a table → confirm first"


async def test_take_returns_the_lines_fifo_and_none_when_empty():
    """Witness: LIFO would announce the newer promotion first, and the oldest
    would wait behind it."""
    notices = PromotionNotices()
    for index in range(3):
        await notices.handle(_promotion(f"Rule confirmed: c{index} → a{index}"))

    assert [notices.take() for _ in range(4)] == [
        "Rule confirmed: c0 → a0",
        "Rule confirmed: c1 → a1",
        "Rule confirmed: c2 → a2",
        None,
    ]


async def test_the_cap_drops_the_oldest():
    """Witness: an unbounded buffer keeps a line nobody read for weeks."""
    notices = PromotionNotices(capacity=2)

    for index in range(3):
        await notices.handle(_promotion(f"Rule confirmed: c{index} → a{index}"))

    assert [notices.take(), notices.take(), notices.take()] == [
        "Rule confirmed: c1 → a1",
        "Rule confirmed: c2 → a2",
        None,
    ]


async def test_the_default_buffer_holds_twenty_lines():
    notices = PromotionNotices()

    for index in range(21):
        await notices.handle(_promotion(f"line {index}"))

    assert [notices.take() for _ in range(21)] == [f"line {index}" for index in range(1, 21)] + [
        None
    ]


async def test_an_event_of_another_type_is_ignored():
    """Witness: a handler keyed on the payload alone would announce a line no
    rule ever produced."""
    notices = PromotionNotices()

    await notices.handle(
        BaseEvent.create(
            event_type=EventTypes.CONVERSATION_MESSAGE,
            payload={"line": "Rule confirmed: a → b"},
            source_module="memory",
        )
    )

    assert notices.take() is None


async def test_an_event_without_a_line_is_ignored():
    notices = PromotionNotices()

    await notices.handle(BaseEvent.create(event_type=EventTypes.RULE_PROMOTED, source_module="rules"))
    await notices.handle(_promotion("   "))
    await notices.handle(_promotion(None))

    assert notices.take() is None
