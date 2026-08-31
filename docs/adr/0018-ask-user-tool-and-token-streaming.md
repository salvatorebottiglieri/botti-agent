# Clarification is a tool (ask_user), and the loop streams tokens

Two decisions that this ADR records, refining ADR-0002 and superseding the
clarification half of ADR-0005.

## Clarification via the `ask_user` tool (supersedes ADR-0005's `[QUESTION]`)

ADR-0005 made `DecisionType.ASK_QUESTION` reachable by teaching the model a
`[QUESTION]...[/QUESTION]` text convention parsed out of the response with a
regex. That convention was fragile as ADR-0005 itself predicted: models emit
malformed markers (a truncated `[/QUESTION`), and in a token stream the raw
markers scroll out to the user before the parser can recognize them.

We replaced it with a structured tool. The model calls `ask_user(question,
options?)`; the tool returns a `ToolResult` carrying `control="ask_user"` (a
new typed field on `ToolResult`). The loop recognizes the control signal and
halts, surfacing the question — no regex, no markers on the wire, and the
model's own function-calling machinery guarantees the shape. Optional
`options` are model-suggested answers the UI renders as clickable choices.

Persistence (B2): the loop consumes the `ask_user` call and persists the
question as a plain assistant message, **not** as an assistant tool-call +
tool-result round. This keeps the conversation clean and, crucially, avoids a
dangling tool-call in history (OpenAI-compatible providers reject an assistant
tool-call with no matching tool message). `ask_user` is excluded from
`tools_used` — it is a control signal, not work.

Consequently the marker parsing, `DecisionType.ASK_QUESTION`, and
`Decision.ask_question` were removed. ADR-0005's other decision — typed
`ChatResult` access and raising on empty responses instead of `getattr` chains
— stands unchanged.

## Token streaming (refines ADR-0002)

ADR-0002 established the async-generator streaming seam and noted that
`TextDeltaEvent` carried the *full* response text as a single event, with
"multiple deltas only if the reasoner streams tokens." The reasoner now
streams tokens, so that hypothetical is the default:

- `LLMClient.chat_stream()` streams provider deltas and reassembles a final
  `ChatResult`; `Reasoner.reason_stream()` and `AgentLoop.stream_chat(stream=…)`
  propagate deltas up to one `TextDeltaEvent` per delta. The SSE route enables
  it via `ChatRequest.stream` (default on).
- Out-of-band reasoning (a provider's separate `reasoning_content` field) is
  normalized at the provider boundary into an inline `<think>...</think>` block,
  so every downstream consumer — and the UI's reasoning toggle — treats the two
  reasoning-delivery styles identically.
- A new `AskUserEvent` (wire name `ask_user`, carrying `question` + `options`)
  joins the event vocabulary. The old `ASK_QUESTION` decision type referenced in
  ADR-0002's #15 consequences is gone.

## Event vocabulary (current)

`thinking`, `text`, `tool_start`, `tool_done`, `ask_user`, `done`, `error`.
The async-generator seam, the yield-then-reraise error contract, and the
non-streaming `run_chat` drainer from ADR-0002 are unchanged; the drainer also
accumulates an `AskUserEvent`'s question as the turn's message.
