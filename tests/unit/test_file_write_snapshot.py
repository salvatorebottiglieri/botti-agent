"""``FileWriteTool`` snapshots before overwriting an existing file (#147).

The witness pairs are ``v1`` vs ``v2``: the snapshot must hold the *previous*
bytes, not the new ones. New files and appends are not destructive and write no
journal entry; a snapshot failure refuses the overwrite (fail closed).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cortex.config.safety import SafetySettings
from cortex.safety import SafetyStore, UndoError
from cortex.tools.meta import FileWriteTool


def _settings(tmp_path: Path, *, enabled: bool = True) -> SafetySettings:
    return SafetySettings(
        store_dir=tmp_path / "store",
        snapshot_enabled=enabled,
        _env_file=None,
    )


@pytest.fixture
def tool(tmp_path: Path) -> FileWriteTool:
    return FileWriteTool(_settings(tmp_path))


class TestOverwrite:
    async def test_existing_file_is_snapshotted_before_the_write(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")

        result = await tool.execute({"path": str(target), "content": "v2"})

        assert result.success is True, result.error
        assert target.read_text(encoding="utf-8") == "v2"
        store = tool._store
        assert store is not None
        (entry,) = store.entries()
        snapshot_file = store.snapshots_dir / entry["snapshot"]
        assert snapshot_file.read_bytes() == b"v1"
        assert entry["targets"] == [str(target.resolve())]
        assert result.metadata["snapshot_id"] == entry["id"]
        assert entry["mode"] == 0o644

    async def test_two_overwrites_undo_in_reverse_order(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")

        await tool.execute({"path": str(target), "content": "v2"})
        await tool.execute({"path": str(target), "content": "v3"})

        store = tool._store
        assert store is not None
        entries = store.entries()
        assert [entry["action"] for entry in entries] == ["file_write", "file_write"]
        assert len(store.journal_path.read_text(encoding="utf-8").splitlines()) == 2

        store.undo("last")
        assert target.read_text(encoding="utf-8") == "v2"
        store.undo("last")
        assert target.read_text(encoding="utf-8") == "v1"

    async def test_snapshot_failure_refuses_the_overwrite(
        self,
        tool: FileWriteTool,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")

        def fail_snapshot(self: SafetyStore, path: Path) -> str | None:
            raise UndoError("disk full")

        monkeypatch.setattr(SafetyStore, "snapshot", fail_snapshot)

        result = await tool.execute({"path": str(target), "content": "v2"})

        assert result.success is False
        assert "Snapshot failed; refusing to overwrite" in result.error
        assert "disk full" in result.error
        assert target.read_text(encoding="utf-8") == "v1"
        store = tool._store
        assert store is not None
        assert store.entries() == []

    async def test_directory_target_is_refused(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "directory"
        target.mkdir()

        result = await tool.execute({"path": str(target), "content": "v2"})

        assert result.success is False
        assert "Snapshot failed; refusing to overwrite" in result.error
        assert target.is_dir()

    async def test_disabled_snapshot_writes_without_journaling(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        tool = FileWriteTool(_settings(tmp_path, enabled=False))

        result = await tool.execute({"path": str(target), "content": "v2"})

        assert result.success is True, result.error
        assert target.read_text(encoding="utf-8") == "v2"
        store = tool._store
        assert store is not None
        assert store.entries() == []
        assert not store.store_dir.exists()
        assert "snapshot_id" not in result.metadata

    async def test_unwritable_store_construction_is_lazy_and_write_fails_closed(
        self, tmp_path: Path
    ) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        settings = SafetySettings(store_dir=blocker / "store", _env_file=None)
        tool = FileWriteTool(settings)
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")

        result = await tool.execute({"path": str(target), "content": "v2"})

        assert result.success is False
        assert "Snapshot failed; refusing to overwrite" in result.error
        assert target.read_text(encoding="utf-8") == "v1"

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file mode bits")
    async def test_read_only_target_aborts_the_journal_entry(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        target.chmod(0o444)

        result = await tool.execute({"path": str(target), "content": "v2"})

        assert result.success is False
        assert "Permission denied" in result.error
        assert target.read_text(encoding="utf-8") == "v1"
        store = tool._store
        assert store is not None
        entries = store.entries()
        assert [entry["action"] for entry in entries] == ["file_write", "abort"]
        assert entries[1]["target_id"] == entries[0]["id"]
        with pytest.raises(UndoError, match="nothing to undo"):
            store.undo("last")
        with pytest.raises(UndoError, match=f"action was aborted: {entries[0]['id']}"):
            store.undo(entries[0]["id"])

    async def test_encoding_failure_rolls_back_the_original_file(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1-original", encoding="utf-8")
        target.chmod(0o644)

        result = await tool.execute(
            {"path": str(target), "content": "é", "encoding": "ascii"}
        )

        assert result.success is False
        assert "Failed to write file" in result.error
        assert target.read_bytes() == b"v1-original"
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        store = tool._store
        assert store is not None
        entries = store.entries()
        assert [entry["action"] for entry in entries] == ["file_write", "abort"]
        with pytest.raises(UndoError, match="nothing to undo"):
            store.undo("last")

    async def test_failed_second_overwrite_rolls_back_to_the_last_good_version(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        await tool.execute({"path": str(target), "content": "v2"})

        result = await tool.execute(
            {"path": str(target), "content": "é", "encoding": "ascii"}
        )

        assert result.success is False
        assert target.read_bytes() == b"v2"
        store = tool._store
        assert store is not None
        assert [entry["action"] for entry in store.entries()] == [
            "file_write",
            "file_write",
            "abort",
        ]
        store.undo("last")
        assert target.read_text(encoding="utf-8") == "v1"

    async def test_abort_rollback_failure_still_returns_the_write_error(
        self, tmp_path: Path
    ) -> None:
        class RaisingAbortStore:
            def __init__(self) -> None:
                self.calls: list[tuple[str, bool]] = []

            def snapshot(self, path: Path) -> str:
                return "entry-1"

            def abort(self, entry_id: str, *, restore: bool = False) -> None:
                self.calls.append((entry_id, restore))
                raise UndoError("journal unavailable")

        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        tool = FileWriteTool()
        fake = RaisingAbortStore()
        tool._store = fake  # type: ignore[assignment]

        result = await tool.execute(
            {"path": str(target), "content": "é", "encoding": "ascii"}
        )

        assert result.success is False
        assert "Failed to write file" in result.error
        assert fake.calls == [("entry-1", True)]


class TestNonDestructiveWrites:
    async def test_new_file_creates_no_snapshot_or_journal_entry(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "new.txt"

        result = await tool.execute({"path": str(target), "content": "v1"})

        assert result.success is True, result.error
        assert target.read_text(encoding="utf-8") == "v1"
        store = tool._store
        assert store is not None
        assert store.entries() == []
        assert not store.store_dir.exists()
        assert "snapshot_id" not in result.metadata

    async def test_append_is_not_snapshotted(
        self, tool: FileWriteTool, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")

        result = await tool.execute(
            {"path": str(target), "content": "v2", "append": True}
        )

        assert result.success is True, result.error
        assert target.read_text(encoding="utf-8") == "v1v2"
        store = tool._store
        assert store is not None
        assert store.entries() == []
        assert not store.journal_path.exists()

    async def test_without_settings_there_is_no_store(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")

        result = await FileWriteTool().execute({"path": str(target), "content": "v2"})

        assert result.success is True, result.error
        assert target.read_text(encoding="utf-8") == "v2"
        assert "snapshot_id" not in result.metadata
        assert not (tmp_path / "store").exists()
