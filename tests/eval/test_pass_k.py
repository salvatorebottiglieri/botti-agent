"""Unit tests for pass^k consistency semantics (T4, E2).

pass^k is the probability that all k trials of a task succeed — the
consistency metric for loop tasks (CONTEXT.md: pass^k). v1 runs each task
once (k=1), so pass^1 over a single-run result must equal the suite pass
rate; the k>=3 phase computes pass^k from repeated suite runs, which the
caller supplies to the pure helper (run_suite stays single-trial).
"""

from __future__ import annotations

import pytest

from cortex.eval.metrics import compute_pass_k


class TestComputePassK:
    """The pure pass^k helper follows all-of-k semantics."""

    def test_k1_equals_suite_pass_rate(self) -> None:
        """One trial per task: pass^1 == pass_count / task_count."""
        results = {"a": [True], "b": [True], "c": [False], "d": [False]}
        assert compute_pass_k(results, 1) == 0.5

    def test_k1_all_passing(self) -> None:
        results = {"a": [True], "b": [True]}
        assert compute_pass_k(results, 1) == 1.0

    def test_k1_none_passing(self) -> None:
        results = {"a": [False], "b": [False]}
        assert compute_pass_k(results, 1) == 0.0

    def test_k2_requires_both_trials_pass(self) -> None:
        """all-of-k: a task passes only if every one of its k trials passed."""
        results = {
            "a": [True, True],
            "b": [True, False],  # flaky: fails the k=2 bar
            "c": [False, True],  # flaky: fails the k=2 bar
        }
        assert compute_pass_k(results, 2) == pytest.approx(1 / 3)

    def test_k3_consistency(self) -> None:
        results = {
            "a": [True, True, True],
            "b": [True, True, False],
            "c": [False, False, True],
        }
        assert compute_pass_k(results, 3) == pytest.approx(1 / 3)

    def test_task_with_fewer_trials_than_k_does_not_pass(self) -> None:
        """Insufficient trials cannot demonstrate pass^k; count as not passing."""
        results = {"a": [True], "b": [True, True]}
        assert compute_pass_k(results, 2) == 0.5

    def test_empty_results_is_zero(self) -> None:
        """Like pass_rate on an empty suite, pass^k is 0.0, never NaN."""
        assert compute_pass_k({}, 1) == 0.0
        assert compute_pass_k({}, 3) == 0.0

    def test_k_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="k"):
            compute_pass_k({"a": [True]}, 0)
        with pytest.raises(ValueError, match="k"):
            compute_pass_k({"a": [True]}, -1)

    def test_repeated_suite_runs_feed_the_helper(self) -> None:
        """Two runs of a 3-task suite: pass^2 over per-task trial lists."""
        run1 = {"a": True, "b": False, "c": True}
        run2 = {"a": True, "b": True, "c": True}
        by_task = {name: [run1[name], run2[name]] for name in run1}
        assert compute_pass_k(by_task, 2) == pytest.approx(2 / 3)
        # The same data seen as a single run is pass^1 == pass rate of run1.
        assert compute_pass_k({name: [run1[name]] for name in run1}, 1) == pytest.approx(2 / 3)
