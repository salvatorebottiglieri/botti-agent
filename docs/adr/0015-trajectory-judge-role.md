# Trajectory Judge is never the pass/fail oracle

For loop Eval Tasks, the annotated goal state (sandbox filesystem, database) is the only pass/fail
oracle — deterministic and executable, per the outcome-based grading principle every serious agent
benchmark follows (τ-bench, OSWorld, SWE-bench). The LLM Trajectory Judge runs only on failed tasks,
producing partial credit (how far the agent progressed) and a diagnosis (wrong tool, bad arguments,
wasted iterations, policy violation). It is deliberately not used as the primary verdict: LLM judges
are calibrated instruments with position/verbosity/self-enhancement biases, and keeping pass/fail
deterministic keeps pass^k, cost, and latency comparable across runs. Alternatives considered and
rejected: judge as primary verdict (non-deterministic pass/fail, needs calibration before trust) and
two independent scores (state score + judge score, harder to interpret per task). Literature:
Agent-as-a-Judge (arXiv:2410.10934), Anthropic "Demystifying evals for AI agents".
