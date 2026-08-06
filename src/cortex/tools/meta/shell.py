"""Shell command execution tool."""

import asyncio
import os
from typing import Any

from cortex.tools.interfaces import ToolErrorSeverity, ToolResult
from cortex.tools.meta.base import BaseMetaTool


class ShellTool(BaseMetaTool):
    """
    Execute shell commands.

    Supports running system commands with optional working directory.
    Captures stdout and stderr.
    """

    @property
    def name(self) -> str:
        return "shell"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command on the system. "
            "Returns stdout, stderr, and exit code. "
            "Use for running scripts, system commands, or git operations."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."},
                "working_dir": {
                    "type": "string",
                    "description": "Working directory for command execution (optional).",
                    "default": None,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60).",
                    "default": 60,
                },
            },
            "required": ["command"],
        }

    @property
    def tags(self) -> list[str]:
        return ["shell", "command", "system", "execute"]

    @property
    def timeout_seconds(self) -> int:
        return 120  # Shell commands may take longer

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        command = arguments.get("command", "")
        working_dir = arguments.get("working_dir")
        timeout = arguments.get("timeout", 60)

        if not command:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error="Command cannot be empty",
                error_severity=ToolErrorSeverity.ERROR,
            )

        # Validate command (basic security - no newlines in command)
        if "\n" in command or "\r" in command:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error="Command cannot contain newlines",
                error_severity=ToolErrorSeverity.ERROR,
            )

        # Determine working directory
        if working_dir:
            try:
                cwd = os.path.abspath(working_dir)
                if not os.path.isdir(cwd):
                    return ToolResult(
                        tool_call_id="",
                        tool_name=self.name,
                        success=False,
                        error=f"Working directory not found: {working_dir}",
                        error_severity=ToolErrorSeverity.ERROR,
                    )
            except Exception as e:
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    success=False,
                    error=f"Invalid working directory: {e}",
                    error_severity=ToolErrorSeverity.ERROR,
                )
        else:
            cwd = os.getcwd()

        try:
            # Run command using asyncio subprocess
            process = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=cwd
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    tool_call_id="",
                    tool_name=self.name,
                    success=False,
                    error=f"Command timed out after {timeout} seconds",
                    error_severity=ToolErrorSeverity.ERROR,
                    metadata={
                        "timeout": timeout,
                        "command": command[:100] + "..." if len(command) > 100 else command,
                    },
                )

            stdout = stdout_data.decode("utf-8", errors="replace") if stdout_data else ""
            stderr = stderr_data.decode("utf-8", errors="replace") if stderr_data else ""

            output_parts = []
            if stdout:
                output_parts.append(f"STDOUT:\n{stdout}")
            if stderr:
                output_parts.append(f"STDERR:\n{stderr}")

            output = "\n".join(output_parts) if output_parts else "(no output)"

            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=process.returncode == 0,
                output=output,
                metadata={"exit_code": process.returncode, "cwd": cwd},
            )

        except Exception as e:
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error=f"Failed to execute command: {e}",
                error_severity=ToolErrorSeverity.ERROR,
            )
