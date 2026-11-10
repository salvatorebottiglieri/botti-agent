"""``FileWriteTool`` snapshots before overwriting an existing file (#147).

The witness pairs are ``v1`` vs ``v2``: the snapshot must hold the *previous*
bytes, not the new ones. New files and appends are not destructive and write no
journal entry; a snapshot failure refuses the overwrite (fail closed).
"""

from __future__ import annotations

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
        snapshot_file = Path(entry["snapshot"])
        assert snapshot_file.read_bytes() == b"v1"
        assert entry["targets"] == [str(target.resolve())]
        assert result.metadata["snapshot_id"] == entry["id"]

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
        assert not store.journal_path.exists()
        assert "snapshot_id" not in result.metadata


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
        assert not store.journal_path.exists()
        assert list(store.snapshots_dir.iterdir()) == []
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
