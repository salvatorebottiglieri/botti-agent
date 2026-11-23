---
feature: rule-system
area: rules
entities: [Rule]
issue: https://github.com/salvatorebottiglieri/botti-agent/issues/168
---

# Model: rule-system

The learned-rule system as built through #168: a turn becomes evidence about
the Rules it applied. Spec: `docs/specs/rule-system.md`; design:
`docs/design/rule-system.md`.

## Lifecycle inventory

| Entity | Moves in time? | Statechart |
|---|---|---|
| `Rule` | yes — `state` enum plus confidence-driven transitions | required, see [Rule](#rule) |
| `QueuedFeedback` | yes — `status` enum (`pending`, `attributed`, `dropped`) driven by the drain | required, see [QueuedFeedback](#queuedfeedback) |
| `RuleUse` | no — an immutable record of one application | — |
| `Feedback` | no — it exists only inside one attribution run | — |
| `SplitProposal` | no — it exists only inside one split, handed straight to the service | — |

## Elements

| ID | Kind | Code | Source |
|---|---|---|---|
| Loop | module | src.cortex.agentic.loop | `src/cortex/agentic/loop.py` |
| Bundle | entity | MemoryContext | `src/cortex/agentic/models.py:102` |
| Classifier | symbol | src.cortex.rules.feedback.FeedbackClassifier.classify | `src/cortex/rules/feedback.py` |
| Attribution | symbol | src.cortex.rules.attribution.AttributionRun.attribute | `src/cortex/rules/attribution.py` |
| Service | symbol | src.cortex.rules.service.RuleService.record_use | `src/cortex/rules/service.py:92` |
| Split | symbol | src.cortex.rules.splits.SplitProposer.propose | `src/cortex/rules/splits.py` |
| Enqueue | symbol | src.cortex.rules.queue.enqueue_turn | `src/cortex/rules/queue.py` |
| Drain | symbol | src.cortex.rules.queue.FeedbackDrain.drain | `src/cortex/rules/queue.py` |
| Render | symbol | render_rules | `src/cortex/rules/render.py:21` |
| Provider | external | https://github.com/openai/openai-python | `src/cortex/llm/providers/openai.py` |

## Flow

```mermaid
flowchart LR
  User[User replies] --> Loop[Loop answers the turn]
  Loop --> Bundle[Rules in the bundle]
  Loop --> Reply[Reply delivered]
  Reply --> Enqueue[enqueue_turn]
  Enqueue -->|one pending row| Queue[(queued_feedback)]
  Queue --> Drain[FeedbackDrain]
  Drain --> Run[Attribution run]
  Run -->|had rules and a reply| Classifier[Feedback classifier]
  Bundle --> Classifier
  Bundle --> Render[render_rules into the prompt]
  Classifier -->|per applied Rule| Service[RuleService.record_use]
  Run -->|a scope correction was attributed| Split[Split proposer]
  Split -->|two children and one assignment| Service
  Service --> Score[Recomputed confidence and state]
  Run -->|classifier or storage failed| Nothing[Nothing written, caller unaffected]
```

## Sequence

```mermaid
sequenceDiagram
  participant Caller
  participant Run as AttributionRun
  participant Classifier as Feedback classifier
  participant Provider
  participant Service as RuleService

  Caller->>Run: attribute(rules, session, turn, reply, situation)
  Run->>Classifier: classify(rules, turn, reply)
  Classifier->>Classifier: skip when no rules or a blank reply
  Classifier->>Provider: one chat(), temperature 0
  Provider-->>Classifier: JSON, reasoning in an inline think block
  Classifier-->>Run: Feedback — uses plus at most one proposal
  loop each attributed rule
    Run->>Service: record_use(rule, session, situation, outcome)
    Service-->>Run: Rule, confidence and state recomputed
  end
  Run-->>Caller: the refreshed rules, or the given ones when nothing was attributed
```

## State machines

### Rule

Slice A's machine; #168 is the first thing that drives it from a real turn.

```mermaid
stateDiagram-v2
  %% Rule
  [*] --> staging : a correction reveals a Rule
  [*] --> definitive : the user dictates a Rule
  staging --> definitive : confidence >= 0.9 and 3 positive uses from 2 sessions
  definitive --> staging : confidence < 0.7
  staging --> archived : confidence < 0.3
  definitive --> archived : confidence < 0.3
  staging --> archived : the user archives
  definitive --> archived : the user archives
  staging --> archived : a scope correction splits it
  definitive --> archived : a scope correction splits it
  archived --> [*]
```

### QueuedFeedback

A row waits, is attributed, or ages out. A failed classification keeps it
waiting: the queue exists so that a provider outage costs a delay.

```mermaid
stateDiagram-v2
  %% QueuedFeedback
  [*] --> pending : a turn with rules and a reply is enqueued
  pending --> attributed : the drain attributed it
  pending --> pending : the classifier was unavailable
  pending --> dropped : older than the Rule validity window
  attributed --> [*]
  dropped --> [*]
```

## Model changes

| # | Element | Change | Why | Approval |
|---|---|---|---|---|
| 1 | `QueuedFeedback` | - | The design lists the queue; #168 delivered the classifier and the run only | https://github.com/salvatorebottiglieri/botti-agent/issues/166#issuecomment-7038922357 |
| 2 | `QueuedFeedback` | + | #169 builds the queue: the row moves pending → attributed, stays pending when the classifier is unavailable, and is dropped past the validity window | https://github.com/salvatorebottiglieri/botti-agent/issues/169 |
| 3 | `Rule` | + | #171 splits a rule through the service instead of penalising it: a scope correction archives the parent and creates two children, so the machine gains the transition that leaves a rule for a Split | https://github.com/salvatorebottiglieri/botti-agent/issues/171 |
| 4 | `QueuedFeedback` | + | #172 wires the enqueue into both chat endpoints and the drain into the composition root, so the row is written by a real turn and the pending → attributed transition happens in the running system; the rules of the row are the active set read before the turn (design Decision 4, owner's answer "A" of 2026-11-23) | https://github.com/salvatorebottiglieri/botti-agent/issues/172 |

## Notes

Since #172 the model describes the running system: both chat endpoints read the active set before the
turn and enqueue the row after the answer, and `main.py` starts the drain beside the evidence sweep.
The eval runner calls `AgentLoop.stream_chat` directly and never enqueues, so an eval still learns nothing.
