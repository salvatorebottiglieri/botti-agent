# Custom in-repo evaluation harness instead of an external framework

Cortex's evaluation system is built as a custom pytest-native harness in `src/cortex/eval/` plus
`tests/eval/`, rather than adopting DeepEval, promptfoo, or LangSmith. The eval surface is too custom
for off-the-shelf tooling — deterministic evidence invariants, τ-bench-style loop tasks with pass^k and
cost/latency tracking, and a trajectory judge that only grades failures — and the repo is already
pytest-native with CI wired to pytest. External frameworks were evaluated (DeepEval's agentic metrics,
promptfoo's quality gates, LangSmith's experiments) and rejected: each would add a dependency and an
abstraction layer over surfaces they don't model, and none covers the evidence-machinery invariants.
Design rationale: `docs/research/agent-evaluation-literature.md`.
