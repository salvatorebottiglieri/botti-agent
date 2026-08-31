# Async generator as the AgentLoop streaming seam

> **Partially superseded by [ADR-0018](0018-ask-user-tool-and-token-streaming.md).**
> The async-generator seam and error contract below stand. But token streaming is
> now real (one `TextDeltaEvent` per delta, not one per RESPOND), the event
> vocabulary gained `ask_user`, and the `ASK_QUESTION` decision type referenced in
> the #15 consequences was removed.

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

## Consequences (refined during #17)

- `ExecutionModule` exposes a transparent `stream_chat()` passthrough: it delegates to `AgentLoop.stream_chat()` unchanged — no `MaxIterationsError` swallowing (unlike `run_chat()`'s fallback). The yield-then-reraise contract must reach the consumer intact; the SSE adapter is the consumer that decides what to do with the reraise.
- SSE wire format is an explicit minimal mapping, not `to_dict()`: `thinking{message}`, `text{delta}`, `tool_start{tool_name,tool_call_id}`, `tool_done{tool_name,tool_call_id,success,output,error,execution_time_ms}`, `done{final_message,tool_calls,iterations}`, `error{error,code}`. `done` renames `message`→`final_message` and `tools_used`→`tool_calls`; `session_id` and `duration_ms` are not on the wire — a per-request SSE connection needs no session attribution, and nothing measures duration.
- Session resolution happens before the stream starts: a missing session is an HTTP 404, never an in-stream `error` frame. The stream itself only iterates `stream_chat()`.
- Error policy: on `ErrorEvent` the adapter yields the `error` frame and returns — the loop's reraise stays silent, so expected conditions like `max_iterations` produce no server traceback. Unexpected exceptions in the adapter yield an `error{code:null}` frame and then re-raise, so bugs are logged.
- Consequence of the above: streaming and non-streaming diverge on max-iterations — the stream emits `error{code:"max_iterations"}` and ends, while non-streaming `run_chat()` returns the friendly fallback message.

## Consequences (refined during #87)

`ResponseDoneEvent` carries `usage` (`UsageStats`) and `latency_ms`, and LLM-layer data models such as `UsageStats` may ride on agentic-core dataclasses (`Decision`, `ChatResponse`, `LoopEvent`). This refines "Pydantic stays at the API boundary": pydantic models are value objects defined in the llm layer — core never imports pydantic at runtime, and serialization is delegated to the model's own `model_dump()` at the `to_dict` boundary. Rationale: the one-type-per-concept rule documented in `llm/models.py` wins over duplicating a parallel core type.
