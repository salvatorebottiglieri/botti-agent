"""File snapshots, append-only journal, and undo (ADR-0021, issue #147).

:class:`SafetyStore` is the filesystem side of the safety net:

* ``snapshots/<journal-id>/original`` is a byte-for-byte private copy of a
  file before a destructive overwrite;
* ``journal.jsonl`` is append-only: one JSON line per destructive action and
  one per undo marker, never rewritten or deleted;
* :meth:`SafetyStore.undo` restores a snapshot and records the marker, or
  raises :class:`UndoError` with the recorded recipe when it cannot.

The store lives under ``SAFETY_STORE_DIR`` (``~/.local/share/cortex``),
outside the repo and every Docker volume, for the same reason as the backups
(ADR-0021).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortex.config.safety import SafetySettings

logger = logging.getLogger(__name__)


class UndoError(RuntimeError):
    """A destructive action cannot be undone."""


@dataclass(frozen=True, slots=True)
class UndoResult:
    """What an undo restored."""

    target: Path
    snapshot: Path
    entry_id: str


class SafetyStore:
    """Snapshots, the append-only journal, and undo."""

    def __init__(self, settings: SafetySettings) -> None:
        self._settings = settings
        self.store_dir = Path(settings.store_dir)
        self.snapshots_dir = self.store_dir / "snapshots"
        self.journal_path = self.store_dir / "journal.jsonl"
        self._ensure_snapshots_dir()

    def _ensure_snapshots_dir(self) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.snapshots_dir.chmod(0o700)
        except OSError as exc:  # a mounted store may refuse the chmod
            logger.warning("could not set mode 0700 on %s: %s", self.snapshots_dir, exc)

    def snapshot(self, path: Path) -> str | None:
        """Copy ``path`` as it is now and journal the destructive action.

        Returns the journal id, or ``None`` when snapshots are disabled. Any
        failure leaves no half-written snapshot behind and raises
        :class:`UndoError`: callers must fail closed.
        """
        if not self._settings.snapshot_enabled:
            return None

        entry_id = uuid.uuid4().hex
        snapshot_dir = self.snapshots_dir / entry_id
        snapshot_path = snapshot_dir / "original"
        try:
            self.snapshots_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            snapshot_dir.mkdir(mode=0o700)
            shutil.copy2(path, snapshot_path)
            snapshot_path.chmod(0o600)
        except Exception as exc:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise UndoError(f"could not snapshot {path}: {exc}") from exc

        entry: dict[str, Any] = {
            "id": entry_id,
            "ts": datetime.now(UTC).isoformat(),
            "action": "file_write",
            "targets": [str(path)],
            "snapshot": str(snapshot_path),
            "undo_recipe": f"restore {snapshot_path} to {path}",
            "status": "applied",
        }
        try:
            self._append(entry)
        except Exception as exc:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise UndoError(f"could not journal the snapshot of {path}: {exc}") from exc
        return entry_id

    def entries(self) -> list[dict[str, Any]]:
        """The journal in file order; blank or malformed lines are skipped."""
        if not self.journal_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        with self.journal_path.open("r", encoding="utf-8") as journal:
            for lineno, line in enumerate(journal, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.warning(
                        "skipping malformed journal line %d in %s",
                        lineno,
                        self.journal_path,
                    )
                    continue
                if not isinstance(entry, dict):
                    logger.warning(
                        "skipping malformed journal line %d in %s",
                        lineno,
                        self.journal_path,
                    )
                    continue
                entries.append(entry)
        return entries

    def undo(self, target: str) -> UndoResult:
        """Restore the newest entry (``last``) or the entry with ``target`` id."""
        entries = self.entries()
        if not entries:
            raise UndoError("journal is empty")

        undone = {
            marker.get("target_id")
            for marker in entries
            if marker.get("action") == "undo"
        }
        entry = self._select(entries, target, undone)
        entry_id = str(entry.get("id", ""))
        action = str(entry.get("action", "unknown"))
        recipe = entry.get("undo_recipe")
        if entry_id in undone:
            raise UndoError(f"already undone: {entry_id}")

        targets = entry.get("targets")
        raw_snapshot = entry.get("snapshot")
        if (
            not isinstance(targets, list)
            or not targets
            or not isinstance(raw_snapshot, str)
            or not raw_snapshot
            or not Path(raw_snapshot).is_file()
        ):
            raise UndoError(f"cannot undo {action}; manual: {recipe}")

        target_path = Path(str(targets[0]))
        snapshot_path = Path(raw_snapshot).resolve()
        if not snapshot_path.is_relative_to(self.snapshots_dir.resolve()):
            raise UndoError(
                f"refusing to restore snapshot outside the store: {raw_snapshot}"
            )

        self._restore(snapshot_path, target_path)

        marker: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(),
            "action": "undo",
            "target_id": entry_id,
            "targets": targets,
            "status": "undone",
        }
        try:
            self._append(marker)
        except Exception as exc:
            raise UndoError(
                f"restored {target_path} but could not append the undo marker: {exc}"
            ) from exc
        return UndoResult(
            target=target_path,
            snapshot=snapshot_path,
            entry_id=entry_id,
        )

    def _select(
        self,
        entries: list[dict[str, Any]],
        target: str,
        undone: set[Any],
    ) -> dict[str, Any]:
        """Pick an applied entry; undo markers are history, never targets.

        ``last`` skips entries that already have an undo marker so repeated
        calls walk backwards through two consecutive overwrites.
        """
        if target == "last":
            for entry in reversed(entries):
                if entry.get("action") == "undo":
                    continue
                if entry.get("id") in undone:
                    continue
                return entry
            raise UndoError("journal is empty")

        for entry in entries:
            if entry.get("id") == target and entry.get("action") != "undo":
                return entry
        raise UndoError(f"journal entry not found: {target}")

    def _restore(self, snapshot: Path, target: Path) -> None:
        """Put ``snapshot`` over ``target`` atomically, preserving its stats."""
        temp: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".undo", dir=target.parent
            )
            os.close(fd)
            temp = Path(temp_name)
            shutil.copyfile(snapshot, temp)
            os.replace(temp, target)
            temp = None
            shutil.copystat(snapshot, target)
        except Exception as exc:
            if temp is not None:
                temp.unlink(missing_ok=True)
            raise UndoError(
                f"could not restore {target} from {snapshot}: {exc}"
            ) from exc

    def _append(self, entry: dict[str, Any]) -> None:
        """Append exactly one JSON line, creating the journal mode ``0600``."""
        line = (json.dumps(entry) + "\n").encode("utf-8")
        fd = os.open(
            self.journal_path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
