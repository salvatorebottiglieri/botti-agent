# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`docs/adr/`** — read ADRs that touch the area you're about to work in
- **`docs/evidence-system.md`** — when touching the Memory module or the evidence system

If any of these files don't exist, **proceed silently**. Don't flag their absence.

## Single-context layout

```
/
├── CONTEXT.md
├── docs/adr/
│   └── *.md
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept, use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly.
