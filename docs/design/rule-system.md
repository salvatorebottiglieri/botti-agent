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

## Ticket #169 — the feedback queue

_Written 2026-11-20, before the code. Signatures and contracts are in the
`json design` block below, under their own entries._

The queue is what makes attribution survive a provider outage or a restart: a
turn that had Rules in context and got a reply is written as a pending row, and
a later pass attributes it. Three shapes: the row (`QueuedFeedback`), the port
(`FeedbackQueue`), the Postgres implementation, plus `FeedbackDrain` that ties
the queue to #168's `AttributionRun`.

**No claim state.** Two drains may pick the same pending row and both classify
it; the partial unique index on `rule_uses (feedback_id, rule_id)` means the
second writes nothing, so the guarantee that matters — one turn never scores a
Rule twice — is held by the database, not by a locking protocol. A second drain
can therefore redo a classification that is still in flight: wasted work, never a
wrong score.

## Decision 2 — the queue needs no claim state

- **Choice**: three states (`pending`, `attributed`, `dropped`) and no
  `FOR UPDATE SKIP LOCKED` claim; idempotency comes from the partial unique
  index on `rule_uses (feedback_id, rule_id)`.
- **Rejected**: a fourth `claimed` state with a locking claim (it holds a
  connection across an LLM call, or needs a lease and a timeout); keeping the
  ticket's original "two drains cannot both claim the same row" criterion (it
  protects against a harm the index already prevents).
- **Consequences**: `RuleUse` gains a nullable `feedback_id`, the drain becomes a
  plain loop, and the ticket's criterion was rewritten to "no double scoring".
  The cost is a duplicated provider call when two drains race.
- **Owner sign-off**: 2026-11-20, owner in chat — "A" (to the two options, A =
  drop the claim criterion and rely on the unique index, B = add the `claimed`
  state).

## Ticket #170 — proposals and the conflict check

_Design, 2026-11-20, before the code. Signatures and contracts are in the
`json design` block._

The classifier already returns at most one proposal per turn (#168) and the run
ignores it. #170 is the short path from that proposal to a stored Rule, and the
one check that stands between them.

- **`cortex.rules.conflicts.ConflictCheck`** — one `chat()` call comparing the
  candidate with the active set. `True` when the candidate contradicts an active
  Rule. An empty active set returns `False` without a provider call: there is
  nothing to contradict, and an ordinary first rule must not cost a second call.
- **`AttributionRun` gains an optional third collaborator.** With a
  `ConflictCheck` the run applies the proposal after writing the uses: a
  dictated proposal goes through `create_dictated` (definitive, the dictation as
  its first use), a correction-born one through `create_from_correction`
  (staging). Without one the run behaves exactly as in #168, which is what keeps
  the existing tests and the eval-free path unchanged.
- **The budget is the service's.** `list_active()` already returns at most
  `CONTEXT_BUDGET` rules, so the run does not slice again; the check sees exactly
  what the context would see.
- **One handler covers both.** A conflict, a provider error or malformed output
  drops the proposal and never the uses: the turn's evidence is already written
  by then, and a proposal is the cheaper thing to lose.
- **Return value.** The run returns every Rule it touched — the ones whose use
  it recorded and the one it created — so a caller can refresh or announce them
  without a second read.

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
   },
   {
    "name": "cortex.rules.queue.FeedbackDrain.drain",
    "note": "#169 one pass over the pending rows; returns how many were attributed",
    "children": [
     {
      "name": "cortex.rules.queue.FeedbackQueue.list_pending",
      "note": "#169 oldest pending first",
      "children": []
     },
     {
      "name": "cortex.rules.service.RuleService.get",
      "note": "#169 the rules of that turn, by id",
      "children": []
     },
     {
      "name": "cortex.rules.attribution.AttributionRun.attribute",
      "note": "#168 unchanged",
      "children": []
     },
     {
      "name": "cortex.rules.queue.FeedbackQueue.mark_attributed",
      "note": "#169 only when attribute returned a list, never on None",
      "children": []
     },
     {
      "name": "cortex.rules.queue.FeedbackQueue.drop_stale",
      "note": "#169 rows past the validity window",
      "children": []
     }
    ]
   },
   {
    "name": "cortex.rules.conflicts.ConflictCheck.contradicts",
    "note": "#170 one provider call, only when the turn proposed something",
    "children": []
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
  },
  {
   "kind": "add",
   "path": "migrations/011_queued_feedback.sql",
   "note": "#169 the queue table and rule_uses.feedback_id with its partial unique index"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/queue.py",
   "note": "#169 QueuedFeedback, QueuedFeedbackStatus, FeedbackQueue, PostgresFeedbackQueue, FeedbackDrain"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/interfaces.py",
   "note": "#169 the FeedbackQueue port"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/models.py",
   "note": "#169 RuleUse.feedback_id, nullable"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/repository.py",
   "note": "#169 write and read rule_uses.feedback_id"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_queue.py",
   "note": "#169 the statements and the drain"
  },
  {
   "kind": "add",
   "path": "src/cortex/rules/conflicts.py",
   "note": "#170 the conflict check against the active set"
  },
  {
   "kind": "mod",
   "path": "src/cortex/rules/attribution.py",
   "note": "#170 the run applies the proposal after the conflict check"
  },
  {
   "kind": "add",
   "path": "tests/unit/rules/test_conflicts.py",
   "note": "#170 the check and the proposal path"
  },
  {
   "kind": "mod",
   "path": "tests/unit/rules/test_attribution.py",
   "note": "#170 the proposal path through the run"
  }
 ],
 "signatures": [
  {
   "module": "src/cortex/rules/feedback.py",
   "language": "python",
   "code": "@dataclass(frozen=True)\nclass AttributedUse:\n    rule_id: UUID\n    outcome: UseOutcome\n\n@dataclass(frozen=True)\nclass RuleProposal:\n    spec: RuleSpec\n    origin: RuleOrigin\n\n@dataclass(frozen=True)\nclass Feedback:\n    uses: list[AttributedUse] = field(default_factory=list)\n    proposals: list[RuleProposal] = field(default_factory=list)\n\ndef _parse_feedback(content: str, known_rule_ids: set[UUID]) -> Feedback: ...\n\nclass FeedbackClassifier:\n    def __init__(self, llm_client: LLMClient) -> None: ...\n    async def classify(self, rules: Sequence[Rule], assistant_turn: str, user_message: str) -> Feedback: ..."
  },
  {
   "module": "src/cortex/rules/attribution.py",
   "language": "python",
   "code": "class AttributionRun:\n    def __init__(self, classifier: FeedbackClassifier, service: RuleService) -> None: ...\n    async def attribute(self, rules: Sequence[Rule], session_id: UUID, assistant_turn: str, user_message: str, situation: str, feedback_id: UUID | None = None) -> list[Rule] | None: ..."
  },
  {
   "module": "src/cortex/llm/providers/openai.py",
   "language": "python",
   "code": "async def chat(self, messages: list[ChatMessage], *, tools: list[ToolDefinition] | None = None, generation_config: GenerationConfig | None = None) -> ChatResult: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def record_use(self, rule_id: UUID, session_id: UUID, situation: str, outcome: UseOutcome, feedback_id: UUID | None = None) -> Rule: ..."
  },
  {
   "module": "src/cortex/llm/providers/openai.py",
   "language": "python",
   "code": "def _reasoning_delta(delta: Any) -> str | None: ..."
  },
  {
   "module": "src/cortex/rules/queue.py",
   "language": "python",
   "code": "class QueuedFeedbackStatus(StrEnum):\n    PENDING = \"pending\"\n    ATTRIBUTED = \"attributed\"\n    DROPPED = \"dropped\"\n\n@dataclass(frozen=True)\nclass QueuedFeedback:\n    session_id: UUID\n    rule_ids: tuple[UUID, ...]\n    assistant_turn: str\n    user_reply: str\n    status: QueuedFeedbackStatus = QueuedFeedbackStatus.PENDING\n    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))\n    attributed_at: datetime | None = None\n    id: UUID | None = None\n\nclass FeedbackQueue(ABC):\n    @abstractmethod\n    async def enqueue(self, item: QueuedFeedback) -> QueuedFeedback: ...\n    @abstractmethod\n    async def list_pending(self, limit: int = 10) -> list[QueuedFeedback]: ...\n    @abstractmethod\n    async def mark_attributed(self, feedback_id: UUID, now: datetime) -> None: ...\n    @abstractmethod\n    async def drop_stale(self, cutoff: datetime) -> int: ...\n\nclass PostgresFeedbackQueue(FeedbackQueue):\n    def __init__(self, session_factory: Callable[[], AbstractAsyncContextManager[Any]] = DbSession) -> None: ...\n\nclass FeedbackDrain:\n    def __init__(self, queue: FeedbackQueue, run: AttributionRun, service: RuleService, *, clock: Callable[[], datetime] = _utcnow) -> None: ...\n    async def drain(self, limit: int = 10) -> int: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def get(self, rule_id: UUID) -> Rule | None: ..."
  },
  {
   "module": "src/cortex/rules/conflicts.py",
   "language": "python",
   "code": "class ConflictCheck:\n    def __init__(self, llm_client: LLMClient) -> None: ...\n    async def contradicts(self, candidate: RuleSpec, active: Sequence[Rule]) -> bool: ..."
  },
  {
   "module": "src/cortex/rules/attribution.py",
   "language": "python",
   "code": "class AttributionRun:\n    def __init__(self, classifier: FeedbackClassifier, service: RuleService, conflicts: ConflictCheck | None = None) -> None: ...\n    async def attribute(self, rules: Sequence[Rule], session_id: UUID, assistant_turn: str, user_message: str, situation: str, feedback_id: UUID | None = None) -> list[Rule] | None: ..."
  },
  {
   "module": "src/cortex/rules/service.py",
   "language": "python",
   "code": "async def list_active(self) -> list[Rule]: ...\nasync def create_dictated(self, session_id: UUID, spec: RuleSpec) -> Rule: ...\nasync def create_from_correction(self, spec: RuleSpec) -> Rule: ..."
  }
 ],
 "data_flow": {
  "nodes": [
   {
    "id": "cortex.rules.attribution.AttributionRun.attribute",
    "module": "src/cortex/rules/attribution.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.feedback.FeedbackClassifier.classify",
    "module": "src/cortex/rules/feedback.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.feedback._parse_feedback",
    "module": "src/cortex/rules/feedback.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.service.RuleService.record_use",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.llm.providers.openai.OpenAIClient.chat",
    "module": "src/cortex/llm/providers/openai.py",
    "kind": "mod"
   },
   {
    "id": "cortex.llm.providers.openai._reasoning_delta",
    "module": "src/cortex/llm/providers/openai.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.queue.FeedbackDrain.drain",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.list_pending",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.mark_attributed",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.queue.FeedbackQueue.drop_stale",
    "module": "src/cortex/rules/queue.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.service.RuleService.get",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.conflicts.ConflictCheck.contradicts",
    "module": "src/cortex/rules/conflicts.py",
    "kind": "new"
   },
   {
    "id": "cortex.rules.service.RuleService.list_active",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.service.RuleService.create_dictated",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   },
   {
    "id": "cortex.rules.service.RuleService.create_from_correction",
    "module": "src/cortex/rules/service.py",
    "kind": "keep"
   }
  ],
  "edges": [
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.feedback.FeedbackClassifier.classify"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.record_use"
   },
   {
    "from": "cortex.rules.feedback.FeedbackClassifier.classify",
    "to": "cortex.rules.feedback._parse_feedback"
   },
   {
    "from": "cortex.rules.feedback.FeedbackClassifier.classify",
    "to": "cortex.llm.providers.openai.OpenAIClient.chat"
   },
   {
    "from": "cortex.llm.providers.openai.OpenAIClient.chat",
    "to": "cortex.llm.providers.openai._reasoning_delta"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.queue.FeedbackQueue.list_pending"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.service.RuleService.get"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.attribution.AttributionRun.attribute"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.queue.FeedbackQueue.mark_attributed"
   },
   {
    "from": "cortex.rules.queue.FeedbackDrain.drain",
    "to": "cortex.rules.queue.FeedbackQueue.drop_stale"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.conflicts.ConflictCheck.contradicts"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.record_use"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.list_active"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.create_dictated"
   },
   {
    "from": "cortex.rules.attribution.AttributionRun.attribute",
    "to": "cortex.rules.service.RuleService.create_from_correction"
   }
  ]
 },
 "contracts": [
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.feedback.FeedbackClassifier.classify",
   "exits": [
    {
     "kind": "returns",
     "type": "Feedback: the attributed uses and at most one proposal",
     "handled_at": "each use goes to RuleService.record_use; the proposal is not acted on in this ticket"
    },
    {
     "kind": "absent",
     "type": "None: the classification failed and wrote nothing",
     "handled_at": "the caller sees None, leaves the queued row pending and retries it later"
    },
    {
     "kind": "raises",
     "type": "ValueError on malformed provider output, provider and circuit-breaker errors",
     "handled_at": "caught here by one handler, logged, nothing written, returns None so a queued row is not consumed"
    },
    {
     "kind": "effect",
     "type": "one billable provider call",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.feedback.FeedbackClassifier.classify",
   "callee": "cortex.llm.providers.openai.OpenAIClient.chat",
   "exits": [
    {
     "kind": "returns",
     "type": "ChatResult whose message content is the JSON the prompt asked for",
     "handled_at": "handed to _parse_feedback"
    },
    {
     "kind": "absent",
     "type": "content None or blank",
     "handled_at": "_parse_feedback raises ValueError on it"
    },
    {
     "kind": "raises",
     "type": "the provider's error, or CircuitOpenError from the breaker",
     "handled_at": "not caught here: AttributionRun.attribute catches and writes nothing"
    },
    {
     "kind": "effect",
     "type": "one provider call, temperature 0",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.record_use",
   "exits": [
    {
     "kind": "returns",
     "type": "Rule with the recomputed confidence and state",
     "handled_at": "collected into the returned list"
    },
    {
     "kind": "absent",
     "type": "RuleNotFoundError when the rule vanished between classify and write",
     "handled_at": "caught by the single handler, logged with the rule id, the remaining uses are still written"
    },
    {
     "kind": "raises",
     "type": "the repository's error (asyncpg) from the write",
     "handled_at": "caught by the same handler, logged, the remaining uses are still written"
    },
    {
     "kind": "effect",
     "type": "one rule_uses row plus one rules refresh per use",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.feedback.FeedbackClassifier.classify",
   "callee": "cortex.rules.feedback._parse_feedback",
   "exits": [
    {
     "kind": "returns",
     "type": "Feedback with only the uses and proposals that parse and name a known rule",
     "handled_at": "returned as is"
    },
    {
     "kind": "absent",
     "type": "an empty Feedback when every entry was dropped",
     "handled_at": "returned as is, AttributionRun writes nothing"
    },
    {
     "kind": "raises",
     "type": "ValueError on malformed JSON or a missing uses key",
     "handled_at": "propagates to AttributionRun.attribute, which logs it and writes nothing"
    },
    {
     "kind": "effect",
     "type": "none, pure",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.llm.providers.openai.OpenAIClient.chat",
   "callee": "cortex.llm.providers.openai._reasoning_delta",
   "exits": [
    {
     "kind": "returns",
     "type": "the reasoning text of the delta",
     "handled_at": "wrapped into one inline think block in the content"
    },
    {
     "kind": "absent",
     "type": "None or empty for a provider/model that publishes no reasoning",
     "handled_at": "content left untouched"
    },
    {
     "kind": "raises",
     "type": "nothing expected: it reads attributes off a dict-like delta",
     "handled_at": "\u2014 nothing raised, nothing to handle"
    },
    {
     "kind": "effect",
     "type": "none, pure",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.queue.FeedbackQueue.list_pending",
   "exits": [
    {
     "kind": "returns",
     "type": "the pending rows, oldest first",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "an empty list: nothing to attribute",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error (asyncpg)",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one read-only query",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.service.RuleService.get",
   "exits": [
    {
     "kind": "returns",
     "type": "the Rule, or None when it has been deleted since",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "None: the id is skipped, the rest of the row is still processed",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one read per rule id",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.attribution.AttributionRun.attribute",
   "exits": [
    {
     "kind": "returns",
     "type": "the refreshed rules, or None when the classification failed",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "None: the row stays pending and a later pass retries it",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "nothing: the run catches its own failures",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one provider call per pending row that still has rules",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.queue.FeedbackQueue.mark_attributed",
   "exits": [
    {
     "kind": "returns",
     "type": "None, called only when the run returned a list (not None)",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "the row was already attributed by another drain: a no-op update",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error, logged; the row stays pending",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one update",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.queue.FeedbackDrain.drain",
   "callee": "cortex.rules.queue.FeedbackQueue.drop_stale",
   "exits": [
    {
     "kind": "returns",
     "type": "the number of rows dropped",
     "handled_at": "the drain loop"
    },
    {
     "kind": "absent",
     "type": "zero: nothing is old enough",
     "handled_at": "no work that pass"
    },
    {
     "kind": "raises",
     "type": "the repository's error, logged; the pass still reports its attributions",
     "handled_at": "logged, the row stays pending, the drain continues"
    },
    {
     "kind": "effect",
     "type": "one update",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.conflicts.ConflictCheck.contradicts",
   "exits": [
    {
     "kind": "returns",
     "type": "True or False from the provider's verdict",
     "handled_at": "True drops the proposal, False lets it be created"
    },
    {
     "kind": "absent",
     "type": "an empty active set: nothing to contradict",
     "handled_at": "False without any provider call, so a first rule costs one call, not two"
    },
    {
     "kind": "raises",
     "type": "the provider's error, or malformed JSON",
     "handled_at": "logged, the proposal is dropped, the uses are still written"
    },
    {
     "kind": "effect",
     "type": "one provider call, and only when there is a proposal",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.list_active",
   "exits": [
    {
     "kind": "returns",
     "type": "the active rules, already capped at the context budget",
     "handled_at": "the proposal is stored from it"
    },
    {
     "kind": "absent",
     "type": "no active rule: the check short-circuits",
     "handled_at": "nothing to create"
    },
    {
     "kind": "raises",
     "type": "the repository's error",
     "handled_at": "caught by the same handler as the conflict check: the proposal is dropped, the uses stand"
    },
    {
     "kind": "effect",
     "type": "one read per sweep",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.create_dictated",
   "exits": [
    {
     "kind": "returns",
     "type": "the new Rule, definitive with the dictation as its first use",
     "handled_at": "the proposal is stored from it"
    },
    {
     "kind": "absent",
     "type": "none: the branch is taken only for a dictated proposal",
     "handled_at": "nothing to create"
    },
    {
     "kind": "raises",
     "type": "ValueError on blank text, the repository's error",
     "handled_at": "caught by the same handler as the conflict check: the proposal is dropped, the uses stand"
    },
    {
     "kind": "effect",
     "type": "one rules row plus one use row",
     "handled_at": null
    }
   ]
  },
  {
   "site": "cortex.rules.attribution.AttributionRun.attribute",
   "callee": "cortex.rules.service.RuleService.create_from_correction",
   "exits": [
    {
     "kind": "returns",
     "type": "the new Rule, staging at the prior",
     "handled_at": "the proposal is stored from it"
    },
    {
     "kind": "absent",
     "type": "none: the branch is taken only for a correction-born proposal",
     "handled_at": "nothing to create"
    },
    {
     "kind": "raises",
     "type": "ValueError on blank text, the repository's error",
     "handled_at": "caught by the same handler as the conflict check: the proposal is dropped, the uses stand"
    },
    {
     "kind": "effect",
     "type": "one rules row",
     "handled_at": null
    }
   ]
  }
 ]
}
```
