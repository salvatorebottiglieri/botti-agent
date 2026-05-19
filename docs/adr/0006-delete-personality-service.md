# Delete PersonalityService — personality is injected at the prompt layer

`PersonalityService` had three methods (`get_personality`, `format_response`, `update_preferences`)
and a FastAPI dependency (`PersonalityServiceDep`) — none of which were called by any production
route, module, or service. The methods were only exercised in tests.

Personality context was already flowing to the LLM through a separate, working path:
`MemoryService.get_personality_context()` → `MemoryContext.personality` →
`Reasoner._build_prompt()` → system message injection. This path handles the core concern
(styling LLM output via prompt instructions) without an intermediate service.

`format_response` was a post-processing layer that did regex-based contraction expansion for
`formality > 0.8`. Post-processing LLM output is fragile — the LLM already styles its output
based on the injected prompt, and regex transformations risk double-processing or producing
stilted text. Deterministic post-processing should be added only when a specific test case
requires it, not as a pre-emptive layer.

The class was deleted. `InteractionService` no longer takes a `personality_service` parameter.
