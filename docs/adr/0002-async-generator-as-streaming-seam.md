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
