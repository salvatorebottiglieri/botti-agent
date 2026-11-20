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

## Elements

| ID | Kind | Code | Source |
|---|---|---|---|
| Loop | module | src.cortex.agentic.loop | `src/cortex/agentic/loop.py` |
| Bundle | entity | MemoryContext | `src/cortex/agentic/models.py:102` |
| Classifier | symbol | src.cortex.rules.feedback.FeedbackClassifier.classify | `src/cortex/rules/feedback.py` |
| Attribution | symbol | src.cortex.rules.attribution.AttributionRun.attribute | `src/cortex/rules/attribution.py` |
| Service | symbol | src.cortex.rules.service.RuleService.record_use | `src/cortex/rules/service.py:92` |
| Render | symbol | render_rules | `src/cortex/rules/render.py:21` |
| Provider | external | https://github.com/openai/openai-python | `src/cortex/llm/providers/openai.py` |

## Flow

```mermaid
flowchart LR
  User[User replies] --> Loop[Loop answers the turn]
  Loop --> Bundle[Rules in the bundle]
  Loop --> Reply[Reply delivered]
  Reply --> Run[Attribution run]
  Run -->|had rules and a reply| Classifier[Feedback classifier]
  Bundle --> Classifier
  Bundle --> Render[render_rules into the prompt]
  Classifier -->|per applied Rule| Service[RuleService.record_use]
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

## Notes

Nothing in `src/` calls `AttributionRun` or `FeedbackDrain` yet: the wiring into a turn is #172, and
until then the model describes the component, not the running system. The enqueue side of the queue (writing the row after a turn) is #172.
