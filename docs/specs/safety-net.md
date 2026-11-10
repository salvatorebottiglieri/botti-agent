# Safety Net

> Single source of truth for the mechanisms that make an agent's destructive
> action recoverable. Rationale: ADR-0021. Work items: #145 (umbrella), #146
> (DB backup, implemented), #147 (file snapshots, implemented), #148 (shell
> guard, implemented).
>
> **Status: slices 1 (#146), 2 (#147) and 3 (#148) implemented.**

## Why

On 2026-10-28 a delegated agent ran `docker compose down -v` while verifying a
UI fix and destroyed the dev database: the volume was recreated and 30 sessions
were unrecoverable. The product had no guard and no backup. The safety net is
the answer to that incident, in value order: prevent, snapshot, journal, undo.

## Threat model

The agent acts through three seams; each needs a net.

| Seam | What it can destroy | Net today |
|---|---|---|
| `shell` (opt-in since #134) | anything: `rm -rf`, `docker compose down -v`, `git reset --hard`, `DROP TABLE` | deny-list + journal (#148) |
| `file_write` | overwrites an existing file, no copy | none |
| migrations / DDL at boot | schema and data | pre-migration dump (#146) |

## Decisions (2026-11-10, owner)

1. **Store location:** `~/.local/share/cortex/` (XDG user data), outside the
   repo and outside every Docker volume. A backup inside the volume dies with
   `down -v`, which is the incident this exists for; a repo path dies with
   `rm -rf` or a fresh clone.
2. **Pre-migration dump failure:** fail closed. If there are pending migrations
   and the dump fails, startup aborts. `SAFETY_BACKUP_ON_MIGRATION=false` skips
   the dump entirely; `SAFETY_REQUIRE_BACKUP_BEFORE_MIGRATION=false` keeps the
   attempt but continues on failure (loud warning). The default is abort.
3. **Shell guard policy (v1):** deny-list + explicit denial + journal. Known
   destructive patterns are refused with an explanation; the exact command and
   matched pattern are journaled. A config bypass exists and is journaled so it
   is never silent. Consent via `ask_user` is deferred to #120's host sidecar,
   which is the real trust boundary.

## Store layout

```
~/.local/share/cortex/            # SAFETY_STORE_DIR, mode 0700
├── backups/
│   └── cortex-20261110T101500Z.dump
├── snapshots/                    # #147
│   └── <journal-id>/...
└── journal.jsonl                 # #147/#148, append-only
```

A backup id is the file stem (`cortex-20261110T101500Z`). `backup:restore`
accepts an id or a path.

The compose `cortex` service still runs as root for now: it writes
`/mosquitto/config/passwd.conf`, a volume owned by mosquitto, and the
non-root container is #120's companion change. A one-shot `safety-store`
service creates `backups/` and `snapshots/` and chowns the host store to the
host user; `BackupService` then best-effort chowns each new dump to the store
directory's owner (mode `0600`) so the host CLI can read it.
The host CLI uses the same directory (`SAFETY_STORE_DIR`, default
`~/.local/share/cortex`), so a pre-migration dump taken in the container is
restorable from the host.

Compose notes: the mount path is `~/.local/share/cortex` (the default);
`SAFETY_HOST_STORE_DIR` is not wired, so a custom `SAFETY_STORE_DIR` applies to
host CLI runs only. The init uses `${UID:-1000}:${GID:-1000}`; on a host whose
user is not 1000, export `UID`/`GID` or set them in `.env`. Rootless Docker
maps UIDs, and NFS root-squash can refuse the chown: the dump then stays
root-owned and the host CLI cannot read it — a known residual until the
non-root/#120 work lands.

## Configuration (`SAFETY_` namespace)

| Field | Default | Meaning |
|---|---|---|
| `SAFETY_STORE_DIR` | `~/.local/share/cortex` | Backup/snapshot/journal root |
| `SAFETY_BACKUP_RETENTION` | `14` | Newest N backups kept; `0` disables pruning |
| `SAFETY_BACKUP_ON_MIGRATION` | `true` | Dump before applying pending migrations |
| `SAFETY_REQUIRE_BACKUP_BEFORE_MIGRATION` | `true` | Abort when the pre-migration dump fails |
| `SAFETY_SNAPSHOT_ENABLED` | `true` | Snapshot an existing file before `file_write` overwrites it |
| `SAFETY_SHELL_GUARD` | `true` | Deny-list guard for the opt-in `shell` tool; `false` is journaled |

Slice #148 adds no other field: the guard is table-driven in
`src/cortex/safety/shell_guard.py`, so adding a pattern needs no configuration.

## Slice 1 — DB backup/restore (#146)

### `cortex backup:create`

- `pg_dump --format=custom`, timestamped UTC filename
  (`cortex-YYYYmmddTHHMMSSZ.dump`).
- Written to a temporary file in the backups directory and atomically renamed:
  a killed process never leaves a partial file that retention could count.
- File mode `0600`; the directory `0700`.
- Retention: keep the newest `backup_retention` files; never delete the file
  just created; `0` keeps everything.
- **Transport:** prefer `pg_dump` on `PATH`. If absent and the target is the
  local compose Postgres, use `docker compose exec -T postgres pg_dump`.
  Otherwise fail with a clear message (no silent fallback).
- No secret (password, token, URL credentials) is printed. The connection
  password travels in `PGPASSWORD`, never on the command line.
- Output: path and size.

### `cortex backup:restore <id|path>`

- Resolves an id in the store or an explicit path.
- Confirmation prompt by default; `--yes` skips it.
- Refuses while the app is using the target database (any connection other than
  the check itself) unless `--force`.
- `pg_restore --clean --if-exists --no-owner --no-privileges`; the same
  host/Docker transport rule as create (Docker path streams the file on stdin).
- `--database` (restore into a scratch DB) is deferred: it needs database
  creation privileges and is not required by the incident.

### Automatic snapshot before migrations

`run_migrations(settings)`:

1. ensure `schema_migrations` exists;
2. compute the pending versions;
3. if none, apply nothing and take no dump;
4. if pending and `backup_on_migration`, create a backup tagged
   `pre-migration`; log its path;
5. if the dump fails, abort when `require_backup_before_migration` (default),
   otherwise log an error and continue;
6. apply the pending migrations, each in its own transaction.

The Cortex image installs PostgreSQL 16 client tools so the in-container boot
can dump. A host running `cortex` directly needs `pg_dump` on `PATH` (the CLI
falls back to the compose container for the CLI commands).

## Slice 2 — file snapshots + undo (#147, implemented)

- Before `file_write` overwrites an existing file (not `append`, not a new
  file), copy the previous content to `snapshots/<journal-id>/original`
  (mode `0600`; the snapshots directory is `0700`).
- Append one JSON line per destructive action to `journal.jsonl`
  (mode `0600`):
  `{id, ts, action, targets, snapshot, mode, undo_recipe, status}`. For a file
  overwrite: `action: "file_write"`, `targets: [resolved path]`, `snapshot`
  **relative** to `snapshots/` (`<id>/original`), `mode` the original permission
  bits, `status: "applied"`.
- If the write then fails (including a post-open encoding or ENOSPC error
  that already truncated the target), the tool restores the snapshot over the
  target **before** appending an abort marker
  `{id, ts, action: "abort", target_id, targets, status: "aborted"}`; `undo`
  skips the entry. If that rollback itself fails, no abort marker is written:
  the entry stays applied so `undo` can still recover from the snapshot.
- `cortex undo last` / `cortex undo <id>` restores the snapshot (and its
  recorded mode), appends an undo marker
  `{id, ts, action: "undo", target_id, status: "undone"}`, and prints the
  restored target. The journal stays append-only: status is derived by scanning
  markers, never by rewriting a line.
- `undo last` targets the newest entry that is neither undone nor aborted. If
  that entry has no snapshot (a future shell/db action), the command prints its
  `undo_recipe` and exits non-zero instead of pretending to undo it.
- The snapshot path is resolved relative to the store and must stay inside it;
  a journal entry pointing outside `snapshots/` is rejected. The **target**
  path is the writer-side absolute path: `undo` must run in the same filesystem
  namespace as the `file_write` (host-local Cortex is the supported deployment;
  host undo of a container-written entry waits for #120's host sidecar).
- Snapshot failure policy: **fail closed** for an overwrite — the write is
  refused rather than performed without a copy. A new file needs no snapshot
  and writes no journal entry; `append` is not an overwrite and is not
  snapshotted.
- Two consecutive overwrites produce two entries; `undo last` reverts them in
  reverse order (one call per overwrite).
- `SAFETY_SNAPSHOT_ENABLED=false` disables the feature (default on) and creates
  no store directories. Disabling is explicit and documented; it is the same
  escape hatch style as the shell guard bypass.
- New artifacts are best-effort chowned to the store owner, so a root container
  writing through the compose bind mount leaves files readable by the host CLI.
- The store never lives inside the repo, so `git status` stays clean.
- Retention of old snapshots/journal entries is deferred: the journal is the
  undo history and pruning it silently would break `undo`.

## Slice 3 — shell guard (#148, implemented)

- A table-driven deny-list in `src/cortex/safety/shell_guard.py`; every entry
  carries `name`, a compiled `pattern`, the `reason`, and an `example` command
  it is meant to stop. Examples: `rm -rf`, `docker (compose )?(down|rm)` with
  `-v`/`--volumes`, `docker volume rm`, `docker system prune`,
  `git reset --hard`, `git clean -fdx`, `DROP (TABLE|DATABASE|SCHEMA)`,
  `TRUNCATE TABLE` (or a SQL-client command containing `truncate`), `mkfs`,
  `dd of=`, package-manager global uninstalls,
  `pkill`/`kill -9`/`killall -9` as command words.
- A match returns an error `ToolResult` with the matched pattern name and
  reason; the command is not executed. Benign commands write nothing and pay
  one `classify` call (one pass over the pattern table). The deny-list fails
  open on an unknown command: it is a mitigation, not a sandbox.
- Every classified command appends a journal entry
  `{id, ts, action: "shell", targets: [command], snapshot: null,
  undo_recipe, status: "denied"|"bypass", pattern, reason}`. A denied command
  is journaled before the error is returned; a bypassed command is journaled
  before execution. If the journal write fails, the command is refused — denied
  or bypassed alike — so no matched command runs silently.
- Bypass: `SAFETY_SHELL_GUARD=false` disables the guard for the process. The
  startup logs a warning and, when the `shell` tool is registered, the store
  records a `{action: "shell_guard", targets: [], status: "disabled",
  reason: "SAFETY_SHELL_GUARD=false"}` entry; every matched command that then
  executes is journaled with `status: "bypass"`. It is never silent. A
  `shell_guard` marker is history: `undo last` skips it.
- `ShellTool(safety=None)` has no guard (eval sandbox and existing tests);
  production injects `settings.safety` through `register_meta_tools`.
- Tests are table-driven from the same list: each `ShellPattern.example` must
  match its pattern, and a benign table must match none. A pattern without a
  test fails.
- #120's host sidecar with policy + consent remains the real trust boundary;
  this slice is the in-container mitigation for the opt-in `shell` tool.

## Invariants

1. A backup and the journal live outside the repo and outside every Docker
   volume (`SAFETY_STORE_DIR`), so `down -v` and `rm -rf` cannot destroy both
   the data and its net.
2. A successful `backup:create` is restorable with `backup:restore`, and the
   restored row counts match the source.
3. A pending migration without a successful pre-migration dump never runs when
   `require_backup_before_migration` is true.
4. Retention keeps the newest N backups and never deletes the just-created
   file.
5. No secret appears in CLI output, logs, or a process command line.
6. A file overwrite with a failed snapshot does not happen (#147).
7. A denied shell command does not execute and is journaled with its pattern
   (#148).

## Non-goals

- Sandboxing the process or per-argument policy for every tool — #120.
- WAL/PITR or continuous replication.
- Encryption of backups at rest (file mode `0600` only); a follow-up if the
  threat model grows.
- Multi-user or remote backup targets.

## Sources of truth

- `docs/adr/0021-safety-net.md` — why these choices, and what was rejected.
- `CONTEXT.md` — vocabulary for backup, snapshot, journal, undo.
- GitHub issues #145–#148 — slices and acceptance criteria.
