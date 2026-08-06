"""Grep tool for searching file contents."""

import re
from pathlib import Path
from typing import Any

from cortex.tools.interfaces import ToolErrorSeverity, ToolResult
from cortex.tools.meta.base import BaseMetaTool, FileToolMixin


class GrepTool(BaseMetaTool, FileToolMixin):
    """
    Search for patterns in files.

    Supports regex patterns, case-insensitive search, and line numbers.
    Returns matching lines with file path and line numbers.
    """

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search for text patterns in files. "
            "Supports regular expressions and file globbing. "
            "Returns matching lines with file paths and line numbers."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "The pattern to search for (supports regex).",
                },
                "path": {"type": "string", "description": "Directory or file path to search in."},
                "file_pattern": {
                    "type": "string",
                    "description": "File glob pattern to filter files (e.g., '*.py').",
                    "default": "*",
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether search is case-sensitive.",
                    "default": False,
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Maximum number of matches to return.",
                    "default": 100,
                },
                "include_binary": {
                    "type": "boolean",
                    "description": "Whether to include binary files in search.",
                    "default": False,
                },
            },
            "required": ["pattern", "path"],
        }

    @property
    def tags(self) -> list[str]:
        return ["search", "grep", "find", "regex"]

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        pattern = arguments.get("pattern", "")
        path = arguments.get("path", "")
        file_pattern = arguments.get("file_pattern", "*")
        case_sensitive = arguments.get("case_sensitive", False)
        max_matches = arguments.get("max_matches", 100)
        include_binary = arguments.get("include_binary", False)

        if not pattern:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error="Pattern cannot be empty",
                error_severity=ToolErrorSeverity.ERROR,
            )

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
            search_path = Path(path).resolve()
        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Invalid path: {e}",
                error_severity=ToolErrorSeverity.ERROR,
            )

        if not search_path.exists():
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Path not found: {path}",
                error_severity=ToolErrorSeverity.WARNING,
            )

        # Compile regex
        try:
            flags = 0 if case_sensitive else re.IGNORECASE
            regex = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Invalid regex pattern: {e}",
                error_severity=ToolErrorSeverity.ERROR,
            )

        # Search
        matches: list[tuple[str, int, str]] = []
        files_searched = 0

        try:
            if search_path.is_file():
                # Search single file
                files_searched = 1
                matches.extend(self._search_file(search_path, regex, max_matches - len(matches)))
            else:
                # Search directory
                for file_path in search_path.rglob(file_pattern):
                    if file_path.is_file() and not include_binary:
                        # Check if binary
                        try:
                            with open(file_path, "rb") as f:
                                f.read(1024)
                        except Exception:
                            continue

                    files_searched += 1
                    matches.extend(self._search_file(file_path, regex, max_matches - len(matches)))

                    if len(matches) >= max_matches:
                        break

            # Format output
            if matches:
                output = f"Found {len(matches)} matches in {files_searched} files:\n\n"
                current_file = None
                for match in matches:
                    match_file, line_num, line_text = match
                    if match_file != current_file:
                        output += f"\n{match_file}:\n"
                        current_file = match_file
                    output += f"  {line_num}: {line_text}\n"
            else:
                output = f"No matches found for '{pattern}' in {path}"

            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=True,
                output=output,
                metadata={
                    "files_searched": files_searched,
                    "match_count": len(matches),
                    "pattern": pattern,
                },
            )

        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Search failed: {e}",
                error_severity=ToolErrorSeverity.ERROR,
            )

    def _search_file(
        self, file_path: Path, regex: re.Pattern[str], max_matches: int
    ) -> list[tuple[str, int, str]]:
        """Search a single file for regex matches."""
        matches = []

        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                for line_num, line in enumerate(f, 1):
                    if regex.search(line):
                        matches.append((str(file_path), line_num, line.rstrip()))
                        if len(matches) >= max_matches:
                            break
        except Exception:
            pass

        return matches
