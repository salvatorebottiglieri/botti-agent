"""CI gate: compare a suite run against its versioned baseline (spec #85).

v1 rule: fail ONLY on a gross regression — a pass-rate drop at or beyond
two standard deviations of the run-to-run difference — or on a harness
error. Everything inside that band is model noise on small suites and must
not alarm: the E2/E3/E6 suites run ~30 tasks each, where the sd of the
difference between two independent runs at p=0.5 is sqrt(2*0.25/30) =
12.9pp, so the gate tolerates drops up to ~26pp before failing.

The logic is a pure function; the CI workflow (``.github/workflows/eval.yml``)
calls the tiny CLI below (``uv run python -m cortex.eval.gate``) per suite,
so the verdict logic lives here where ruff/mypy and the tests in
``tests/eval/test_gate.py`` cover it — no untested gate logic in YAML.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from math import sqrt
from pathlib import Path
from typing import Any

from cortex.eval.baseline import EvalBaseline, load_baseline

#: How many standard deviations of the run-to-run difference a drop must
#: reach before the gate fails (v1 gross-regression rule).
SIGMA_MULTIPLIER = 2.0


@dataclass(frozen=True)
class GateVerdict:
    """Outcome of comparing a run against its versioned baseline."""

    passed: bool
    reason: str


def sd_of_difference(run_task_count: int, baseline_task_count: int, pooled_pass_rate: float) -> float:
    """Standard deviation of the run-vs-baseline pass-rate difference.

    Each run is an independent binomial sample, so
    Var(p_hat_run - p_hat_base) = p(1-p)/n_run + p(1-p)/n_base with the
    pooled pass rate as the estimate of p. At p=0.5 with
    n_run = n_base = 30 this is sqrt(2*0.25/30) = 12.9pp — the spec's
    worked example.

    Empty suites carry no signal: returns 0.0, so a drop never exceeds the
    threshold on its own (an empty *run* is caught separately as a harness
    error).
    """
    if run_task_count <= 0 or baseline_task_count <= 0:
        return 0.0
    variance = pooled_pass_rate * (1.0 - pooled_pass_rate) * (
        1.0 / run_task_count + 1.0 / baseline_task_count
    )
    return sqrt(variance)


def compare_run_to_baseline(
    run_metrics: EvalBaseline,
    baseline: EvalBaseline | None,
    *,
    harness_error: str | None = None,
) -> GateVerdict:
    """Apply the v1 gate to one suite run.

    Args:
        run_metrics: This run's metrics, as recorded by the runner
            (``run_suite(..., baseline_path=...)`` writes an
            :class:`EvalBaseline`-shaped JSON whose task/pass counts are
            the run's).
        baseline: The versioned baseline (first known-good run) for the
            same suite and version; ``None`` when none exists yet.
        harness_error: Non-empty when the suite did not run to completion
            (crash, missing or malformed metrics, ...). A harness error
            fails the gate regardless of the numbers.

    Returns:
        ``passed=True`` when there is no baseline yet (the run records it),
        the run is at least as good as the baseline, or the drop is within
        2 sd of the run-to-run difference (model noise). ``passed=False``
        only on a harness error or a drop at or beyond 2 sd (gross
        regression).
    """
    if harness_error:
        return GateVerdict(False, f"harness error: {harness_error}")
    if baseline is None:
        return GateVerdict(True, "no versioned baseline yet — this run records it")
    if run_metrics.task_count <= 0:
        return GateVerdict(False, "harness error: run recorded no tasks")

    run_rate = run_metrics.metrics.pass_rate
    baseline_rate = baseline.metrics.pass_rate
    drop = baseline_rate - run_rate
    if drop <= 0.0:
        return GateVerdict(
            True,
            f"no regression: run pass rate {run_rate:.1%} >= baseline {baseline_rate:.1%}",
        )
    pooled = (run_metrics.pass_count + baseline.pass_count) / (
        run_metrics.task_count + baseline.task_count
    )
    sd = sd_of_difference(run_metrics.task_count, baseline.task_count, pooled)
    threshold = SIGMA_MULTIPLIER * sd
    if drop >= threshold:
        return GateVerdict(
            False,
            f"gross regression: pass rate dropped {drop:.1%} "
            f"({baseline_rate:.1%} -> {run_rate:.1%}), "
            f"at or beyond {SIGMA_MULTIPLIER:.0f} sd ({threshold:.1%})",
        )
    return GateVerdict(
        True,
        f"within noise: drop {drop:.1%} < {SIGMA_MULTIPLIER:.0f} sd ({threshold:.1%})",
    )


def _print_verdict(
    verdict: GateVerdict,
    *,
    suite: str,
    suite_version: str,
    run_pass_rate: float,
    baseline_pass_rate: float | None,
) -> int:
    """Print the verdict as one JSON line; return the process exit code."""
    payload: dict[str, Any] = {
        "suite": suite,
        "suite_version": suite_version,
        "passed": verdict.passed,
        "reason": verdict.reason,
        "run_pass_rate": run_pass_rate,
        "baseline_pass_rate": baseline_pass_rate,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if verdict.passed else 1


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: compare a run's metrics JSON to the versioned baseline."""
    parser = argparse.ArgumentParser(
        prog="python -m cortex.eval.gate",
        description="CI gate (spec #85): fail only on gross regressions (>= 2 sd) "
        "or harness errors; model noise on small suites never alarms.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare = subparsers.add_parser(
        "compare",
        help="Compare a run's metrics JSON against the versioned baseline.",
    )
    compare.add_argument(
        "--run",
        required=True,
        type=Path,
        help="This run's metrics JSON (the baseline file the runner recorded).",
    )
    compare.add_argument(
        "--baseline",
        type=Path,
        help="Versioned baseline JSON for the same suite+version; omit when none exists yet.",
    )
    compare.add_argument(
        "--harness-error",
        default=None,
        help="Non-empty when the suite did not run to completion (crash, missing metrics).",
    )
    args = parser.parse_args(argv)

    if args.command == "compare":
        run = load_baseline(args.run)
        if run is None:
            return _print_verdict(
                GateVerdict(
                    False,
                    f"harness error: run metrics JSON missing or malformed: {args.run}",
                ),
                suite="unknown",
                suite_version="",
                run_pass_rate=0.0,
                baseline_pass_rate=None,
            )
        baseline = load_baseline(args.baseline) if args.baseline is not None else None
        verdict = compare_run_to_baseline(run, baseline, harness_error=args.harness_error)
        return _print_verdict(
            verdict,
            suite=run.suite_name,
            suite_version=run.suite_version,
            run_pass_rate=run.metrics.pass_rate,
            baseline_pass_rate=baseline.metrics.pass_rate if baseline is not None else None,
        )
    return 2  # pragma: no cover - argparse enforces the subcommand


if __name__ == "__main__":
    sys.exit(main())
