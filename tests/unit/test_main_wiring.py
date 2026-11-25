"""The composition root builds the attribution path (#172).

``initialize_app`` is exercised against fakes for everything that talks to the
outside world (pool, migrations, event bus, minion MQTT, the loop resume), so
the test pins the wiring, not the infrastructure.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.config.models import Settings

SETTINGS = Settings(llm={"api_key": "test-key"})

# Stands in for the real MinionService: the wiring under test is "whatever the
# init puts on the container reaches the state dict the API reads".
class FakeMinionService:
    async def disconnect(self) -> None:
        pass


MINION_SERVICE = FakeMinionService()


@pytest.fixture
async def wired_app(monkeypatch):
    """``initialize_app`` with the drain loop held open, so it can be observed."""
    bus = MagicMock()
    bus.start = AsyncMock()
    bus.stop = AsyncMock()
    bus.subscribe = AsyncMock()
    bus.publish = AsyncMock()

    started = asyncio.Event()
    seen: list[object] = []

    async def hold_open(drain) -> None:
        seen.append(drain)
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("cortex.main._feedback_drain_loop", hold_open)

    async def fake_minion_init(cortex) -> None:
        cortex.minion_service = MINION_SERVICE

    monkeypatch.setattr("cortex.main._initialize_minion_service", fake_minion_init)

    with (
        patch("cortex.main.get_settings", return_value=SETTINGS),
        patch("cortex.db.pool.create_pool", AsyncMock(return_value=MagicMock())),
        patch("cortex.db.migrations.runner.run_migrations", AsyncMock()),
        patch("cortex.events.bus.EventBus", return_value=bus),
        patch("cortex.execution.module.ExecutionModule.resume_in_flight", AsyncMock()),
    ):
        from cortex.main import initialize_app

        cortex = await initialize_app()

    yield cortex, started, seen
    await cortex.shutdown()


async def test_the_root_builds_the_run_the_queue_and_the_drain(wired_app):
    from cortex.llm.wrapper import CircuitBreakerLLMClient
    from cortex.rules.attribution import AttributionRun
    from cortex.rules.conflicts import ConflictCheck
    from cortex.rules.feedback import FeedbackClassifier
    from cortex.rules.queue import FeedbackDrain, PostgresFeedbackQueue
    from cortex.rules.splits import SplitProposer

    cortex, _, _ = wired_app

    drain = cortex.feedback_drain
    assert isinstance(drain, FeedbackDrain)
    assert isinstance(drain._queue, PostgresFeedbackQueue)
    run = drain._run
    assert isinstance(run, AttributionRun)
    assert run._service is cortex.rule_service
    assert isinstance(run._classifier, FeedbackClassifier)
    assert isinstance(run._conflicts, ConflictCheck)
    assert isinstance(run._splits, SplitProposer)
    # The classifier rides its own per-module circuit, so a classifier outage
    # leaves the rest of Cortex alone.
    client = run._classifier._llm_client
    assert isinstance(client, CircuitBreakerLLMClient)
    assert client._module == "rules"


async def test_the_state_dict_exposes_the_two_new_dependencies(wired_app):
    from cortex.api.dependencies import get_app_state

    cortex, _, _ = wired_app

    state = get_app_state()
    assert state["rule_service"] is cortex.rule_service
    assert state["feedback_queue"] is cortex.feedback_drain._queue


async def test_the_state_carries_the_minion_service_the_root_initialized(wired_app):
    """The init runs after the state dict exists: the service must still reach it."""
    from cortex.api.dependencies import get_app_state

    cortex, _, _ = wired_app

    assert cortex.minion_service is MINION_SERVICE
    assert get_app_state()["minion_service"] is MINION_SERVICE


async def test_the_drain_loop_is_started_with_the_drain(wired_app):
    cortex, started, seen = wired_app

    await asyncio.wait_for(started.wait(), timeout=1)

    assert seen == [cortex.feedback_drain]
    assert cortex.feedback_drain_task is not None
    assert not cortex.feedback_drain_task.done()


async def test_the_drain_loop_logs_and_survives_a_failed_drain(caplog):
    from cortex.main import _feedback_drain_loop

    drain = MagicMock()
    drain.drain = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch("cortex.main.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError)):
        with caplog.at_level("WARNING", logger="cortex.main"):
            with pytest.raises(asyncio.CancelledError):
                await _feedback_drain_loop(drain)

    drain.drain.assert_awaited_once()
    assert any("Feedback drain failed" in record.message for record in caplog.records)


async def test_the_rule_service_publishes_through_the_bus(wired_app):
    """#173: the promotion edge exists only if the service can publish at all."""
    from cortex.events.emitter import EventEmitter

    cortex, _, _ = wired_app

    emitter = cortex.rule_service._emitter
    assert isinstance(emitter, EventEmitter)
    assert emitter._bus is cortex.event_bus
    assert emitter._source_module == "rules"


async def test_the_loop_reads_the_notices_the_root_built(wired_app):
    """One buffer: the object the loop drains is the object the bus feeds."""
    from cortex.agentic.notices import PromotionNotices

    cortex, _, _ = wired_app

    assert isinstance(cortex.promotion_notices, PromotionNotices)
    assert cortex.execution_module._agent_loop._notices is cortex.promotion_notices


async def test_the_notices_handler_is_subscribed_to_rule_promoted(wired_app):
    from cortex.events.types import EventTypes

    cortex, _, _ = wired_app

    subscriptions = {
        call.args[0]: call.args[1] for call in cortex.event_bus.subscribe.await_args_list
    }
    handler = subscriptions[EventTypes.RULE_PROMOTED]
    assert handler.__self__ is cortex.promotion_notices


class FakeServer:
    """Stands in for uvicorn's server: ``serve`` returns without binding a socket."""

    def __init__(self, config) -> None:
        self.config = config

    async def serve(self) -> None:
        pass


@pytest.fixture
def entry_point(monkeypatch):
    """``cortex.__main__`` with the app and uvicorn's server replaced by fakes."""
    from cortex import __main__ as entrypoint
    from cortex.main import CortexApp

    cortex = CortexApp(app=MagicMock(), settings=SETTINGS)
    shutdown = AsyncMock()
    monkeypatch.setattr(cortex, "shutdown", shutdown)

    async def fake_initialize_app() -> CortexApp:
        return cortex

    # ``__main__`` binds the name at import time, so patch it there, not on
    # ``cortex.main``.
    monkeypatch.setattr(entrypoint, "initialize_app", fake_initialize_app)

    return entrypoint, shutdown


async def test_the_entry_point_shuts_the_app_down_when_serve_returns(entry_point, monkeypatch):
    """#188: ``python -m cortex`` must release what ``initialize_app`` acquired."""
    module, shutdown = entry_point
    monkeypatch.setattr("uvicorn.Server", FakeServer)

    await module.run()

    shutdown.assert_awaited_once()


async def test_the_entry_point_shuts_the_app_down_when_serve_fails(entry_point, monkeypatch):
    """A serve failure must still release the pool, the bus and the minion link."""
    module, shutdown = entry_point

    class FailingServer(FakeServer):
        async def serve(self) -> None:
            raise RuntimeError("address already in use")

    exit_code = MagicMock()

    monkeypatch.setattr("uvicorn.Server", FailingServer)
    # ``run`` exits the process on failure; the test only observes the code.
    monkeypatch.setattr("sys.exit", exit_code)

    await module.run()

    shutdown.assert_awaited_once()
    exit_code.assert_called_once_with(1)
