# Typed Reasoner parsing with [QUESTION] convention

> **Clarification half superseded by [ADR-0018](0018-ask-user-tool-and-token-streaming.md).**
> The typed `ChatResult` access and the empty-response `ValueError` below still
> stand. The `[QUESTION]...[/QUESTION]` convention and `DecisionType.ASK_QUESTION`
> were removed — the model now asks for clarification by calling the `ask_user`
> tool.

The `Reasoner._parse_response` method used `getattr(result, "message", None)` chains to
extract content and tool calls from `ChatResult`, even though `ChatResult` is a Pydantic
model with guaranteed typed fields. Empty responses fell through silently to a
"I'm not sure how to respond" message instead of raising.

We replaced `getattr` chains with typed access (`result.message.content`,
`result.tool_calls`), added a `ValueError` raise on empty responses, and added a
`[QUESTION]` text convention to the default system prompt so the LLM can signal
clarification requests (`DecisionType.ASK_QUESTION`), which was previously unreachable
dead code in the `AgentLoop`.

Rejected alternative: OpenAI `response_format` with JSON schema. This would require
dropping native function calling for tools, which is a larger trade-off — native tools
are provider-optimized and carry less prompt overhead than JSON-schema-described tools.
The `[QUESTION]` convention is fragile across model upgrades but is the lightest way to
make `ASK_QUESTION` reachable today.
