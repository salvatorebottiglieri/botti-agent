"""Suite runner: composes the real AgentLoop inside a per-task sandbox.

Per task the runner builds the production loop wiring — ``ContextBuilder``,
``Reasoner``, ``LoopExecutor`` wrapping a ``DefaultToolExecutor`` registered
with the four meta tools — confined to a fresh ``TaskSandbox``, then drives
the task's scripted user turns through ``AgentLoop.stream_chat`` and grades
the annotated goal state against the sandbox afterwards. No new seams are
added inside existing modules; the only injected dependency is the LLM
client, so harness tests run against a fake while real runs use the factory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from cortex.agentic.context_builder import ContextBuilder
from cortex.agentic.events import LoopEvent, TextDeltaEvent
from cortex.agentic.executor import LoopExecutor
from cortex.agentic.loop import AgentLoop
from cortex.agentic.reasoner import Reasoner
from cortex.config.models import ModelPricing
from cortex.eval.baseline import record_baseline
from cortex.eval.fixtures import EvalSuite, EvalTask, assert_balanced_refusal_suite
from cortex.eval.grader import GradingResult, grade_goal
from cortex.eval.metrics import SuiteMetrics, TaskMetrics, collect_metrics
from cortex.eval.sandbox import TaskSandbox, build_sandboxed_tools
from cortex.sessions.interfaces import SessionRepository
from cortex.sessions.models import Message, MessageRole, Session, SessionState
from cortex.tools.executor import DefaultToolExecutor
from cortex.tools.registry import InMemoryToolRegistry

if TYPE_CHECKING:
    from cortex.llm.base import LLMClient
    from cortex.memory.context_provider import ContextProvider

logger = logging.getLogger(__name__)


@dataclass
class TaskResult:
    """Outcome of one eval task."""

    name: str
    passed: bool
    message: str
    failures: list[str] = field(default_factory=list)
    metrics: TaskMetrics = field(default_factory=TaskMetrics)


@dataclass
class SuiteResult:
    """Outcome of a whole suite run."""

    suite_name: str
    suite_version: str
    results: list[TaskResult]
    metrics: SuiteMetrics
    created_at: str


class _InMemorySessionRepository(SessionRepository):
    """Minimal in-memory session store so the loop keeps multi-turn state."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, Session] = {}
        self._messages: dict[UUID, list[Message]] = {}

    async def create(self) -> Session:
        session = Session()
        self._sessions[session.id] = session
        self._messages[session.id] = []
        return session

    async def get(self, session_id: UUID) -> Session | None:
        return self._sessions.get(session_id)

    async def update_state(
        self,
        session_id: UUID,
        state: SessionState,
        ended_at: datetime | None = None,
    ) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        session.state = state
        session.ended_at = ended_at
        return session

    async def update_activity(self, session_id: UUID) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_activity_at = datetime.now(UTC)

    async def add_message(
        self,
        session_id: UUID,
        role: MessageRole,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
    ) -> Message:
        message = Message(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        self._messages.setdefault(session_id, []).append(message)
        return message

    async def get_messages(
        self,
        session_id: UUID,
        limit: int = 50,
        before: datetime | None = None,
    ) -> list[Message]:
        messages = self._messages.get(session_id, [])
        if before is not None:
            messages = [m for m in messages if m.created_at < before]
        return messages[-limit:]

    async def list_active(self, limit: int = 10) -> list[Session]:
        active = [
            s
            for s in self._sessions.values()
            if s.state in (SessionState.CREATED, SessionState.ACTIVE, SessionState.IDLE)
        ]
        return active[-limit:]


class _EmptyContextProvider:
    """Memory-free stand-in so eval runs need no database."""

    async def get_memory_context(
        self,
        session_id: UUID | None,
        query: str,
        *,
        max_facts: int = 10,
        fact_types: list[Any] | None = None,
    ) -> Any:
        from cortex.agentic.models import MemoryContext

        return MemoryContext()


async def run_suite(
    suite: EvalSuite,
    llm_client: LLMClient | None = None,
    *,
    max_iterations: int = 20,
    baseline_path: str | Path | None = None,
    pricing: ModelPricing | None = None,
) -> SuiteResult:
    """Run every task in ``suite`` through the real AgentLoop.

    Args:
        suite: The eval suite to run.
        llm_client: LLM client used for reasoning. Harness tests inject a
            deterministic fake; when None, a real client is created from
            settings via the LLM client factory.
        max_iterations: Per-turn iteration cap for the loop.
        baseline_path: When given, a versioned baseline JSON is recorded
            for the suite at this path.
        pricing: Per-model token pricing used to estimate per-task LLM cost
            from usage. When None and a real client is created from settings,
            the configured model's pricing is used; when None with an
            injected client, cost is reported as zero.

    Raises:
        ValueError: If the suite is a refusal suite (its name starts with
            ``refusal``) and is unbalanced — it must contain both
            ``refuse-*`` and ``comply-*`` tasks (see
            :func:`assert_balanced_refusal_suite`).

    Returns:
        A :class:`SuiteResult` with per-task results and suite metrics.
    """
    if suite.name.startswith("refusal"):
        assert_balanced_refusal_suite(suite)
    client = llm_client
    if client is None:
        from cortex.config.loader import get_settings
        from cortex.llm.factory import LLMClientFactory

        settings = get_settings()
        client = LLMClientFactory(settings).create()
        if pricing is None:
            pricing = settings.llm_pricing.get(settings.llm_model)
    results = [
        await _run_task(task, client, max_iterations=max_iterations, pricing=pricing)
        for task in suite.tasks
    ]
    metrics = _suite_metrics(results)
    suite_result = SuiteResult(
        suite_name=suite.name,
        suite_version=suite.version,
        results=results,
        metrics=metrics,
        created_at=datetime.now(UTC).isoformat(),
    )
    if baseline_path is not None:
        record_baseline(
            suite_name=suite.name,
            suite_version=suite.version,
            metrics=metrics,
            path=baseline_path,
            created_at=suite_result.created_at,
        )
    return suite_result


async def _run_task(
    task: EvalTask,
    llm_client: LLMClient,
    *,
    max_iterations: int,
    pricing: ModelPricing | None,
) -> TaskResult:
    sandbox = TaskSandbox()
    events: list[LoopEvent] = []
    error: str | None = None
    final_message = ""
    try:
        sandbox.setup(task.sandbox)
        loop = _build_loop(llm_client, sandbox, max_iterations=max_iterations)
        session_id = uuid4()
        for turn in task.turns:
            final_message = await _drive_turn(loop, session_id, turn, events)
    except Exception as exc:  # noqa: BLE001 - loop errors fail the task
        error = str(exc)
        logger.warning("Eval task %s failed: %s", task.name, exc)

    try:
        grade: GradingResult = grade_goal(task.goal, sandbox, final_message)
    except Exception as exc:  # noqa: BLE001 - grading must not crash the suite
        grade = GradingResult(passed=False, failures=[f"grading failed: {exc}"])
    finally:
        sandbox.cleanup()

    failures = list(grade.failures)
    if error is not None:
        failures.append(f"loop error: {error}")
    return TaskResult(
        name=task.name,
        passed=grade.passed and error is None,
        message=error or final_message,
        failures=failures,
        metrics=collect_metrics(events, pricing=pricing),
    )


def _build_loop(
    llm_client: LLMClient,
    sandbox: TaskSandbox,
    *,
    max_iterations: int,
) -> AgentLoop:
    """Wire the real loop components against the sandbox (no new seams)."""
    registry = InMemoryToolRegistry()
    for tool in build_sandboxed_tools(sandbox):
        registry.register(tool)

    tool_executor = DefaultToolExecutor(registry=registry)
    loop_executor = LoopExecutor(tool_executor=tool_executor)

    session_repository: SessionRepository = _InMemorySessionRepository()
    from cortex.memory.context_provider import ContextProvider

    context_provider: ContextProvider = cast(ContextProvider, _EmptyContextProvider())
    context_builder = ContextBuilder(
        session_repository=session_repository,
        context_provider=context_provider,
        tool_registry=registry,
    )
    reasoner = Reasoner(llm_client=llm_client, tool_registry=registry)

    return AgentLoop(
        context_builder=context_builder,
        reasoner=reasoner,
        executor=loop_executor,
        session_repository=session_repository,
        max_chat_iterations=max_iterations,
    )


async def _drive_turn(
    loop: AgentLoop,
    session_id: UUID,
    turn: str,
    events: list[LoopEvent],
) -> str:
    """Run one scripted user turn, recording the transcript.

    Returns the final response text, accumulated from TextDeltaEvent deltas
    in order — consumers must never assume chunk size.
    """
    final_text = ""
    async for event in loop.stream_chat(session_id, turn):
        events.append(event)
        if isinstance(event, TextDeltaEvent):
            final_text += event.delta
    return final_text


def _suite_metrics(results: list[TaskResult]) -> SuiteMetrics:
    tools_used: list[str] = []
    seen: set[str] = set()
    for result in results:
        for name in result.metrics.tools_used:
            if name not in seen:
                seen.add(name)
                tools_used.append(name)
    return SuiteMetrics(
        task_count=len(results),
        pass_count=sum(1 for r in results if r.passed),
        total_iterations=sum(r.metrics.iterations for r in results),
        total_tool_calls=sum(r.metrics.tool_calls for r in results),
        tools_used=tools_used,
        total_latency_ms=sum(r.metrics.latency_ms or 0.0 for r in results),
        total_cost_usd=sum(r.metrics.cost_usd for r in results),
    )
