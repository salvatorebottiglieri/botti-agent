# Agents

## Agent skills

### Issue tracker

Issues live in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage labels (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one CONTEXT.md at the repo root + docs/adr/. See `docs/agents/domain.md`.
### Evidence system

The evidence-based fact confidence system is specified in `docs/evidence-system.md` — the
single source of truth for its data model, update rule, constants, and invariants. Update
that document whenever the evidence system changes (ADR-0013, issues #82/#83).
