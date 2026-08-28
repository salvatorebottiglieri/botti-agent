"""Per-suite baseline recording and loading.

A baseline is the recorded metrics of a suite on a known-good run
(CONTEXT.md: Eval Baseline). It is written as a small versioned JSON file
(suite name, suite version, task count, pass count, metrics, timestamp) so
nightly runs can compare against it and gates can trigger on gross
regressions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortex.eval.metrics import SuiteMetrics

#: Version of the baseline file schema; bump on breaking shape changes.
BASELINE_SCHEMA_VERSION = 1


@dataclass
class EvalBaseline:
    """Recorded metrics of a suite on a known-good run."""

    suite_name: str
    suite_version: str
    task_count: int
    pass_count: int
    metrics: SuiteMetrics
    created_at: str
    schema_version: int = BASELINE_SCHEMA_VERSION


def record_baseline(
    *,
    suite_name: str,
    suite_version: str,
    metrics: SuiteMetrics,
    path: str | Path,
    created_at: str | None = None,
) -> EvalBaseline:
    """Write a versioned baseline JSON file and return it.

    Args:
        suite_name: Name of the eval suite.
        suite_version: Version of the suite (baselines are versioned per suite).
        metrics: Aggregate metrics of the run.
        path: Destination file (parent directories are created).
        created_at: ISO timestamp; defaults to now (UTC).

    Returns:
        The recorded :class:`EvalBaseline`.
    """
    baseline = EvalBaseline(
        suite_name=suite_name,
        suite_version=suite_version,
        task_count=metrics.task_count,
        pass_count=metrics.pass_count,
        metrics=metrics,
        created_at=created_at or datetime.now(UTC).isoformat(),
    )
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(asdict(baseline), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return baseline


def load_baseline(path: str | Path) -> EvalBaseline | None:
    """Load a baseline file; None when missing or malformed.

    Validates shape only: required keys present, metrics is a mapping.
    Value types under valid keys are not validated, so a future schema
    version that changes a field's type still loads (forward compat).
    """
    try:
        raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    required = ("suite_name", "suite_version", "task_count", "pass_count", "created_at")
    if not isinstance(raw, dict) or not all(key in raw for key in required):
        return None
    metrics = raw.get("metrics")
    if not isinstance(metrics, dict):
        return None
    try:
        return EvalBaseline(
            suite_name=raw["suite_name"],
            suite_version=raw["suite_version"],
            task_count=raw["task_count"],
            pass_count=raw["pass_count"],
            metrics=SuiteMetrics(**metrics),
            created_at=raw["created_at"],
            schema_version=raw.get("schema_version", BASELINE_SCHEMA_VERSION),
        )
    except (KeyError, TypeError):
        return None
