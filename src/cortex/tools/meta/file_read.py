"""File reading tool."""

from pathlib import Path
from typing import Any

from cortex.tools.interfaces import ToolErrorSeverity, ToolResult
from cortex.tools.meta.base import BaseMetaTool, FileToolMixin


class FileReadTool(BaseMetaTool, FileToolMixin):
    """
    Read contents of a file.

    Supports reading text files with optional encoding.
    Binary files are read but content is truncated.
    """

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return (
            "Read the contents of a file from the filesystem. "
            "Use this to view file contents before editing or to extract information. "
            "Returns the file contents as a string."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to read.",
                },
                "encoding": {
                    "type": "string",
                    "description": "Text encoding to use (default: utf-8).",
                    "default": "utf-8",
                },
                " max_lines": {
                    "type": "integer",
                    "description": "Maximum number of lines to read (default: all).",
                    "default": 0,
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset to start reading from (0-based).",
                    "default": 0,
                },
            },
            "required": ["path"],
        }

    @property
    def tags(self) -> list[str]:
        return ["file", "read", "filesystem", "io"]

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path", "")
        encoding = arguments.get("encoding", "utf-8")
        max_lines = arguments.get("max_lines", 0)
        offset = arguments.get("offset", 0)

        # Validate path
        path_error = self._validate_path(path)
        if path_error:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=path_error,
                error_severity=ToolErrorSeverity.ERROR,
            )

        # Resolve path
        try:
            file_path = Path(path).resolve()
        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Invalid path: {e}",
                error_severity=ToolErrorSeverity.ERROR,
            )

        # Check if file exists
        if not file_path.exists():
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"File not found: {path}",
                error_severity=ToolErrorSeverity.WARNING,
            )

        # Check if it's a file (not directory)
        if not file_path.is_file():
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Path is not a file: {path}",
                error_severity=ToolErrorSeverity.WARNING,
            )

        # Read file
        try:
            if max_lines > 0:
                # Read specific lines
                with open(file_path, encoding=encoding) as f:
                    lines = f.readlines()
                    content = "".join(lines[offset : offset + max_lines])
            else:
                # Read entire file
                with open(file_path, encoding=encoding) as f:
                    content = f.read()
                    if offset > 0:
                        lines = content.split("\n")
                        content = "\n".join(lines[offset:])

            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=True,
                output=content,
                metadata={
                    "path": str(file_path),
                    "size_bytes": file_path.stat().st_size,
                    "lines_read": content.count("\n") + 1 if content else 0,
                },
            )

        except UnicodeDecodeError:
            # Binary file
            try:
                with open(file_path, "rb") as f:
                    f.read(1000)  # Read first 1KB
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    success=True,
                    output=f"<binary file: {file_path.name} ({file_path.stat().st_size} bytes)>",
                    metadata={
                        "path": str(file_path),
                        "size_bytes": file_path.stat().st_size,
                        "truncated": True,
                    },
                )
            except Exception as e:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    success=False,
                    error=f"Failed to read binary file: {e}",
                    error_severity=ToolErrorSeverity.ERROR,
                )

        except PermissionError:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Permission denied: {path}",
                error_severity=ToolErrorSeverity.ERROR,
            )

        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Failed to read file: {e}",
                error_severity=ToolErrorSeverity.ERROR,
            )
