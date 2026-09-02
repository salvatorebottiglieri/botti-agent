# Cortex (`botti-agent`)

<p align="center">
  <img src="assets/cortex-mascot-thinking.png" alt="Cortex — the proactive agent mascot" width="340">
</p>

**A from-scratch agent harness in Python** — an agent runtime with an explicit
plan → act → verify loop, a decoupled event bus, trustworthy memory, an
extensible tool registry, and distributed sensor agents. Not a prompt-chaining
script: the machine underneath one.

> **Vision:** the system should be like water flowing into a river — no rigid
> boundaries, modules emerge and cooperate as the flow requires. An ecosystem of
> modules, not a single agent entity.

---

## The agentic loop

The heart of the system is an explicit **Think → Act → Observe → Respond** cycle.
An `AgentLoop` orchestrates three components, each injected so it can be tested in
isolation:

```
┌──────────────┐
│   RECEIVE    │  ← user message or background goal
└──────┬───────┘
       ▼
┌──────────────┐
│   CONTEXT    │  ← ContextBuilder: history + trusted facts + tool schemas + ambient
└──────┬───────┘
       ▼
┌──────────────┐     ┌─────────────┐
│    THINK     │────►│    ACT      │  ← LoopExecutor: run tools (seq/parallel),
│  (Reasoner)  │     │  (Tools)    │    feed results — including errors — back
└──────┬───────┘     └──────▲──────┘
       │   Decision         │ tool calls
       │   ┌────────────────┘
       ▼   ▼
┌──────────────┐
│   RESPOND    │  ← final response, or loop again
└──────────────┘
```

- **`ContextBuilder`** assembles the reasoning context every turn: recent
  conversation, relevant facts from memory, available tool schemas, and ambient
  signals (time, location).
- **`Reasoner`** turns LLM output into a **typed `Decision`** — `RESPOND`,
  `EXECUTE_TOOLS`, or `ASK_QUESTION` — so malformed model output fails loudly
  instead of silently corrupting the loop.
- **`LoopExecutor`** runs tool calls sequentially or in parallel (with a
  concurrency cap), emits an event per call for monitoring, and returns results —
  errors included — as observations for the next turn.

Source: [`src/cortex/agentic/`](src/cortex/agentic/) —
`loop.py`, `reasoner.py`, `executor.py`, `context_builder.py`.

## Two operating modes, one loop

- **Chat** — interactive, multi-turn; may use zero tools; terminates when the
  reasoner returns text.
- **Goals** — long-running background tasks that run across many iterations and
  can be **paused and resumed**.

See [`docs/AGENTIC_LOOP.md`](docs/AGENTIC_LOOP.md) for the full design and worked
examples.

## What makes it a harness, not a demo

- **Extensible tool registry** — capabilities are added to a registry; the core
  loop never changes to gain a new tool.
- **Event bus ("The River")** — modules never call each other directly. They
  communicate through an asyncio event bus with **salience filtering**, which
  keeps the system decoupled and observable. See
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- **Reliability designed in, not bolted on:**
  - bounded iterations with a `MaxIterationsError` (chat ≤ 20, goals ≤ 100) —
    no runaway loops
  - a **circuit breaker** ([ADR-0009](docs/adr/0009-circuit-breaker.md))
  - **typed reasoner parsing** ([ADR-0005](docs/adr/0005-typed-reasoner-parsing.md))
  - per-call event emission for monitoring and tracing
- **Trustworthy memory** — a dedicated memory service exposes facts as ambient
  context; a learning module turns events into patterns over time.
- **Distributed sensor agents ("minions")** — organs that stream real-world data
  (battery, network, screen, keyboard) over **MQTT with encrypted events**.
  `laptop-minion/` is a full sub-package with its own tests.

## Design decisions (ADRs)

Every non-obvious call is written down with its rationale in
[`docs/adr/`](docs/adr/) — this is where the trade-offs live:

| ADR | Decision |
|-----|----------|
| [0002](docs/adr/0002-async-generator-as-streaming-seam.md) | Async generator as the streaming seam |
| [0003](docs/adr/0003-split-memory-service.md) | Split the memory service |
| [0004](docs/adr/0004-persistent-goals.md) | Persistent, resumable goals |
| [0005](docs/adr/0005-typed-reasoner-parsing.md) | Typed reasoner parsing |
| [0008](docs/adr/0008-embedding-semantic-search.md) | Embedding-based semantic fact search |
| [0009](docs/adr/0009-circuit-breaker.md) | Circuit breaker for resilience |
| [0010](docs/adr/0010-per-module-config.md) | Per-module configuration |
| [0011](docs/adr/0011-public-get-or-create-session.md) | Public get_or_create_session on InteractionService |
| [0012](docs/adr/0012-system-invariants.md) | System invariants — CircuitBreaker + SessionPolicy |
| [0018](docs/adr/0018-ask-user-tool-and-token-streaming.md) | ask_user tool + token streaming (refines 0002, supersedes 0005's `[QUESTION]`) |

(Full list, 0001–0018, in the folder.)

## Architecture at a glance

```
INPUT SOURCES  ──►  Event Bus ("The River")  ──►  Modules
 • chat/tools/goals      asyncio queue,          interaction · memory · learning
 • minions (MQTT,        salience-filtered       tools · execution · sessions
   encrypted events)                             agentic (the loop) · llm · api
```

Modules live under [`src/cortex/`](src/cortex/): `agentic`, `events`, `memory`,
`tools`, `execution`, `interaction`, `sessions`, `services`, `llm`, `minions`,
`api`, `db`.

## Tech

Python · Pydantic · AsyncIO · PostgreSQL · FastAPI · MQTT · Docker
(multi-container). LLM provider is pluggable (OpenAI by default; configured in
`config.yaml`).

## Running it

```bash
cp .env.example .env            # set OPENAI_API_KEY and secrets
# adjust config.yaml for DB / LLM / MQTT / per-module settings
docker compose up -d --build    # Postgres + broker + app
```

The API serves on `:8000`. See `config.yaml` for database, LLM, MQTT, and
per-module settings.

---

> Cortex is a personal system — not something serving millions of tool calls a
> day. But it's built at the depth where every hard call is mine to make and
> defend: the loop, the event system, memory and learning, the tool registry, the
> minion protocol, and the tests.
