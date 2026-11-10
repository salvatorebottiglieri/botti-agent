"""File writing tool."""

import logging
from pathlib import Path
from typing import Any

from cortex.config.safety import SafetySettings
from cortex.safety.journal import SafetyStore, UndoError
from cortex.tools.interfaces import ToolErrorSeverity, ToolResult
from cortex.tools.meta.base import BaseMetaTool, FileToolMixin

logger = logging.getLogger(__name__)


class FileWriteTool(BaseMetaTool, FileToolMixin):
    """
    Write content to a file.

    Creates parent directories if needed.
    Will overwrite existing files.
    """

    def __init__(self, safety: SafetySettings | None = None) -> None:
        self._store = SafetyStore(safety) if safety is not None else None

    def _abort_snapshot(self, snapshot_id: str | None) -> None:
        """Mark a snapshot whose write failed so undo does not consume it."""
        if snapshot_id is None or self._store is None:
            return
        try:
            self._store.abort(snapshot_id)
        except Exception as exc:  # the write error is the one the caller sees
            logger.warning("could not abort snapshot %s: %s", snapshot_id, exc)

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return (
            "Write content to a file. Creates the file and parent directories "
            "if they don't exist. Will overwrite existing files. "
            "Use this to create new files or update existing ones."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file to write."
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file."
                },
                "encoding": {
                    "type": "string",
                    "description": "Text encoding to use (default: utf-8).",
                    "default": "utf-8"
                },
                "append": {
                    "type": "boolean",
                    "description": "If True, append to existing file instead of overwriting.",
                    "default": False
                }
            },
            "required": ["path", "content"]
        }

    @property
    def tags(self) -> list[str]:
        return ["file", "write", "filesystem", "io"]

    @property
    def idempotent(self) -> bool:
        return False  # Writing has side effects

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        path = arguments.get("path", "")
        content = arguments.get("content", "")
        encoding = arguments.get("encoding", "utf-8")
        append = arguments.get("append", False)

        # Validate path
        path_error = self._validate_path(path)
        if path_error:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=path_error,
                error_severity=ToolErrorSeverity.ERROR
            )

        # Validate content
        if content is None:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error="Content cannot be None",
                error_severity=ToolErrorSeverity.ERROR
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
                error_severity=ToolErrorSeverity.ERROR
            )

        # Create parent directories
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Failed to create parent directory: {e}",
                error_severity=ToolErrorSeverity.ERROR
            )

        # Snapshot an existing file before an overwrite: a failed snapshot
        # refuses the write (fail closed, ADR-0021). New files and appends are
        # not destructive and are not snapshotted.
        snapshot_id: str | None = None
        if not append and file_path.exists() and self._store is not None:
            try:
                snapshot_id = self._store.snapshot(file_path)
            except UndoError as exc:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    success=False,
                    error=f"Snapshot failed; refusing to overwrite {path}: {exc}",
                    error_severity=ToolErrorSeverity.ERROR
                )

        # Write file
        mode = 'a' if append else 'w'
        try:
            with open(file_path, mode, encoding=encoding) as f:
                f.write(content)

            metadata: dict[str, Any] = {
                "path": str(file_path),
                "bytes_written": len(content.encode(encoding)),
                "append": append
            }
            if snapshot_id is not None:
                metadata["snapshot_id"] = snapshot_id

            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=True,
                output=f"Successfully wrote {len(content)} characters to {path}",
                metadata=metadata
            )

        except PermissionError:
            self._abort_snapshot(snapshot_id)
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Permission denied: {path}",
                error_severity=ToolErrorSeverity.ERROR
            )

        except Exception as e:
            self._abort_snapshot(snapshot_id)
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Failed to write file: {e}",
                error_severity=ToolErrorSeverity.ERROR
            )
