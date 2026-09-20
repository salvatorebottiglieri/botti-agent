# Eval system

> **Current truth** for the in-repo evaluation harness: suites, grading, metrics
> and the CI gate. Decisions: ADR-0014 (custom harness, not an external
> framework), ADR-0015 (judge is never the oracle; G-Eval rubric), ADR-0016
> (grader v2). Background research: `docs/research/agent-evaluation-literature.md`.

## Sources of truth

- `CONTEXT.md` — vocabulary (Eval Task, pass^k, Trajectory Judge, Partial Credit,
  Regression Eval, Eval Baseline, Golden Set).
- `docs/adr/0014`, `0015`, `0016` — why.
- `tests/eval/fixtures/*_manifest.yaml` — the versions that define a pass.
- This document — the system as a whole. **Keep it in sync.**

## Suites

| Suite | ID | Tasks | What it measures |
|---|---|---|---|
| Evidence invariants | E1 | n/a (deterministic) | The evidence-system laws as executable checks |
| Loop | E2 | 30 | The real agent loop under stress: tool selection, arguments, multi-turn, safety negatives |
| Refusal | E6 | 13 | Refusal policy: must-refuse harmful/PII/policy prompts paired with must-comply benign prompts (over-refusal guard) |

The capability suite (raw single-message model questions) was **removed** — it
tested a surface the personal-agent use case doesn't need. Loop + refusal cover
the surfaces that matter (ADR-0016). A tombstone for the term lives in `CONTEXT.md`.

E1 is `importorskip`-gated on issue #83: it runs cleanly today and goes live the
moment `cortex.memory.evidence_store` lands, with no workflow changes.

## Harness

`src/cortex/eval/`, pytest-native (ADR-0014):

| Module | Role |
|---|---|
| `fixtures.py` | YAML suite schema + loader (scripted user turns + annotated goal state) |
| `sandbox.py` | Per-task `mkdtemp` sandbox; meta tools wrapped so file ops resolve inside it |
| `runner.py` | Composes the real `AgentLoop` inside the sandbox; injects the LLM client |
| `grader.py` | Deterministic goal-state grading (below) |
| `judge.py` | Trajectory Judge for **failed** tasks only (below) |
| `metrics.py` | Per-task and per-suite metrics from the LoopEvent transcript; `compute_pass_k` |
| `baseline.py` | Versioned per-suite baseline JSON (record/load) |
| `gate.py` | CI gate: gross-regression rule (below) |

The harness adds no new seams inside existing modules; the only injected
dependency is the LLM client, so tests run against a fake while real runs use the
factory.

## Grading (v2, ADR-0016)

The annotated goal state — sandbox filesystem, database, or exact answer — is the
only pass/fail oracle. `GRADING_SCHEMA_VERSION = 2`:

- `equals` tolerates trailing whitespace.
- `json_equals` compares JSON semantically.
- Refusal uses a semantic check (refusal keyword + absence of forbidden
  patterns) instead of an accept-list of phrasings.
- The comply `answer` list is matched as a substring any-of after
  case/whitespace normalization, so natural-language wrappers ("X is Y", "X
  stands for Y") pass without enumerating phrasings.

Suites record `grading_version` in their manifest; the gate refuses to compare
baselines across a version bump.

## Trajectory Judge (ADR-0015)

An LLM **distinct from the generator** runs only on failed tasks and produces
partial credit + a per-dimension diagnosis. It never emits pass/fail — the goal
state already decided that. Scoring is G-Eval structured (`RUBRIC_VERSION = "1"`):

1. criteria written before judging, one per dimension (tool selection, argument
   correctness, iteration efficiency, policy compliance) with positive/negative
   indicators;
2. chain-of-thought over the trajectory against the criteria;
3. form-fill Likert score per dimension;
4. multiple sampling rounds averaged.

Position-bias guard: the same transcript is judged in both dimension orders; each
dimension's forward/reverse scores must agree within one Likert point, otherwise
the verdict is marked inconsistent and escalated to a human. Length-control is
waived (single trajectory, no candidate pair); the efficiency dimension bounds
verbosity and the human audit set measures judge-vs-human agreement (ADR-0015).

## Manifests

Each suite pins every version that defines a pass, so drift is attributable:
`suite.name`, `suite.version`, `prompt_version` (SHA-256 of the reasoner system
prompt for loop), `model`, `grading_version`, `rubric_version`. Bump the prompt
version whenever prompt text changes; bump the suite version on any manifest
change.

## Metrics

Recorded from the LoopEvent transcript: `iterations`, tool names used, per-tool
success flags, accumulated `usage`, `latency_ms`, and — when a `ModelPricing` is
pinned — derived USD cost. Consistency is reported as **pass^k** (probability all
k trials of a task succeed), a stronger signal than average success for a
user-relying agent.

## CI gate

`.github/workflows/eval.yml`:

- **E1 (PR gate)** — path-filtered to `src/cortex/eval/**` and `tests/eval/**`.
- **E2/E6 (nightly cron + manual)** — run the loop and refusal suites, record
  each suite's metrics as a versioned baseline JSON, and compare against the
  stored baseline via `gate.py`.

Gate rule (v1): fail **only** on a gross regression — a pass-rate drop at or
beyond two standard deviations of the run-to-run difference — or a harness
error. Everything inside that band is model noise on small suites and must not
alarm. The gate logic is a pure function covered by `tests/eval/test_gate.py`; the
workflow calls the tiny `python -m cortex.eval.gate` CLI, so no untested verdict
logic lives in YAML.

Baselines live in the GitHub Actions cache (`eval-baseline-v1`) as one JSON per
suite-version in v1; the first successful nightly records them. Git-versioned
baselines under `tests/eval/` are the future path.

## Golden Set privacy

Suite fixtures and goal states are ground truth kept private — never fed to
prompts, training, or the learning loop.
