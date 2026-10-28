"""Meta-tool allow-list slice (issue #134).

Deny-by-default: the registered meta tools are what ``TOOLS_ALLOW`` names, and
the default set is read-only. The real policy (paths, arguments, per-call
approval) is #120.

Slices are built with ``_env_file=None`` wherever the assertion is about a
default: a nested ``BaseSettings`` reads ``.env`` itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex.config.loader import load_settings
from cortex.config.models import Settings
from cortex.config.tools import ToolsSettings


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run every test from an empty directory, with a key for the composed root.

    Keeps the module runnable with no repo-root ``.env`` (CI has none), like
    ``tests/config/test_loader.py::_hermetic_env``.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "tools-test-key")


class TestDefaultAllowList:
    """The default must *exclude* exactly the two host-mutating tools.

    Negation: a default of "all tools" (or ``None`` meaning "no filter") fails
    the equality assertion — that is the whole point of the change.
    """

    def test_default_is_the_read_only_set(self) -> None:
        assert ToolsSettings(_env_file=None).allow == ["file_read", "grep", "ask_user"]

    def test_reachable_from_the_composed_root(self) -> None:
        assert Settings().tools.allow == ["file_read", "grep", "ask_user"]


class TestAllowListDecoding:
    """``TOOLS_ALLOW`` is CSV: pydantic-settings would otherwise JSON-decode a
    list field from the environment, so ``TOOLS_ALLOW=file_read`` and an
    explicit ``TOOLS_ALLOW=""`` would raise instead of parsing."""

    def test_single_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOLS_ALLOW", "file_read")

        assert ToolsSettings(_env_file=None).allow == ["file_read"]

    def test_comma_separated_names_strip_whitespace(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOOLS_ALLOW", "file_read, grep")

        assert ToolsSettings(_env_file=None).allow == ["file_read", "grep"]

    def test_opt_in_to_the_host_mutating_tools(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Negation: an allow-list intersected with the read-only default would
        still pass the two tests above and silently ignore the opt-in."""
        monkeypatch.setenv("TOOLS_ALLOW", "file_read,grep,ask_user,shell,file_write")

        assert ToolsSettings(_env_file=None).allow == [
            "file_read",
            "grep",
            "ask_user",
            "shell",
            "file_write",
        ]

    def test_empty_string_is_an_empty_list_not_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit empty list is a decision: advertise no tool. Falling back
        to the default would silently re-grant capabilities the operator
        removed."""
        monkeypatch.setenv("TOOLS_ALLOW", "")

        assert ToolsSettings(_env_file=None).allow == []

    def test_yaml_section_reaches_the_root(self, tmp_path: Path) -> None:
        """The ``tools:`` section of ``config.yaml`` is a loader section, not
        just a field name (``_SETTINGS_SECTIONS``)."""
        config = tmp_path / "config.yaml"
        config.write_text("tools:\n  allow:\n    - file_read\n", encoding="utf-8")

        settings = load_settings(config)

        assert settings.tools.allow == ["file_read"]


class TestToolsNamespaceIsolation:
    """The slice reads ``TOOLS_*``, never the bare field name.

    Negation: without ``env_prefix`` on the slice, a process-wide ``ALLOW``
    would configure the registered tool set for the whole process.
    """

    def test_env_namespace_is_prefixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOLS_ALLOW", "file_read")
        monkeypatch.setenv("ALLOW", "shell,file_write")

        assert ToolsSettings(_env_file=None).allow == ["file_read"]
