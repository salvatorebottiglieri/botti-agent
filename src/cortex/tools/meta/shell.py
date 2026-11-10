"""Shell command execution tool."""

import asyncio
import logging
import os
from typing import Any

from cortex.config.safety import SafetySettings
from cortex.safety.journal import SafetyStore
from cortex.safety.shell_guard import classify
from cortex.tools.interfaces import ToolErrorSeverity, ToolResult
from cortex.tools.meta.base import BaseMetaTool

logger = logging.getLogger(__name__)


class ShellTool(BaseMetaTool):
    """
    Execute shell commands.

    Supports running system commands with optional working directory.
    Captures stdout and stderr.
    """

    def __init__(self, safety: SafetySettings | None = None) -> None:
        self._store = SafetyStore(safety) if safety is not None else None
        self._guard_enabled = safety.shell_guard if safety is not None else False
        if self._store is not None and not self._guard_enabled:
            logger.warning(
                "SAFETY_SHELL_GUARD=false: the shell deny-list is disabled; "
                "matched commands are journaled and executed"
            )

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

        store = self._store
        if store is not None:
            pattern = classify(command)
            if pattern is not None:
                if self._guard_enabled:
                    try:
                        store.record_shell(
                            command,
                            pattern=pattern.name,
                            status="denied",
                            reason=pattern.reason,
                        )
                    except Exception as exc:
                        logger.warning(
                            "could not journal the denied shell command %r: %s",
                            command,
                            exc,
                        )
                        return ToolResult(
                            tool_call_id="",
                            tool_name=self.name,
                            success=False,
                            error=(
                                "Shell guard could not journal the denial; "
                                "refusing to run the command"
                            ),
                            error_severity=ToolErrorSeverity.ERROR,
                        )
                    return ToolResult(
                        tool_call_id="",
                        tool_name=self.name,
                        success=False,
                        error=(
                            f"Blocked by the shell safety guard: {pattern.reason} "
                            f"(pattern: {pattern.name}). Run it manually, or set "
                            "SAFETY_SHELL_GUARD=false (journaled)."
                        ),
                        error_severity=ToolErrorSeverity.ERROR,
                        metadata={
                            "pattern": pattern.name,
                            "reason": pattern.reason,
                            "command": command,
                        },
                    )

                # The bypass is explicit in the config but must never be
                # silent: a failed journal write refuses the command.
                try:
                    store.record_shell(
                        command,
                        pattern=pattern.name,
                        status="bypass",
                        reason=pattern.reason,
                    )
                except Exception as exc:
                    logger.warning(
                        "could not journal the shell-guard bypass for %r: %s",
                        command,
                        exc,
                    )
                    return ToolResult(
                        tool_call_id="",
                        tool_name=self.name,
                        success=False,
                        error=(
                            "SAFETY_SHELL_GUARD is disabled but the bypass "
                            "could not be journaled; refusing to run the command"
                        ),
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
