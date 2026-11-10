"""File snapshots, the append-only journal, and undo (ADR-0021, issue #147).

The contract under test: a snapshot is a byte-for-byte private copy of the
previous file plus one journal line; undo restores the bytes, appends a marker
and never rewrites history. ``undo last`` walks backwards over applied entries,
so two overwrites revert in reverse order.
"""

from __future__ import annotations

import json
import logging
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortex.config.safety import SafetySettings
from cortex.safety import SafetyStore, UndoError


def _settings(tmp_path: Path, *, enabled: bool = True) -> SafetySettings:
    return SafetySettings(
        store_dir=tmp_path / "store",
        snapshot_enabled=enabled,
        _env_file=None,
    )


def _store(tmp_path: Path, *, enabled: bool = True) -> SafetyStore:
    return SafetyStore(_settings(tmp_path, enabled=enabled))


def _journal_line(entry: dict[str, object]) -> str:
    return json.dumps(entry) + "\n"


class TestSnapshot:
    def test_copies_the_previous_content_and_appends_one_entry(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_bytes(b"\x00v1\xff\n")
        store = _store(tmp_path)

        entry_id = store.snapshot(target)

        assert isinstance(entry_id, str)
        assert entry_id
        entries = store.entries()
        assert len(entries) == 1
        entry = entries[0]
        snapshot_file = store.snapshots_dir / entry_id / "original"
        assert entry["id"] == entry_id
        assert entry["action"] == "file_write"
        assert entry["targets"] == [str(target)]
        assert entry["snapshot"] == str(snapshot_file)
        assert entry["undo_recipe"] == f"restore {snapshot_file} to {target}"
        assert entry["status"] == "applied"
        parsed_ts = datetime.fromisoformat(entry["ts"])
        assert parsed_ts.tzinfo == UTC
        assert snapshot_file.read_bytes() == b"\x00v1\xff\n"
        assert len(store.journal_path.read_text(encoding="utf-8").splitlines()) == 1

    def test_store_files_are_private(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)

        entry_id = store.snapshot(target)

        assert entry_id is not None
        snapshot_dir = store.snapshots_dir / entry_id
        snapshot_file = snapshot_dir / "original"
        assert stat.S_IMODE(store.snapshots_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(snapshot_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(snapshot_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.journal_path.stat().st_mode) == 0o600

    def test_disabled_snapshot_writes_nothing(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path, enabled=False)

        assert store.snapshot(target) is None

        assert list(store.snapshots_dir.iterdir()) == []
        assert not store.journal_path.exists()
        assert store.entries() == []

    def test_copy_failure_removes_the_snapshot_dir_and_raises(
        self, tmp_path: Path
    ) -> None:
        store = _store(tmp_path)

        with pytest.raises(UndoError, match="could not snapshot"):
            store.snapshot(tmp_path / "missing.txt")

        assert list(store.snapshots_dir.iterdir()) == []
        assert not store.journal_path.exists()

    def test_a_directory_target_fails_the_copy(self, tmp_path: Path) -> None:
        target = tmp_path / "directory"
        target.mkdir()
        store = _store(tmp_path)

        with pytest.raises(UndoError, match="could not snapshot"):
            store.snapshot(target)

        assert list(store.snapshots_dir.iterdir()) == []

    def test_entries_skip_blank_and_malformed_lines(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        entry_id = store.snapshot(target)
        with store.journal_path.open("a", encoding="utf-8") as journal:
            journal.write("\n{not json\n[1, 2]\n")

        with caplog.at_level(logging.WARNING):
            entries = store.entries()

        assert [entry["id"] for entry in entries] == [entry_id]
        assert "malformed journal line" in caplog.text


class TestUndo:
    def test_undo_last_restores_bytes_and_appends_an_undone_marker(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_bytes(b"\x00v1\xff\n")
        store = _store(tmp_path)
        entry_id = store.snapshot(target)
        target.write_bytes(b"v2\n")

        result = store.undo("last")

        snapshot_file = store.snapshots_dir / entry_id / "original"
        assert target.read_bytes() == b"\x00v1\xff\n"
        assert result.target == target
        assert result.snapshot == snapshot_file
        assert result.entry_id == entry_id

        applied, marker = store.entries()
        assert applied["status"] == "applied"
        assert marker["action"] == "undo"
        assert marker["target_id"] == entry_id
        assert marker["targets"] == [str(target)]
        assert marker["status"] == "undone"

        lines = store.journal_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["id"] == entry_id
        assert json.loads(lines[0])["status"] == "applied"

    def test_undo_by_id_restores_that_entry(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        first = store.snapshot(target)
        assert first is not None
        target.write_text("v2", encoding="utf-8")
        second = store.snapshot(target)
        assert second is not None and second != first
        target.write_text("v3", encoding="utf-8")

        result = store.undo(first)

        assert target.read_text(encoding="utf-8") == "v1"
        assert result.entry_id == first

    def test_two_overwrites_undo_in_reverse_order(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        first = store.snapshot(target)
        assert first is not None
        target.write_text("v2", encoding="utf-8")
        second = store.snapshot(target)
        assert second is not None
        target.write_text("v3", encoding="utf-8")

        first_result = store.undo("last")
        assert target.read_text(encoding="utf-8") == "v2"
        assert first_result.entry_id == second

        second_result = store.undo("last")
        assert target.read_text(encoding="utf-8") == "v1"
        assert second_result.entry_id == first

        actions = [entry["action"] for entry in store.entries()]
        assert actions == ["file_write", "file_write", "undo", "undo"]
        markers = [entry for entry in store.entries() if entry["action"] == "undo"]
        assert [marker["target_id"] for marker in markers] == [second, first]

    def test_already_undone_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        entry_id = store.snapshot(target)
        assert entry_id is not None
        target.write_text("v2", encoding="utf-8")
        store.undo("last")

        with pytest.raises(UndoError, match=f"already undone: {entry_id}"):
            store.undo(entry_id)

    def test_empty_journal_raises(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        with pytest.raises(UndoError, match="journal is empty"):
            store.undo("last")

    def test_unknown_id_raises(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        store.snapshot(target)

        with pytest.raises(UndoError, match="journal entry not found: nope"):
            store.undo("nope")

    def test_entry_without_a_snapshot_prints_the_recipe(self, tmp_path: Path) -> None:
        recipe = "restore /store/backups/cortex.dump with pg_restore"
        store = _store(tmp_path)
        store.journal_path.write_text(
            _journal_line(
                {
                    "id": "shell-1",
                    "ts": datetime.now(UTC).isoformat(),
                    "action": "shell",
                    "targets": ["/tmp/database"],
                    "undo_recipe": recipe,
                    "status": "applied",
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(UndoError) as excinfo:
            store.undo("last")

        assert str(excinfo.value) == f"cannot undo shell; manual: {recipe}"

    def test_missing_snapshot_file_prints_the_recipe(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        entry_id = store.snapshot(target)
        assert entry_id is not None
        snapshot_file = store.snapshots_dir / entry_id / "original"
        snapshot_file.unlink()
        target.write_text("v2", encoding="utf-8")
        (entry,) = store.entries()

        with pytest.raises(UndoError) as excinfo:
            store.undo("last")

        assert str(excinfo.value) == (
            f"cannot undo file_write; manual: {entry['undo_recipe']}"
        )
        assert target.read_text(encoding="utf-8") == "v2"

    def test_snapshot_outside_the_store_is_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v2", encoding="utf-8")
        outside = tmp_path / "outside-original"
        outside.write_text("attacker", encoding="utf-8")
        store = _store(tmp_path)
        store.journal_path.write_text(
            _journal_line(
                {
                    "id": "evil",
                    "ts": datetime.now(UTC).isoformat(),
                    "action": "file_write",
                    "targets": [str(target)],
                    "snapshot": str(outside),
                    "undo_recipe": f"restore {outside} to {target}",
                    "status": "applied",
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(UndoError, match="outside the store"):
            store.undo("last")

        assert target.read_text(encoding="utf-8") == "v2"
        assert len(store.entries()) == 1

    def test_restore_failure_keeps_the_target_and_writes_no_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        store.snapshot(target)
        target.write_text("v2", encoding="utf-8")

        def deny_replace(src: object, dst: object) -> None:
            raise OSError("read-only filesystem")

        monkeypatch.setattr("cortex.safety.journal.os.replace", deny_replace)

        with pytest.raises(UndoError, match="could not restore"):
            store.undo("last")

        assert target.read_text(encoding="utf-8") == "v2"
        assert len(store.entries()) == 1
        leftovers = [
            path
            for path in tmp_path.iterdir()
            if path.name not in {"store", target.name}
        ]
        assert leftovers == []
