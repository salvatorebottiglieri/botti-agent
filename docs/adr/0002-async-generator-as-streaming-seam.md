# Async generator as the AgentLoop streaming seam

The AgentLoop needs to be observable in real time so SSE, CLI, and future consumers can stream
thought → tool_start → tool_result → response → done events. We chose an async generator
(`async def stream_chat(...) -> AsyncGenerator[LoopEvent, None]`) over injecting an observer
callback or publishing to the event bus.

Rejected alternatives:

- **Observer callback**: adds an `on_event: Callable` parameter. Only one consumer (SSE) exists
  today, so this is a hypothetical seam — no proof anything varies. A generator proves the seam
  real with two adapters immediately (the streaming SSE route and the non-streaming wrapper that
  drains it into `ChatResponse`).
- **Event bus**: would make every `loop.thought` and `loop.tool_start` event visible to every
  system module forever. The loop's internal lifecycle is not system-wide domain information; it's
  progress signal for a specific caller.

Consequence: `stream_chat` owns the loop logic. The existing `run_chat()` is a convenience
wrapper that drains the generator and returns `ChatResponse`. Callers that need a final result
call `run_chat`; callers that need progress call `stream_chat`.

## Consequences (refined during #14)

`LoopEvent` is a dataclass base (consistent with the agentic core; Pydantic stays at the API boundary). Every event carries `session_id`. `event_type` is a class constant whose values ARE the SSE wire names — one vocabulary: `thinking`, `text`, `tool_start`, `tool_done`, `done`, `error`. `ResponseDoneEvent.tools_used` carries tool names (same semantic as `ChatResponse.tools_used`). `ErrorEvent.code` is a free string with `max_iterations` reserved.

The obsolete `loop.*` members in `EventTypes` (loop.started, loop.thought, loop.tools_executed, loop.completed, loop.error) were removed with #14 — the loop's lifecycle is caller-scoped progress, never bus events.

## Consequences (refined during #15)

- `ThinkingEvent` is emitted exactly once per iteration, immediately after the decision arrives, with `message = decision.reasoning`. There is no `THINK` decision type (the reasoner returns only RESPOND / ASK_QUESTION / EXECUTE_TOOLS) and no "user message added" event.
- Error contract: `stream_chat` yields `ErrorEvent` and then **re-raises the original exception** — uniformly, for every exception including `MaxIterationsError`. Errors are never swallowed into a silent event. `code` is `"max_iterations"` for `MaxIterationsError`, `None` otherwise.
- Tool events interleave per call: `ToolStartEvent` → `execute_single(call)` → `ToolResultEvent`, preserving the loop's sequential execution semantics (the loop never passed `parallel=True`). `run_goal` keeps the batch `execute_tools`.
- `EXECUTE_TOOLS` with an empty `tool_calls` list responds with the fallback text ("I couldn't determine what tools to use.") as a normal `TextDeltaEvent` + `ResponseDoneEvent` — parity with `run_chat`, iterations not incremented.
- `TextDeltaEvent` is one event per RESPOND/ASK_QUESTION carrying the full response text. "Delta" means a unit of response text — chunk size is never guaranteed; multiple deltas per response only materialize if the reasoner streams tokens.
- `run_chat()` is untouched by #15; its drainer rewrite is #16.
