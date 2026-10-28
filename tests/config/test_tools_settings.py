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
from pydantic import ValidationError

from cortex.config.loader import load_settings
from cortex.config.models import Settings
from cortex.config.tools import DEFAULT_ALLOWED_TOOLS, ToolsSettings


class TestDefaultAllowList:
    """The default must *exclude* exactly the two host-mutating tools.

    Negation: a default of "all tools" (or ``None`` meaning "no filter") fails
    the exclusion assertions — that is the whole point of the change.
    """

    def test_default_is_the_read_only_set(self) -> None:
        assert ToolsSettings(_env_file=None).allow == ["file_read", "grep", "ask_user"]

    def test_default_excludes_the_host_mutating_tools(self) -> None:
        allow = ToolsSettings(_env_file=None).allow

        assert "shell" not in allow
        assert "file_write" not in allow

    def test_default_is_a_copy_not_the_module_constant(self) -> None:
        """A per-instance list: mutating one settings object must not rewrite
        ``DEFAULT_ALLOWED_TOOLS`` for the rest of the process."""
        first = ToolsSettings(_env_file=None)
        first.allow.append("shell")

        assert "shell" not in ToolsSettings(_env_file=None).allow
        assert "shell" not in DEFAULT_ALLOWED_TOOLS

    def test_reachable_from_the_composed_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("LLM_API_KEY", "k")

        settings = Settings()

        assert isinstance(settings.tools, ToolsSettings)
        assert settings.tools.allow == ["file_read", "grep", "ask_user"]


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

        allow = ToolsSettings(_env_file=None).allow

        assert allow == []
        assert allow != list(DEFAULT_ALLOWED_TOOLS)

    def test_json_list_still_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOLS_ALLOW", '["file_read", "grep"]')

        assert ToolsSettings(_env_file=None).allow == ["file_read", "grep"]

    def test_malformed_json_fails_loudly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TOOLS_ALLOW", '["file_read"')

        with pytest.raises(ValidationError):
            ToolsSettings(_env_file=None)

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
