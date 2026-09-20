# Domain Docs

How the engineering skills should consume this repo's documentation when exploring the codebase.

## Documentation levels

One owner per level; never duplicate one level's content in another.

| Level | Home | Canonical for |
| --- | --- | --- |
| Orientation | `README.md` | what the project is, how to run it; links only, no design prose |
| Vocabulary | `CONTEXT.md` | domain terms and the synonyms to avoid; glossary only |
| Decisions | `docs/adr/NNNN-*.md` | why, and the alternatives rejected; append-only |
| Current truth | `docs/specs/*.md` | one subsystem per file, updated in place, each declares its own sources of truth |
| Frozen research | `docs/research/*.md` | dated investigations; never updated in place |
| Agent config | `AGENTS.md`, `docs/agents/*.md` | harness operating rules |

The backlog is the GitHub issue tracker — **no document is a backlog**.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`docs/specs/`** — the subsystem spec covering the area you're about to work in
- **`docs/adr/`** — read ADRs that touch that area

If any of these files don't exist, **proceed silently**. Don't flag their absence.

## Single-context layout

```
/
├── CONTEXT.md
├── docs/
│   ├── adr/        # decisions (append-only)
│   ├── specs/      # current subsystem truth (updated in place)
│   ├── research/   # frozen investigations
│   └── agents/     # harness config
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept, use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly.
