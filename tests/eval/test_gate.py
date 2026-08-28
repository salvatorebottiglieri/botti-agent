"""Tests for the CI gate (T8): the >= 2 sd gross-regression rule.

The gate compares a suite run against its versioned baseline. v1 fails only
on a gross regression — a pass-rate drop at or beyond 2 sd of the
run-to-run difference (12.9pp per 30-task suite at p=0.5, so a ~26pp
threshold) — or on a harness error; everything inside the band is model
noise and must pass. The workflow YAML's syntactic validity is also pinned
here (yaml.safe_load) since actionlint is not available in this repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cortex.eval.baseline import EvalBaseline, record_baseline
from cortex.eval.gate import compare_run_to_baseline, main, sd_of_difference
from cortex.eval.metrics import SuiteMetrics

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "eval.yml"


def _baseline(*, task_count: int, pass_count: int) -> EvalBaseline:
    """A versioned baseline (or run) with the given binomial outcome."""
    return EvalBaseline(
        suite_name="loop",
        suite_version="1.0.0",
        task_count=task_count,
        pass_count=pass_count,
        metrics=SuiteMetrics(task_count=task_count, pass_count=pass_count),
        created_at="2026-01-01T00:00:00+00:00",
    )


class TestSDOfDifference:
    """The run-to-run difference sd follows binomial variance math."""

    def test_30_tasks_at_p50_is_12_9pp(self) -> None:
        """sqrt(2*0.25/30) = sqrt(1/60) = 0.1291 — the spec's worked example."""
        assert sd_of_difference(30, 30, 0.5) == pytest.approx(0.1291, abs=1e-4)

    def test_unequal_suite_sizes(self) -> None:
        """sqrt(0.25/30 + 0.25/60) = sqrt(0.0125) = 0.1118."""
        assert sd_of_difference(30, 60, 0.5) == pytest.approx(0.1118, abs=1e-4)

    def test_empty_suite_has_no_signal(self) -> None:
        assert sd_of_difference(0, 30, 0.5) == 0.0
        assert sd_of_difference(30, 0, 0.5) == 0.0


class TestCompareRunToBaseline:
    """The gate: >= 2 sd gross regression or harness error fails; noise passes."""

    def test_drop_of_26pp_on_30_tasks_fails(self) -> None:
        """A 26.7pp drop (15/30 -> 7/30) is beyond 2 sd (~25pp) -> fail."""
        verdict = compare_run_to_baseline(
            _baseline(task_count=30, pass_count=7),
            _baseline(task_count=30, pass_count=15),
        )
        assert verdict.passed is False
        assert "gross regression" in verdict.reason

    def test_drop_of_20pp_on_30_tasks_passes_as_noise(self) -> None:
        """A 20pp drop (15/30 -> 9/30) is inside 2 sd (~25pp) -> noise, pass."""
        verdict = compare_run_to_baseline(
            _baseline(task_count=30, pass_count=9),
            _baseline(task_count=30, pass_count=15),
        )
        assert verdict.passed is True
        assert "noise" in verdict.reason

    def test_small_suites_never_alarm_on_noise(self) -> None:
        """A 2-task swing on an 8-task suite is inside 2 sd (~43pp) -> pass."""
        verdict = compare_run_to_baseline(
            _baseline(task_count=8, pass_count=5),
            _baseline(task_count=8, pass_count=7),
        )
        assert verdict.passed is True

    def test_harness_error_fails_regardless_of_numbers(self) -> None:
        verdict = compare_run_to_baseline(
            _baseline(task_count=30, pass_count=30),
            _baseline(task_count=30, pass_count=30),
            harness_error="suite crashed before grading",
        )
        assert verdict.passed is False
        assert "harness error" in verdict.reason

    def test_zero_failure_run_passes(self) -> None:
        """Baseline and run both perfect: no drop, no alarm."""
        verdict = compare_run_to_baseline(
            _baseline(task_count=30, pass_count=30),
            _baseline(task_count=30, pass_count=30),
        )
        assert verdict.passed is True

    def test_no_baseline_yet_passes_and_records(self) -> None:
        """First known-good run: nothing to compare against, always passes."""
        verdict = compare_run_to_baseline(_baseline(task_count=30, pass_count=20), None)
        assert verdict.passed is True
        assert "records it" in verdict.reason

    def test_run_better_than_baseline_passes(self) -> None:
        verdict = compare_run_to_baseline(
            _baseline(task_count=30, pass_count=20),
            _baseline(task_count=30, pass_count=15),
        )
        assert verdict.passed is True
        assert "no regression" in verdict.reason

    def test_empty_run_is_a_harness_error(self) -> None:
        verdict = compare_run_to_baseline(
            _baseline(task_count=0, pass_count=0),
            _baseline(task_count=30, pass_count=15),
        )
        assert verdict.passed is False
        assert "harness error" in verdict.reason


class TestGateCLI:
    """The CLI the workflow can call via `uv run python -m cortex.eval.gate`."""

    def _write_run(
        self,
        tmp_path: Path,
        *,
        pass_count: int,
        task_count: int = 30,
        name: str = "run.json",
    ) -> Path:
        path = tmp_path / name
        record_baseline(
            suite_name="loop",
            suite_version="1.0.0",
            metrics=SuiteMetrics(task_count=task_count, pass_count=pass_count),
            path=path,
            created_at="2026-01-02T00:00:00+00:00",
        )
        return path

    def test_compare_pass_exits_zero(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        run = self._write_run(tmp_path, pass_count=9)
        base = self._write_run(tmp_path, pass_count=15, name="baseline.json")
        assert main(["compare", "--run", str(run), "--baseline", str(base)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is True
        assert payload["suite"] == "loop"

    def test_compare_gross_regression_exits_one(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        run = self._write_run(tmp_path, pass_count=7)
        base = self._write_run(tmp_path, pass_count=15, name="baseline.json")
        assert main(["compare", "--run", str(run), "--baseline", str(base)]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is False
        assert payload["baseline_pass_rate"] == pytest.approx(0.5)

    def test_no_baseline_file_exits_zero_and_records(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        run = self._write_run(tmp_path, pass_count=20)
        assert main(["compare", "--run", str(run)]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is True
        assert "records it" in payload["reason"]

    def test_missing_run_metrics_is_harness_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["compare", "--run", str(tmp_path / "nope.json")]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is False
        assert "harness error" in payload["reason"]

    def test_harness_error_flag_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        run = self._write_run(tmp_path, pass_count=30)
        assert main(["compare", "--run", str(run), "--harness-error", "boom"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["passed"] is False


class TestWorkflowYaml:
    """eval.yml parses and declares the required triggers (T8 acceptance).

    No actionlint is available, so the practical check is a YAML parse plus
    assertions pinning the ticket's acceptance criteria: PR trigger for E1,
    nightly cron + workflow_dispatch for E2/E3/E6, the nightly job shelling
    out to the tested gate CLI, and the F1/A1/A3 review fixes.
    """

    def test_workflow_parses_and_declares_triggers(self) -> None:
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        on_section = raw.get("on")
        if on_section is None:
            # PyYAML 1.1 parses the bare `on:` key as boolean True.
            on_section = raw.get(True)
        assert isinstance(on_section, dict)
        assert "pull_request" in on_section
        assert "schedule" in on_section
        assert "workflow_dispatch" in on_section
        assert isinstance(on_section["schedule"], list)
        assert "cron" in on_section["schedule"][0]

        jobs = raw["jobs"]
        assert "e1-pr-gate" in jobs
        assert "nightly" in jobs

    def test_nightly_job_shells_out_to_the_tested_gate_cli(self) -> None:
        """The compare step shells out to the gate CLI; the verdict logic is
        not re-implemented untested in YAML (T8 advisory A2)."""
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = raw["jobs"]["nightly"]["steps"]
        compare = next(
            s for s in steps if s.get("name") == "Compare against versioned baselines"
        )
        assert "cortex.eval.gate" in compare["run"]
        assert "compare_run_to_baseline" not in compare["run"]

    def test_record_step_skips_per_file_not_per_cache_hit(self) -> None:
        """T8 fatal F1: the record step must not be gated on the cache (a
        first skipped/failed run would otherwise disable baselining forever);
        the per-suite+version skip is on file existence inside the script,
        and the save step is gated on a cache miss AND the baselines dir
        containing files, so an empty eval-baselines is never cached."""
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = raw["jobs"]["nightly"]["steps"]
        record = next(
            s
            for s in steps
            if s.get("name") == "Record first known-good run as versioned baseline"
        )
        assert "if" not in record
        assert "if baseline_path.exists():" in record["run"]
        save = next(s for s in steps if s.get("name") == "Save versioned baselines")
        save_if = save.get("if", "")
        assert "cache-hit != 'true'" in save_if
        assert "hashFiles('eval-baselines/*.json') != ''" in save_if

    def test_upload_step_ignores_missing_files(self) -> None:
        """T8 advisory A1: the always() upload step must not fail on early
        failure/cancellation when the report dirs were never created."""
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = raw["jobs"]["nightly"]["steps"]
        upload = next(s for s in steps if s.get("name") == "Upload eval report")
        assert upload.get("if") == "always()"
        assert upload["with"]["if-no-files-found"] == "ignore"

    def test_all_sync_steps_use_all_extras(self) -> None:
        """T8 advisory A3: eval.yml syncs with --all-extras like every ci.yml
        job, so test-only deps in any extra are installed."""
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        for job in ("e1-pr-gate", "nightly"):
            steps = raw["jobs"][job]["steps"]
            sync = next(s["run"] for s in steps if "uv sync" in s.get("run", ""))
            assert "uv sync --frozen --all-extras" in sync

    def test_pr_job_runs_the_deterministic_eval_suite(self) -> None:
        """The E1 PR job runs pytest tests/eval (evidence suite skips until #83)."""
        raw = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        jobs = raw["jobs"]
        steps = jobs["e1-pr-gate"]["steps"]
        run_steps = [s["run"] for s in steps if "run" in s]
        assert any("pytest tests/eval" in run for run in run_steps)
