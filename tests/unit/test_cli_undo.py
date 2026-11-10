"""`cortex undo` CLI (ADR-0021, issue #147).

The command is a thin shell over ``SafetyStore.undo``: no prompt, a restored
target on stdout, and an ``error:`` line with exit 1 when the entry cannot be
undone.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cortex.cli import cli
from cortex.config.safety import SafetySettings
from cortex.safety import SafetyStore

runner = CliRunner()


@pytest.fixture
def safety_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> SafetySettings:
    settings = SafetySettings(store_dir=tmp_path / "store", _env_file=None)
    stub = SimpleNamespace(safety=settings)
    monkeypatch.setattr("cortex.config.loader.get_settings", lambda: stub)
    return settings


def _store(settings: SafetySettings) -> SafetyStore:
    return SafetyStore(settings)


class TestUndo:
    def test_undo_last_restores_and_prints_target_and_snapshot(
        self, safety_settings: SafetySettings, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(safety_settings)
        entry_id = store.snapshot(target)
        assert entry_id is not None
        target.write_text("v2", encoding="utf-8")

        result = runner.invoke(cli, ["undo"])

        assert result.exit_code == 0, result.output
        assert f"restored: {target}" in result.output
        assert f"snapshot: {store.snapshots_dir / entry_id / 'original'}" in (
            result.output
        )
        assert target.read_text(encoding="utf-8") == "v1"
        marker = store.entries()[-1]
        assert marker["action"] == "undo"
        assert marker["target_id"] == entry_id

    def test_undo_by_journal_id(
        self, safety_settings: SafetySettings, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(safety_settings)
        first = store.snapshot(target)
        assert first is not None
        target.write_text("v2", encoding="utf-8")
        second = store.snapshot(target)
        assert second is not None
        target.write_text("v3", encoding="utf-8")

        result = runner.invoke(cli, ["undo", first])

        assert result.exit_code == 0, result.output
        assert target.read_text(encoding="utf-8") == "v1"

    def test_two_undo_last_calls_revert_two_overwrites_in_reverse_order(
        self, safety_settings: SafetySettings, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(safety_settings)
        store.snapshot(target)
        target.write_text("v2", encoding="utf-8")
        store.snapshot(target)
        target.write_text("v3", encoding="utf-8")

        first = runner.invoke(cli, ["undo"])
        assert first.exit_code == 0, first.output
        assert target.read_text(encoding="utf-8") == "v2"

        second = runner.invoke(cli, ["undo"])
        assert second.exit_code == 0, second.output
        assert target.read_text(encoding="utf-8") == "v1"

    def test_error_goes_to_stderr_with_exit_1_and_no_traceback(
        self, safety_settings: SafetySettings
    ) -> None:
        result = runner.invoke(cli, ["undo"])

        assert result.exit_code == 1
        assert "error: journal is empty" in result.stderr
        assert "Traceback" not in result.output

    def test_already_undone_is_reported(
        self, safety_settings: SafetySettings, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(safety_settings)
        entry_id = store.snapshot(target)
        assert entry_id is not None
        target.write_text("v2", encoding="utf-8")
        store.undo("last")

        result = runner.invoke(cli, ["undo", entry_id])

        assert result.exit_code == 1
        assert f"error: already undone: {entry_id}" in result.stderr

    def test_unknown_id_is_reported(
        self, safety_settings: SafetySettings, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        _store(safety_settings).snapshot(target)

        result = runner.invoke(cli, ["undo", "nope"])

        assert result.exit_code == 1
        assert "error: journal entry not found: nope" in result.stderr
