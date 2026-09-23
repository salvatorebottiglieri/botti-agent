# ADR-0019: Config source precedence

Status: accepted (2026-09-23)

## The law

Configuration sources rank:

```
env > constructor arguments (config.yaml) > .env > secrets dir
```

It is implemented once, on `CortexSettings`
(`src/cortex/config/base.py`), whose `settings_customise_sources` returns
`(env_settings, init_settings, dotenv_settings, file_secret_settings)`.

## Why

`load_settings` (`src/cortex/config/loader.py`) promised in its docstring that
the environment beat the YAML file, but the measured order was the opposite: the
loader passes each YAML section to the composed `Settings` as a constructor
argument, and pydantic-settings' default source order `(init, env, dotenv,
file_secret)` ranks constructor arguments above the environment. A value in
`config.yaml` therefore silently beat a variable the operator had just exported.
Measured on master at `fbba894`; filed as #125.

## Why one seam on the base

A nested `BaseSettings` field reads *its own* sources, so `Settings(app={...})`
reaches `AppSettings` as that class's constructor arguments. The ordering
therefore has to be defined once on `CortexSettings` for the nested slices and
the root alike — a seam on the root alone would not decide the collisions that
happen inside a slice.

## Alternatives rejected

- **Also demoting `init` below `dotenv` — `(env, dotenv, init, file_secret)`** —
  rejected because it additionally changes `config.yaml` vs `.env` for host
  installs, which no requirement asked for.
- **Merging the environment over the YAML dict inside `load_settings` before
  constructing** — rejected because it reimplements pydantic-settings' own
  source resolution and leaves the constructor-argument source authoritative for
  every other caller.
- **Having the loader read the environment itself and stop passing YAML sections
  as arguments** — rejected as a larger surface for the same outcome, and it
  would bypass the loader's `_section_input`/`${VAR}` mapping.

## Consequences

A constructor argument is no longer authoritative anywhere, tests included — a
test that passes a value while the same variable is exported gets the variable.
The Docker deployment path is unaffected, because the image ships no
`config.yaml` and docker-compose injects environment variables directly. `.env`
keeps its rank below both constructor arguments and the environment, the secrets
directory stays last, and `_env_file=None` still suppresses dotenv (`_env_file`
is a keyword controlling the dotenv source, not a field, so it is unaffected).

## Where it is verified

The CFG1–CFG6 table in the `## Configuration` section of
[architecture.md](../specs/architecture.md) (see also ADR-0012 for the
invariant format).
