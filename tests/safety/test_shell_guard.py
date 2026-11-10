"""Shell guard: the deny-list table and its journal entries (ADR-0021, #148).

The table is the contract: every pattern declares a real example it must stop,
and the tests parametrize over ``SHELL_PATTERNS`` itself, so adding an entry
without a matching test is impossible — the new entry's example is the test.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortex.config.safety import SafetySettings
from cortex.safety import SafetyStore, UndoError
from cortex.safety.shell_guard import SHELL_PATTERNS, ShellPattern, classify


def _store(tmp_path: Path) -> SafetyStore:
    return SafetyStore(
        SafetySettings(store_dir=tmp_path / "store", _env_file=None)
    )


class TestPatternTable:
    def test_the_table_covers_every_required_family(self) -> None:
        names = {pattern.name for pattern in SHELL_PATTERNS}

        assert len(names) == len(SHELL_PATTERNS), "pattern names must be unique"
        assert {
            "rm_recursive_force",
            "docker_compose_down_volumes",
            "docker_volume_remove",
            "docker_system_prune",
            "git_reset_hard",
            "git_clean_force",
            "sql_drop",
            "sql_truncate",
            "mkfs",
            "dd_of",
            "package_manager_remove",
            "npm_global_uninstall",
            "pkill",
            "kill_9",
        } <= names

    @pytest.mark.parametrize("pattern", SHELL_PATTERNS, ids=[p.name for p in SHELL_PATTERNS])
    def test_every_entry_is_self_describing(self, pattern: ShellPattern) -> None:
        assert isinstance(pattern, ShellPattern)
        assert pattern.name.strip()
        assert pattern.reason.strip()
        assert pattern.example.strip()

    @pytest.mark.parametrize("pattern", SHELL_PATTERNS, ids=[p.name for p in SHELL_PATTERNS])
    def test_every_example_matches_its_own_pattern(self, pattern: ShellPattern) -> None:
        assert classify(pattern.example) is pattern


class TestVariants:
    @pytest.mark.parametrize(
        "command",
        [
            "RM -RF /tmp/cortex-check",
            "rm  -rf  /tmp/cortex-check",
            "rm -fr /tmp/cortex-check",
            "rm -r -f /tmp/cortex-check",
            "rm --recursive --force /tmp/cortex-check",
            "docker-compose down --volumes",
            "docker compose rm -v",
            "docker compose -f compose.yaml down -v",
            "git clean -fd",
            "git clean --force",
            "mkfs /dev/sdb1",
            "KILL -9 1234",
            # Short flags end at a shell separator, substitution or redirect,
            # not only at whitespace (AC-1 review).
            "rm -rf /tmp/cortex-check; echo done",
            "docker compose down -v; echo done",
            "docker compose down -v | tee log",
            "docker compose down -v|tee log",
            "echo $(docker compose down -v)",
            "(docker compose down -v)",
            "git clean -fd; echo done",
            "git clean -fd >/dev/null",
            "git clean -fd>/dev/null",
            # Documented secondary variants.
            "npm uninstall cortex-cli -g",
            "pacman -R cortex",
            "pacman -Rns cortex",
            "kill -s KILL 1234",
            "killall -9 cortex",
            "sudo pkill -f cortex",
            "sudo kill -9 1234",
            "psql -c 'TRUNCATE sessions'",
            "mysql -e 'truncate table sessions'",
        ],
    )
    def test_case_spacing_and_flag_variants_still_match(self, command: str) -> None:
        assert classify(command) is not None


class TestBenignCommands:
    @pytest.mark.parametrize(
        "command",
        [
            "echo hi",
            "git status",
            "ls -la",
            "python -m pytest",
            "grep -r rm README.md",
            "rm notes.txt",
            "docker compose down",
            "grep -r pkill src",
            "man pkill",
            "grep -r truncate docs",
            "truncate -s 0 notes.txt",
        ],
    )
    def test_benign_commands_match_no_pattern(self, command: str) -> None:
        assert classify(command) is None


class TestJournalEntries:
    def test_record_shell_appends_the_classified_command(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        store.record_shell(
            "docker compose down -v",
            pattern="docker_compose_down_volumes",
            status="denied",
            reason="removes the data volume",
        )

        (entry,) = store.entries()
        assert entry["id"]
        assert datetime.fromisoformat(entry["ts"]).tzinfo == UTC
        assert entry["action"] == "shell"
        assert entry["targets"] == ["docker compose down -v"]
        assert entry["snapshot"] is None
        assert entry["undo_recipe"] == "manual: docker compose down -v"
        assert entry["status"] == "denied"
        assert entry["pattern"] == "docker_compose_down_volumes"
        assert entry["reason"] == "removes the data volume"

    def test_record_guard_disabled_appends_an_explicit_marker(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        store.record_guard_disabled()

        (entry,) = store.entries()
        assert entry["id"]
        assert datetime.fromisoformat(entry["ts"]).tzinfo == UTC
        assert entry["action"] == "shell_guard"
        assert entry["targets"] == []
        assert entry["snapshot"] is None
        assert entry["undo_recipe"] == "n/a"
        assert entry["status"] == "disabled"
        assert entry["reason"] == "SAFETY_SHELL_GUARD=false"

    def test_entries_are_appended_not_rewritten(self, tmp_path: Path) -> None:
        store = _store(tmp_path)

        store.record_shell("x", pattern="p", status="denied", reason="r")
        store.record_shell("x", pattern="p", status="bypass", reason="r")

        lines = store.journal_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert [entry["status"] for entry in store.entries()] == ["denied", "bypass"]

    def test_undo_last_skips_a_disabled_marker(self, tmp_path: Path) -> None:
        target = tmp_path / "notes.txt"
        target.write_text("v1", encoding="utf-8")
        store = _store(tmp_path)
        entry_id = store.snapshot(target)
        assert entry_id is not None
        target.write_text("v2", encoding="utf-8")
        store.record_guard_disabled()
        marker_id = store.entries()[-1]["id"]

        result = store.undo("last")

        assert result.entry_id == entry_id
        assert target.read_text(encoding="utf-8") == "v1"
        with pytest.raises(UndoError, match=f"journal entry not found: {marker_id}"):
            store.undo(marker_id)
