"""File snapshots, append-only journal, and undo (ADR-0021, issue #147).

:class:`SafetyStore` is the filesystem side of the safety net:

* ``snapshots/<journal-id>/original`` is a byte-for-byte private copy of a
  file before a destructive overwrite;
* ``journal.jsonl`` is append-only: one JSON line per destructive action and
  one per undo/abort marker, never rewritten or deleted;
* :meth:`SafetyStore.undo` restores a snapshot and records the marker, or
  raises :class:`UndoError` with the recorded recipe when it cannot.

The store lives under ``SAFETY_STORE_DIR`` (``~/.local/share/cortex``),
outside the repo and every Docker volume, for the same reason as the backups
(ADR-0021). New artifacts are chowned best-effort to the store owner so a root
container's files stay readable by the host CLI; the snapshot path is stored
relative to ``snapshots/`` so the journal is portable across the host/container
bind mount. The target path still assumes one filesystem namespace: ``undo``
must run where the original path exists (host-local Cortex for file undo).
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
        # No filesystem access here: an unwritable store must not abort startup,
        # and disabling snapshots must create nothing. Directories are created
        # lazily by snapshot()/_append().
        self._settings = settings
        self.store_dir = Path(settings.store_dir)
        self.snapshots_dir = self.store_dir / "snapshots"
        self.journal_path = self.store_dir / "journal.jsonl"

    def _ensure_store_dir(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.store_dir.chmod(0o700)
        except OSError as exc:  # a mounted store may refuse the chmod
            logger.warning("could not set mode 0700 on %s: %s", self.store_dir, exc)

    def _ensure_snapshots_dir(self) -> None:
        self._ensure_store_dir()
        self.snapshots_dir.mkdir(exist_ok=True, mode=0o700)
        try:
            self.snapshots_dir.chmod(0o700)
        except OSError as exc:  # a mounted store may refuse the chmod
            logger.warning("could not set mode 0700 on %s: %s", self.snapshots_dir, exc)
        self._chown_to_store_owner(self.snapshots_dir)

    def _chown_to_store_owner(self, path: Path) -> None:
        """Best-effort: a root container writes files owned by root, while the
        host CLI needs them owned by the mounted store's user.

        On a host run the file is already the user's; a failed chown (to self,
        or on a filesystem that refuses it) must not fail the snapshot.
        """
        try:
            store = self.store_dir.stat()
            os.chown(path, store.st_uid, store.st_gid)
        except OSError as exc:
            logger.warning("could not chown %s: %s", path, exc)

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
        relative_snapshot = f"{entry_id}/original"
        try:
            self._ensure_snapshots_dir()
            snapshot_dir.mkdir(mode=0o700)
            mode = path.stat().st_mode & 0o777
            shutil.copy2(path, snapshot_path)
            snapshot_path.chmod(0o600)
            self._chown_to_store_owner(snapshot_dir)
            self._chown_to_store_owner(snapshot_path)
        except Exception as exc:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise UndoError(f"could not snapshot {path}: {exc}") from exc

        entry: dict[str, Any] = {
            "id": entry_id,
            "ts": datetime.now(UTC).isoformat(),
            "action": "file_write",
            "targets": [str(path)],
            "snapshot": relative_snapshot,
            "mode": mode,
            "undo_recipe": f"restore {snapshot_path} to {path}",
            "status": "applied",
        }
        try:
            self._append(entry)
        except Exception as exc:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            raise UndoError(f"could not journal the snapshot of {path}: {exc}") from exc
        return entry_id

    def record_shell(
        self, command: str, *, pattern: str, status: str, reason: str
    ) -> None:
        """Journal one classified shell command (denied or bypassed).

        The journal is written before the denial is returned or the bypassed
        command executes, so a failed write lets the caller refuse to run it.
        """
        self._append(
            {
                "id": uuid.uuid4().hex,
                "ts": datetime.now(UTC).isoformat(),
                "action": "shell",
                "targets": [command],
                "snapshot": None,
                "undo_recipe": f"manual: {command}",
                "status": status,
                "pattern": pattern,
                "reason": reason,
            }
        )

    def record_guard_disabled(self) -> None:
        """Journal that the shell guard was disabled by configuration."""
        self._append(
            {
                "id": uuid.uuid4().hex,
                "ts": datetime.now(UTC).isoformat(),
                "action": "shell_guard",
                "targets": [],
                "snapshot": None,
                "undo_recipe": "n/a",
                "status": "disabled",
                "reason": "SAFETY_SHELL_GUARD=false",
            }
        )

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
                    entry = None
                if not isinstance(entry, dict):
                    logger.warning(
                        "skipping malformed journal line %d in %s",
                        lineno,
                        self.journal_path,
                    )
                    continue
                entries.append(entry)
        return entries

    def abort(self, entry_id: str, *, restore: bool = False) -> None:
        """Mark an applied entry as aborted; optionally roll the target back first.

        A failed write may have truncated the target before raising. With
        ``restore=True`` the snapshot is put back before the abort marker is
        appended, so the last good content survives. If that restore fails, the
        marker is not written and the entry stays applied: ``undo`` can still
        recover from the snapshot.
        """
        entry = next(
            (
                candidate
                for candidate in self.entries()
                if candidate.get("id") == entry_id
                and candidate.get("action") not in {"undo", "abort"}
            ),
            None,
        )
        if entry is None:
            raise UndoError(f"journal entry not found: {entry_id}")

        if restore:
            self._restore_entry(entry)

        marker: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(),
            "action": "abort",
            "target_id": entry_id,
            "targets": entry.get("targets", []),
            "status": "aborted",
        }
        self._append(marker)

    def undo(self, target: str) -> UndoResult:
        """Restore the newest entry (``last``) or the entry with ``target`` id."""
        entries = self.entries()
        if not entries:
            raise UndoError("nothing to undo")

        undone = {
            marker.get("target_id")
            for marker in entries
            if marker.get("action") == "undo"
        }
        aborted = {
            marker.get("target_id")
            for marker in entries
            if marker.get("action") == "abort"
        }
        entry = self._select(entries, target, undone, aborted)
        entry_id = str(entry.get("id", ""))
        if entry_id in aborted:
            raise UndoError(f"action was aborted: {entry_id}")
        if entry_id in undone:
            raise UndoError(f"already undone: {entry_id}")

        result = self._restore_entry(entry)

        marker: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(UTC).isoformat(),
            "action": "undo",
            "target_id": entry_id,
            "targets": entry.get("targets", []),
            "status": "undone",
        }
        try:
            self._append(marker)
        except Exception as exc:
            raise UndoError(
                f"restored {result.target} but could not append the undo marker: {exc}"
            ) from exc
        return result

    def _restore_entry(self, entry: dict[str, Any]) -> UndoResult:
        """Validate an entry's snapshot and restore it over its target."""
        entry_id = str(entry.get("id", ""))
        action = str(entry.get("action", "unknown"))
        recipe = entry.get("undo_recipe")
        targets = entry.get("targets")
        raw_snapshot = entry.get("snapshot")
        if not isinstance(targets, list) or not targets or not isinstance(raw_snapshot, str):
            raise UndoError(f"cannot undo {action}; manual: {recipe}")

        candidate = Path(raw_snapshot)
        if not candidate.is_absolute():
            candidate = self.snapshots_dir / candidate
        if not candidate.is_file():
            raise UndoError(f"cannot undo {action}; manual: {recipe}")
        snapshot_path = candidate.resolve()
        if not snapshot_path.is_relative_to(self.snapshots_dir.resolve()):
            raise UndoError(
                f"refusing to restore snapshot outside the store: {raw_snapshot}"
            )

        target_path = Path(str(targets[0]))
        mode = entry.get("mode")
        self._restore(snapshot_path, target_path, mode if isinstance(mode, int) else None)
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
        aborted: set[Any],
    ) -> dict[str, Any]:
        """Pick an applied entry; undo/abort markers are history, never targets.

        ``last`` skips aborted and already-undone entries so repeated calls walk
        backwards through two consecutive overwrites.
        """
        if target == "last":
            for entry in reversed(entries):
                if entry.get("action") in {"undo", "abort"}:
                    continue
                if entry.get("id") in undone or entry.get("id") in aborted:
                    continue
                return entry
            raise UndoError("nothing to undo")

        for entry in entries:
            if entry.get("id") == target and entry.get("action") not in {
                "undo",
                "abort",
            }:
                return entry
        raise UndoError(f"journal entry not found: {target}")

    def _restore(self, snapshot: Path, target: Path, mode: int | None) -> None:
        """Put ``snapshot`` over ``target`` atomically, then restore ``mode``."""
        temp: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".undo", dir=target.parent
            )
            os.close(fd)
            temp = Path(temp_name)
            shutil.copy2(snapshot, temp)
            os.replace(temp, target)
            temp = None
            if mode is not None:
                os.chmod(target, mode)
        except Exception as exc:
            if temp is not None:
                temp.unlink(missing_ok=True)
            raise UndoError(
                f"could not restore {target} from {snapshot}: {exc}"
            ) from exc

    def _append(self, entry: dict[str, Any]) -> None:
        """Append exactly one JSON line, creating the journal mode ``0600``."""
        self._ensure_store_dir()
        created = not self.journal_path.exists()
        with os.fdopen(
            os.open(
                self.journal_path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            ),
            "a",
            encoding="utf-8",
        ) as journal:
            journal.write(json.dumps(entry) + "\n")
        if created:
            self._chown_to_store_owner(self.journal_path)
