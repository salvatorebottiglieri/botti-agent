"""Table-driven deny-list for destructive shell commands (ADR-0021, #148).

The guard is a **mitigation**, not a sandbox: a pattern match refuses the
command with an explanation, an unknown command still runs. A deny-list also
false-positives on quoted text (``echo "rm -rf /"``); that is accepted, and the
journaled :class:`cortex.config.safety.SafetySettings` bypass exists for the
operator who needs the command. #120's host sidecar is the real trust boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ShellPattern:
    """One deny-list entry: a compiled pattern and why it is destructive."""

    name: str
    pattern: re.Pattern[str]
    reason: str
    example: str


def _pattern(name: str, expression: str, reason: str, example: str) -> ShellPattern:
    """Compile an entry once, case-insensitively, with its human explanation."""
    return ShellPattern(
        name=name,
        pattern=re.compile(expression, re.IGNORECASE),
        reason=reason,
        example=example,
    )


SHELL_PATTERNS: tuple[ShellPattern, ...] = (
    _pattern(
        "rm_recursive_force",
        # Both flags in one token (-rf) or in separate tokens (-r -f); the
        # lookaheads scan one command segment, never across ; | &.
        r"\brm\b(?=[^\n;|&]*(?:\s-[a-zA-Z]*r[a-zA-Z]*(?=\s|$)|--recursive\b))"
        r"(?=[^\n;|&]*(?:\s-[a-zA-Z]*f[a-zA-Z]*(?=\s|$)|--force\b))",
        "recursive forced delete permanently removes files; there is no undo",
        "rm -rf /tmp/cortex-safety-check",
    ),
    _pattern(
        "docker_compose_down_volumes",
        r"\bdocker(?:-compose|\s+compose)\b[^\n;|&]*?\b(?:down|rm)\b"
        r"[^\n;|&]*(?:\s-v(?=\s|$)|--volumes\b)",
        "removes containers and their named volumes; the database data lives in a volume",
        "docker compose down -v",
    ),
    _pattern(
        "docker_volume_remove",
        r"\bdocker\s+volume\s+(?:rm|prune)\b",
        "deletes Docker volumes, including database data",
        "docker volume rm cortex_pgdata",
    ),
    _pattern(
        "docker_system_prune",
        r"\bdocker\s+system\s+prune\b",
        "prunes containers, images and networks; with --volumes it also deletes data",
        "docker system prune -af --volumes",
    ),
    _pattern(
        "git_reset_hard",
        r"\bgit\b[^\n;|&]*\breset\b[^\n;|&]*--hard\b",
        "discards uncommitted work irreversibly",
        "git reset --hard HEAD~1",
    ),
    _pattern(
        "git_clean_force",
        r"\bgit\b[^\n;|&]*\bclean\b[^\n;|&]*(?:\s-[a-zA-Z]*f[a-zA-Z]*(?=\s|$)|--force\b)",
        "deletes untracked files and directories; they are not recoverable",
        "git clean -fdx",
    ),
    _pattern(
        "sql_drop",
        r"\bdrop\s+(?:table|database|schema)\b",
        "drops a database object and all of its data",
        "DROP TABLE sessions",
    ),
    _pattern(
        "sql_truncate",
        r"\btruncate\b",
        "truncates a table, deleting every row",
        "TRUNCATE TABLE sessions",
    ),
    _pattern(
        "mkfs",
        r"\bmkfs(?:\.[a-z0-9]+)?\b",
        "formats a filesystem, destroying everything on the device",
        "mkfs.ext4 /dev/sdb1",
    ),
    _pattern(
        "dd_of",
        r"\bdd\b[^\n;|&]*\bof=\S+",
        "writes raw bytes to the output file or device, overwriting it",
        "dd if=/dev/zero of=/dev/sda bs=1M",
    ),
    _pattern(
        "package_manager_remove",
        r"\b(?:apt|apt-get|dnf|yum|pacman)\s+(?:remove|purge|erase)\b",
        "removes installed system packages and their dependencies",
        "apt-get purge postgresql",
    ),
    _pattern(
        "npm_global_uninstall",
        r"\bnpm\s+uninstall\s+(?:-g|--global)\b",
        "removes globally installed packages",
        "npm uninstall -g cortex-cli",
    ),
    _pattern(
        "pkill",
        r"\bpkill\b",
        "kills processes by name and may kill the agent's own session",
        "pkill -f cortex",
    ),
    _pattern(
        "kill_9",
        r"\bkill\s+(?:-\w+\s+)*-(?:9|kill|sigkill)\b",
        "SIGKILL cannot be caught, so the process cannot clean up",
        "kill -9 1234",
    ),
)


def classify(command: str) -> ShellPattern | None:
    """Return the first matching pattern, or ``None`` for an unknown command."""
    for pattern in SHELL_PATTERNS:
        if pattern.pattern.search(command):
            return pattern
    return None
