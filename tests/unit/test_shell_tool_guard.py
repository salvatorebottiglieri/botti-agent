"""``ShellTool`` shell-guard enforcement (#148).

Discriminating witnesses:

* a destructive command is denied, journaled and **never spawned**;
* a benign command spawns and writes no journal entry;
* a bypassed command spawns and is journaled as ``bypass``;
* a failed journal write refuses both denial and bypass (no silent execution);
* ``ShellTool()`` (eval sandbox, existing tests) has no guard and no store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex.config.safety import SafetySettings
from cortex.safety import SafetyStore
from cortex.safety.shell_guard import SHELL_PATTERNS, ShellPattern, classify
from cortex.tools.meta import ShellTool, get_meta_tools


class _FakeProcess:
    returncode = 0

    async def communicate(self) -> tuple[bytes, bytes]:
        return (b"ok\n", b"")

    def kill(self) -> None:
        return None

    async def wait(self) -> int:
        return 0


def _settings(tmp_path: Path, *, shell_guard: bool = True) -> SafetySettings:
    return SafetySettings(
        store_dir=tmp_path / "store",
        shell_guard=shell_guard,
        _env_file=None,
    )


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_spawn(command: str, **kwargs: object) -> _FakeProcess:
        calls.append((command, kwargs))
        return _FakeProcess()

    monkeypatch.setattr(
        "cortex.tools.meta.shell.asyncio.create_subprocess_shell", fake_spawn
    )
    return calls


class TestGuardEnabled:
    async def test_destructive_command_is_denied_journaled_and_not_spawned(
        self, tmp_path: Path, spawned: list[tuple[str, dict[str, object]]]
    ) -> None:
        tool = ShellTool(_settings(tmp_path))

        result = await tool.execute({"command": "docker compose down -v"})

        assert result.success is False
        pattern = classify("docker compose down -v")
        assert pattern is not None
        assert result.error is not None
        assert "Blocked by the shell safety guard" in result.error
        assert pattern.reason in result.error
        assert f"pattern: {pattern.name}" in result.error
        assert "SAFETY_SHELL_GUARD=false" in result.error
        assert result.metadata["pattern"] == pattern.name
        assert result.metadata["reason"] == pattern.reason
        assert result.metadata["command"] == "docker compose down -v"
        assert spawned == []

        store = tool._store
        assert store is not None
        (entry,) = store.entries()
        assert entry["action"] == "shell"
        assert entry["status"] == "denied"
        assert entry["pattern"] == pattern.name
        assert entry["reason"] == pattern.reason
        assert entry["targets"] == ["docker compose down -v"]

    @pytest.mark.parametrize("pattern", SHELL_PATTERNS, ids=[p.name for p in SHELL_PATTERNS])
    async def test_every_pattern_example_is_blocked_without_spawning(
        self,
        pattern: ShellPattern,
        tmp_path: Path,
        spawned: list[tuple[str, dict[str, object]]],
    ) -> None:
        tool = ShellTool(_settings(tmp_path))

        result = await tool.execute({"command": pattern.example})

        assert result.success is False
        assert result.metadata["pattern"] == pattern.name
        assert spawned == []
        store = tool._store
        assert store is not None
        (entry,) = store.entries()
        assert entry["pattern"] == pattern.name
        assert entry["targets"] == [pattern.example]

    async def test_benign_command_spawns_and_writes_no_journal_entry(
        self, tmp_path: Path, spawned: list[tuple[str, dict[str, object]]]
    ) -> None:
        tool = ShellTool(_settings(tmp_path))

        result = await tool.execute({"command": "echo hi"})

        assert result.success is True, result.error
        assert len(spawned) == 1
        store = tool._store
        assert store is not None
        assert store.entries() == []
        assert not store.journal_path.exists()

    async def test_unknown_destructive_looking_rm_without_recursion_runs(
        self, tmp_path: Path, spawned: list[tuple[str, dict[str, object]]]
    ) -> None:
        tool = ShellTool(_settings(tmp_path))

        result = await tool.execute({"command": "rm notes.txt"})

        assert result.success is True, result.error
        assert [command for command, _ in spawned] == ["rm notes.txt"]
        store = tool._store
        assert store is not None
        assert store.entries() == []

    async def test_journal_failure_refuses_the_denial(
        self,
        tmp_path: Path,
        spawned: list[tuple[str, dict[str, object]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_append(self: SafetyStore, entry: dict[str, object]) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(SafetyStore, "_append", fail_append)
        tool = ShellTool(_settings(tmp_path))

        result = await tool.execute({"command": "rm -rf /tmp/cortex-check"})

        assert result.success is False
        assert result.error == (
            "Shell guard could not journal the denial; refusing to run the command"
        )
        assert spawned == []


class TestGuardDisabled:
    async def test_construction_journals_the_bypass_and_warns(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        tool = ShellTool(_settings(tmp_path, shell_guard=False))

        store = tool._store
        assert store is not None
        (entry,) = store.entries()
        assert entry["action"] == "shell_guard"
        assert entry["status"] == "disabled"
        assert entry["reason"] == "SAFETY_SHELL_GUARD=false"
        assert "SAFETY_SHELL_GUARD" in caplog.text

    async def test_matched_command_spawns_and_is_journaled_as_bypass(
        self,
        tmp_path: Path,
        spawned: list[tuple[str, dict[str, object]]],
    ) -> None:
        tool = ShellTool(_settings(tmp_path, shell_guard=False))

        result = await tool.execute({"command": "rm -rf /tmp/cortex-check"})

        assert result.success is True, result.error
        assert [command for command, _ in spawned] == ["rm -rf /tmp/cortex-check"]
        store = tool._store
        assert store is not None
        assert [entry["action"] for entry in store.entries()] == [
            "shell_guard",
            "shell",
        ]
        bypass = store.entries()[1]
        assert bypass["status"] == "bypass"
        assert bypass["pattern"] == "rm_recursive_force"
        assert bypass["targets"] == ["rm -rf /tmp/cortex-check"]

    async def test_journal_failure_refuses_the_bypass(
        self,
        tmp_path: Path,
        spawned: list[tuple[str, dict[str, object]]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_append(self: SafetyStore, entry: dict[str, object]) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(SafetyStore, "_append", fail_append)
        tool = ShellTool(_settings(tmp_path, shell_guard=False))

        result = await tool.execute({"command": "rm -rf /tmp/cortex-check"})

        assert result.success is False
        assert result.error == (
            "SAFETY_SHELL_GUARD is disabled but the bypass could not be "
            "journaled; refusing to run the command"
        )
        assert spawned == []


class TestNoSettings:
    async def test_without_settings_there_is_no_store_and_commands_run(
        self, spawned: list[tuple[str, dict[str, object]]]
    ) -> None:
        tool = ShellTool()

        result = await tool.execute({"command": "echo hi"})

        assert result.success is True, result.error
        assert [command for command, _ in spawned] == ["echo hi"]
        assert tool._store is None


class TestWiring:
    def test_get_meta_tools_injects_safety_into_shell(self, tmp_path: Path) -> None:
        tools = {tool.name: tool for tool in get_meta_tools(_settings(tmp_path))}
        shell = tools["shell"]

        assert isinstance(shell, ShellTool)
        assert shell._store is not None
