# ADR-0021: Safety net for agent actions — external store, fail-closed migrations, deny-list shell guard

## Status

Accepted (2026-11-10). Slices 1 (#146), 2 (#147) and 3 (#148) implemented.

## Context

On 2026-10-28 a delegated agent ran `docker compose down -v` while verifying a
UI fix. The dev database volume was recreated and 30 sessions were lost. The
product had no guard against the command and no backup to restore from.

The agent touches three destructive seams: the `shell` tool (opt-in since
#134), `file_write` (silent overwrite), and migrations/DDL at boot. Each needs
a net, in value order: prevent (guard), snapshot (before the action), journal +
undo (after), explicit backups.

## Decision

1. **Backups, snapshots and the journal live in `~/.local/share/cortex/`**
   (`SAFETY_STORE_DIR`), outside the repository and outside every Docker
   volume. A backup inside the volume dies with `down -v`; a repo path dies
   with `rm -rf` or a fresh clone.
2. **Pre-migration dumps fail closed.** If pending migrations exist and the
   dump fails, startup aborts. Two settings opt out explicitly:
   `SAFETY_BACKUP_ON_MIGRATION=false` (no dump at all) and
   `SAFETY_REQUIRE_BACKUP_BEFORE_MIGRATION=false` (try, warn, continue).
3. **The shell guard is a deny-list that denies, not a confirmation gate.**
   Known destructive patterns are refused with an explanation and journaled; a
   config bypass is journaled. Consent via `ask_user` is deferred to the #120
   host sidecar, where execution moves to a policy-enforcing host process.
4. **Dumps use `pg_dump --format=custom`**, written atomically and pruned by
   retention. The Cortex image installs PostgreSQL 16 client tools; the CLI
   falls back to `docker compose exec` when the host has no `pg_dump`.
5. **Restore is interactive by default** and refuses while the target database
   has other connections unless `--force`.

## Alternatives rejected

- **Store inside the repo, gitignored.** Convenient to inspect, but `rm -rf`
  of the repo or a fresh clone removes the net, and the original incident was
  a volume wipe, not a repo wipe.
- **Store in a dedicated Docker volume.** More "production", but it adds a
  volume that must itself be protected and keeps recovery behind a running
  Docker daemon; it also does not survive `docker system prune --volumes`.
- **Warn and continue when the pre-migration dump fails.** Availability first,
  but a destructive migration would run without a net exactly when the net is
  most needed. The opt-out setting keeps an escape hatch for operators who
  consciously accept the risk.
- **Ask for confirmation on destructive shell commands (v1).** Better UX, but
  it requires a new loop consent state and a pause/resume path; #120 already
  owns that design for host execution. A deny-list is shippable now and stops
  the recorded incident; an unknown command still executes, which is why this
  is a mitigation, not a sandbox.
- **Execute and journal destructive shell commands.** It records an undo recipe
  but prevents nothing; the 28/10 command would still have destroyed the
  volume.
- **Encrypt backups now.** File mode `0600` and a user-owned directory are
  proportionate to the current threat model (accidental agent actions, not a
  compromised host); encryption adds key management with no incident behind it.

## Consequences

- The Cortex image grows by the PostgreSQL 16 client packages and the Docker
  build needs the PGDG apt repository. The in-container boot can dump; a host
  CLI without `pg_dump` uses the compose container for `backup:*` commands.
- A failed dump with the default settings blocks startup. This is deliberate:
  the operator sees the failure before a migration can lose data.
- Backups are unencrypted; `0600` is the only at-rest protection. Revisit if
  the store ever leaves the user's home or the threat model includes a hostile
  local user.
- The deny-list is pattern-based and fails open on unknown commands. #120
  supersedes it for host execution; until then, `shell` remains opt-in.
- Retention is count-based, not age-based: a burst of backups can evict older
  ones sooner than a time window would.
