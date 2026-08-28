"""Tests for baseline loading: malformed files must yield None."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cortex.eval.baseline import load_baseline


def _write(path: Path, raw: object) -> Path:
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


class TestLoadBaseline:
    """The documented contract: None when missing or malformed."""

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_baseline(tmp_path / "nope.json") is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_baseline(path) is None

    def test_non_dict_root_returns_none(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "root.json", ["not", "a", "dict"])
        assert load_baseline(path) is None

    def test_list_root_with_required_keys_returns_none(self, tmp_path: Path) -> None:
        """A list whose elements happen to be all required keys is still not a dict."""
        required = ["suite_name", "suite_version", "task_count", "pass_count", "created_at"]
        path = _write(tmp_path / "list-root.json", required)
        assert load_baseline(path) is None

    @pytest.mark.parametrize(
        "root",
        [None, 5, 1.5, True, False, "root"],
        ids=["null", "int", "float", "true", "false", "string"],
    )
    def test_scalar_root_returns_none(self, tmp_path: Path, root: object) -> None:
        path = _write(tmp_path / "scalar-root.json", root)
        assert load_baseline(path) is None

    def test_missing_top_level_key_returns_none(self, tmp_path: Path) -> None:
        """A dict missing a required key is malformed, not a crash."""
        raw = {
            "suite_name": "mix",
            "suite_version": "1.0.0",
            "task_count": 2,
            "pass_count": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "metrics": {"task_count": 2, "pass_count": 1},
        }
        del raw["pass_count"]
        path = _write(tmp_path / "missing-key.json", raw)
        assert load_baseline(path) is None

    def test_malformed_metrics_returns_none(self, tmp_path: Path) -> None:
        """A metrics payload with the wrong shape is malformed, not a crash."""
        raw = {
            "suite_name": "mix",
            "suite_version": "1.0.0",
            "task_count": 2,
            "pass_count": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "metrics": {"task_count": 2, "pass_count": 1, "bogus_field": 1},
        }
        path = _write(tmp_path / "bad-metrics.json", raw)
        assert load_baseline(path) is None

    def test_unknown_extra_fields_are_ignored(self, tmp_path: Path) -> None:
        """A future schema version with extra fields still loads (v1 subset)."""
        raw = {
            "suite_name": "mix",
            "suite_version": "1.0.0",
            "task_count": 2,
            "pass_count": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "metrics": {"task_count": 2, "pass_count": 1},
            "schema_version": 2,
            "future_field": "ignored",
        }
        path = _write(tmp_path / "future.json", raw)
        baseline = load_baseline(path)
        assert baseline is not None
        assert baseline.schema_version == 2
