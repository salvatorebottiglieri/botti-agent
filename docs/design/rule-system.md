# Program Design: rule-system — ticket #168: a turn becomes a Feedback

Spec: `docs/specs/rule-system.md` (slice B). Parent: #166. Frontier ticket: #168.

**Status: approved** — the one load-bearing choice below is signed by the owner in chat, 2026-11-20. The "categories, never numbers" rule is not a new choice: it is ADR-0020's, restated in the spec.

_CLASSIFY: new capability inside an existing module (one new seam, no schema in this ticket, no external contract)_

## Shape

- **`cortex.rules.feedback`** (new): `FeedbackClassifier` — one `chat()` per turn, returns a `Feedback`. It hides the prompt, the parsing and the tolerance rules; the caller sees only `Feedback`.
- **`cortex.rules.attribution`** (new): `AttributionRun` — takes a `FeedbackClassifier` and a `RuleService`, writes the uses through `RuleService.record_use`, and never lets a classifier or storage failure escape: a failed turn writes nothing and the caller is told nothing happened.
- **`cortex.llm.providers.openai.OpenAIClient.chat`** (modified): normalises out-of-band reasoning into the inline `<think>` block that `chat_stream()` already produces, using the same `_reasoning_delta` helper, so a non-streaming turn carries the reasoning the classifier reads.
- **Not changed:** `RuleService`, the repository, the inference of a Rule's state, the SSE adapter, the CLI. The queue, the proposals path and the wiring into the chat endpoint belong to #169, #170 and #172 — this ticket runs the classifier and the run directly, against the in-memory repository.

## Decision 1 — the classifier runs only on a turn that can be attributed

- **Choice**: no call when the turn had no Rules in its context, and no call when the user's reply is blank. Silence is not evidence, and a turn with no Rule in context has nothing to attribute.
- **Alternatives rejected**: one call per turn regardless (every turn of every session pays a provider call for nothing); trigger on the text looking like feedback (a heuristic that misses derivations and is invisible to the user when it is wrong).
- **Consequences**: the number of calls is bounded by turns that had Rules and got an answer, which is what a Rule system actually needs.
- **Owner sign-off**: 2026-11-20, owner in chat — "Ok tutte" (to the four recommendations of the phase-1 interview, of which this is the first).

## Premises (design)

Inherited: P1-P10 of the spec. New:

| # | Premise | Command | Output | Verdict |
|---|---|---|---|---|
| P11 | `chat()` and `chat_stream()` share a helper for out-of-band reasoning, so normalising `chat()` is one call, not a second implementation | `rg -n "_reasoning_delta" src/cortex/llm/providers/openai.py` | `openai.py:30: def _reasoning_delta(delta)`, called at `:247` in the stream loop and nowhere else | confirmed |
| P12 | `GenerationConfig` already carries the fields a deterministic classification needs, so no settings change is required | `rg -n "class GenerationConfig" -A20 src/cortex/llm/config.py` | `temperature`, `max_tokens`, `top_p`, `seed`, `response_format` and more, all optional | confirmed |
| P13 | The rules tests already use an in-memory `RuleRepository` with the Postgres semantics (ids and ordering), so this ticket needs no database | `tests/unit/rules/fakes.py` | `InMemoryRuleRepository` — copies on read/write, ids assigned, ordering by confidence | confirmed |

## External fixtures

None. The provider is faked in every test (`MagicMock(spec=LLMClient)` as `tests/memory/test_conversation_extractor.py` does, or the scripted client of `tests/eval/fakes.py`); no response of a real provider is captured, and none is needed: the classifier's contract is with its own prompt, and the provider's shapes are pinned by `tests/unit/test_llm.py`.

## Exits (test list for the implementer)

In `tests/unit/rules/test_feedback.py`, `tests/unit/rules/test_attribution.py` and `tests/unit/test_llm.py`; no network, no database.

- `test_chat_normalises_reasoning_into_an_inline_think_block`: a non-streaming response whose delta carries reasoning produces `...<think>...</think>` in the content, exactly as `chat_stream()` does.
- `test_chat_without_reasoning_is_unchanged`: no `<think>` block, content untouched.
- `test_blank_reply_makes_no_call`: empty and whitespace replies → no LLM call, `Feedback` with no uses and no proposals.
- `test_no_rules_makes_no_call`.
- `test_a_reasoning_turn_is_what_the_classifier_sends`: the request body contains the assistant turn verbatim, including its `<think>` block.
- `test_one_use_per_rule_and_a_second_entry_for_the_same_rule_is_dropped`.
- `test_an_invented_rule_id_is_dropped`.
- `test_an_unknown_category_is_dropped`.
- `test_a_rule_in_context_but_not_applied_gets_no_entry`.
- `test_malformed_json_raises_value_error`.
- `test_fenced_json_is_accepted`.
- `test_a_proposal_with_blank_condition_or_action_is_dropped`.
- `test_a_proposal_with_an_unknown_origin_defaults_to_correction`.
- `test_a_proposal_never_carries_a_confidence`.
- `test_the_run_writes_one_use_per_attributed_rule_through_the_service`: confidence and state move for the staged Rule.
- `test_the_run_writes_nothing_when_the_classifier_raises`: no use row, no error to the caller.
- `test_the_run_skips_a_rule_that_vanished`: `RuleNotFoundError` from the service is logged and the other uses are still written.
- `test_the_run_writes_nothing_when_the_repository_fails`: the storage error is logged, not raised.

## JSON design

```json design
{
 "feature": "rule-system",
 "call_stack": {
  "name": "entrypoints",
  "children": [
   {
    "name": "cortex.rules.attribution.AttributionRun.attribute",
    "note": "#168 the turn's rules, the assistant turn and the reply in; the refreshed rules out",
    "children": [
     {
      "name": "cortex.rules.feedback.FeedbackClassifier.classify",
      "note": "#168 one provider call, tolerant parse",
      "children": [
       {
        "name": "cortex.rules.feedback._parse_feedback",
        "note": "#168 pure: JSON to Feedback, drops what it cannot trust",
        "children": []
       }
      ]
     },
     {
      "name": "cortex.rules.service.RuleService.record_use",
      "note": "unchanged; recomputes and refreshes per use",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.llm.providers.openai.OpenAIClient.chat",
    "note": "#168 normalises out-of-band reasoning into the inline think block",
    "children": [
     {
      "name": "cortex.llm.providers.openai._reasoning_delta",
      "note": "#168 the helper the streaming path already uses",
      "children": []
     }
    ]
   }
  ]
 },
 "file_tree": [
  {
   "kind": "add",
   "path": "src/cortex/rules/feedback.py",
   "note": "#168 Feedback, AttributedUse, RuleProposal, FeedbackClassifier, _parse_feedback"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/attribution.py",
   "note": "#168 AttributionRun"
  },
  {
   "kind": "mod",
   "path": "src/cortex/llm/providers/openai.py",
   "note": "#168 chat() carries the reasoning into the content"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_feedback.py",
   "note": "#168 the classifier and the parse"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_attribution.py",
   "note": "#168 the run against the in-memory repository"
  },
  {
   "kind": "mod",
   "path": "tests/unit/test_llm.py",
   "note": "#168 the non-streaming reasoning tests"
  }
 ],
 "signatures": [
  {
   "module": "src/cortex/rules/feedback.py",
   "language": "python",
   "code": "@dataclass(frozen=True)\nclass AttributedUse:\n    rule_id: UUID\n    outcome: UseOutcome\n\n@dataclass(frozen=True)\nclass RuleProposal:\n    spec: RuleSpec\n    origin: RuleOrigin\n\n@dataclass(frozen=True)\nclass Feedback:\n    uses: list[AttributedUse] = field(default_factory=list)\n    proposals: list[RuleProposal] = field(default_factory=list)\n\ndef _parse_feedback(content: str, known_rule_ids: set[UUID]) -> Feedback: ...\n\nclass FeedbackClassifier:\n    def __init__(self, llm_client: LLMClient, *, generation: GenerationConfig | None = None) -> None: ...\n    async def classify(self, rules: Sequence[Rule], assistant_turn: str, user_message: str) -> Feedback: ..."
  },
  {
   "module": "src/cortex/rules/attribution.py",
   "language": "python",
   "code": "class AttributionRun:\n    def __init__(self, classifier: FeedbackClassifier, service: RuleService) -> None: ...\n    async def attribute(self, rules: Sequence[Rule], session_id: UUID, assistant_turn: str, user_message: str, situation: str) -> list[Rule]: ..."
  },
  {
   "module": "src/cortex/llm/providers/openai.py",
   "language": "python",
   "code": "async def chat(self, messages: list[ChatMessage], *, tools: list[ToolDefinition] | None = None, generation_config: GenerationConfig | None = None) -> ChatResult: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def record_use(self, rule_id: UUID, session_id: UUID, situation: str, outcome: UseOutcome) -> Rule: ..."
  },
  {
   "module": "src/cortex/llm/providers/openai.py",
   "language": "python",
   "code": "def _reasoning_delta(delta: Any) -> str | None: ..."
  }
 ],
 "data_flow": {
  "nodes": [
   { "id": "cortex.rules.attribution.AttributionRun.attribute", "module": "src/cortex/rules/attribution.py", "kind": "new" },
   { "id": "cortex.rules.feedback.FeedbackClassifier.classify", "module": "src/cortex/rules/feedback.py", "kind": "new" },
   { "id": "cortex.rules.feedback._parse_feedback", "module": "src/cortex/rules/feedback.py", "kind": "new" },
   { "id": "cortex.rules.service.RuleService.record_use", "module": "src/cortex/rules/service.py", "kind": "keep" },
   { "id": "cortex.llm.providers.openai.OpenAIClient.chat", "module": "src/cortex/llm/providers/openai.py", "kind": "mod" },
   { "id": "cortex.llm.providers.openai._reasoning_delta", "module": "src/cortex/llm/providers/openai.py", "kind": "keep" }
  ],
  "edges": [
   { "from": "cortex.rules.attribution.AttributionRun.attribute", "to": "cortex.rules.feedback.FeedbackClassifier.classify" },
   { "from": "cortex.rules.attribution.AttributionRun.attribute", "to": "cortex.rules.service.RuleService.record_use" },
   { "from": "cortex.rules.feedback.FeedbackClassifier.classify", "to": "cortex.rules.feedback._parse_feedback" },
   { "from": "cortex.rules.feedback.FeedbackClassifier.classify", "to": "cortex.llm.providers.openai.OpenAIClient.chat" },
   { "from": "cortex.llm.providers.openai.OpenAIClient.chat", "to": "cortex.llm.providers.openai._reasoning_delta" }
  ]
 },
 "contracts": [
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.feedback.FeedbackClassifier.classify",
   "exits": [
    { "kind": "returns", "type": "Feedback: the attributed uses and at most one proposal", "handled_at": "each use goes to RuleService.record_use; the proposal is not acted on in this ticket" },
    { "kind": "absent", "type": "an empty Feedback: no rule was applied and nothing was proposed", "handled_at": "no service call, the rule list is returned unchanged" },
    { "kind": "raises", "type": "ValueError on malformed provider output, provider and circuit-breaker errors", "handled_at": "caught here, logged, nothing written; the caller sees an empty list" },
    { "kind": "effect", "type": "one billable provider call", "handled_at": null }
   ]
  },
  {
   "site": "cortex.rules.feedback.FeedbackClassifier.classify",
   "callee": "cortex.llm.providers.openai.OpenAIClient.chat",
   "exits": [
    { "kind": "returns", "type": "ChatResult whose message content is the JSON the prompt asked for", "handled_at": "handed to _parse_feedback" },
    { "kind": "absent", "type": "content None or blank", "handled_at": "_parse_feedback raises ValueError on it" },
    { "kind": "raises", "type": "the provider's error, or CircuitOpenError from the breaker", "handled_at": "not caught here: AttributionRun.attribute catches and writes nothing" },
    { "kind": "effect", "type": "one provider call, temperature 0", "handled_at": null }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.record_use",
   "exits": [
    { "kind": "returns", "type": "Rule with the recomputed confidence and state", "handled_at": "collected into the returned list" },
    { "kind": "absent", "type": "RuleNotFoundError when the rule vanished between classify and write", "handled_at": "caught here, logged, the remaining uses are still written" },
    { "kind": "raises", "type": "the repository's error (asyncpg) from the write", "handled_at": "caught here, logged, the remaining uses are still written" },
    { "kind": "effect", "type": "one rule_uses row plus one rules refresh per use", "handled_at": null }
   ]
  },
  {
   "site": "cortex.rules.feedback.FeedbackClassifier.classify",
   "callee": "cortex.rules.feedback._parse_feedback",
   "exits": [
    { "kind": "returns", "type": "Feedback with only the uses and proposals that parse and name a known rule", "handled_at": "returned as is" },
    { "kind": "absent", "type": "an empty Feedback when every entry was dropped", "handled_at": "returned as is, AttributionRun writes nothing" },
    { "kind": "raises", "type": "ValueError on malformed JSON or a missing uses key", "handled_at": "propagates to AttributionRun.attribute, which logs it and writes nothing" },
    { "kind": "effect", "type": "none, pure", "handled_at": null }
   ]
  },
  {
   "site": "cortex.llm.providers.openai.OpenAIClient.chat",
   "callee": "cortex.llm.providers.openai._reasoning_delta",
   "exits": [
    { "kind": "returns", "type": "the reasoning text of the delta", "handled_at": "wrapped into one inline think block in the content" },
    { "kind": "absent", "type": "None or empty for a provider/model that publishes no reasoning", "handled_at": "content left untouched" },
    { "kind": "raises", "type": "nothing expected: it reads attributes off a dict-like delta", "handled_at": "— nothing raised, nothing to handle" },
    { "kind": "effect", "type": "none, pure", "handled_at": null }
   ]
  }
 ]
}
```
