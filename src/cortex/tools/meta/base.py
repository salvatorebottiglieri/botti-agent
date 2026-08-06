"""Base classes and utilities for meta tools."""


from cortex.tools.interfaces import Tool


class BaseMetaTool(Tool):
    """
    Base class for meta (built-in) tools.

    Provides common functionality and defaults for system tools.
    """

    @property
    def category(self) -> str:
        return "meta"

    @property
    def timeout_seconds(self) -> int:
        return 30


class FileToolMixin:
    """Mixin providing file system utilities."""

    def _validate_path(self, path: str) -> str | None:
        """
        Validate and sanitize a file path.

        Returns None if valid, error message if invalid.
        """
        if not path:
            return "Path cannot be empty"

        # Check for null bytes
        if "\x00" in path:
            return "Path contains null bytes"

        # Check for traversal attempts
        import os

        normalized = os.path.normpath(path)
        if ".." in normalized:
            return "Path traversal detected"

        return None

    def _serialize_content(self, content: str | bytes, encoding: str = "utf-8") -> str:
        """Convert content to string for ToolResult."""
        if isinstance(content, bytes):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                return f"<binary data ({len(content)} bytes)>"
        return content


class ShellToolMixin:
    """Mixin providing shell command utilities."""

    @property
    def timeout_seconds(self) -> int:
        return 120  # Shell commands may take longer

    def _build_command(self, command: str, args: list[str]) -> list[str]:
        """Build command list from command and arguments."""
        return [command] + list(args)
