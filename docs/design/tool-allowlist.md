# Program design — tool allow-list (phase 0, host access)

> Not a source of truth: the allow-list is behaviour, so its contract lives in
> `docs/specs/architecture.md` (Tools row) and the README's "Host access"
> section; this file owns only the shapes for the change and expires when it lands.

Issue: none yet — phase 0 of the operational readiness work; the real policy
(paths, arguments, per-call approval) is **#120**, which stays open.

## Why

Before this change every meta tool is registered unconditionally
(`register_meta_tools(registry)`), so `shell`, `file_write`, `file_read` and
`grep` are always advertised to the model and execute immediately as the process
user. Demonstrated: a chat turn asking for `echo … && whoami` ran `shell` and
returned `salvatore`. A prompt-injected turn can therefore read `.env` or run
anything the user can. The minimal mitigation is **deny-by-default**: the
composition root registers only the tools the configuration allows, and the
default set is read-only.

`ask_user` is in the default set: it touches no host resource, it only ends the
turn with a question.

## Shape

- New settings slice `ToolsSettings` (`TOOLS_` namespace), field
  `allow: list[str]`, default `["file_read", "grep", "ask_user"]`. The field is
  `NoDecode` with a before-validator because pydantic-settings JSON-decodes a
  list field read from the environment: `TOOLS_ALLOW=file_read,grep` and an
  explicit `TOOLS_ALLOW=""` would raise instead of parsing. The validator
  accepts a CSV string (whitespace stripped, empty items dropped) or an
  already-parsed list; it does **not** validate the names.
- `register_meta_tools(registry, allowed=None)` — `None` keeps today's
  behaviour (every tool), an explicit sequence filters by tool name and
  **validates the whole list before registering anything**: an unknown name
  raises `ValueError`, so a typo in `TOOLS_ALLOW` fails startup loudly instead of
  silently removing a capability.
- The composition root passes `settings.tools.allow`. Nothing else changes: the
  loop, the executor and the registry keep their contracts.

## Call stack

```diff
 initialize_app()
   ...
~  register_meta_tools(tool_registry, allowed=settings.tools.allow)
     ToolRegistrar.register_many([... allowed tools ...])
```

## File tree

```diff
 src/cortex/config
+  └── tools.py            # NEW — ToolsSettings slice (TOOLS_ALLOW)
~  ├── models.py           # MODIFIED — compose the tools slice
~  └── loader.py          # MODIFIED — "tools" in _SETTINGS_SECTIONS
 src/cortex/tools/meta
~  └── __init__.py         # MODIFIED — register_meta_tools(registry, allowed=None)
 src/cortex/main.py       # MODIFIED — pass settings.tools.allow
 config.yaml              # MODIFIED — documented tools: section
 README.md                # MODIFIED — "Host access" section (deny-by-default, #120 open)
 docs/specs/architecture.md  # MODIFIED — Tools row names the allow-list
 tests/config
+  └── test_tools_settings.py  # NEW — default set, TOOLS_ALLOW parsing, namespace isolation
 tests/unit
~  └── test_tools_meta.py       # MODIFIED — filtering, None keeps all, unknown name raises before registering
 tests/settings_env.py          # MODIFIED — declare the TOOLS_ namespace (ambient-env guard)
```

## Type signatures

```json design
{
  "signatures": [
    {
      "module": "src/cortex/config/tools.py",
      "language": "python",
      "code": "class ToolsSettings(CortexSettings):\n    model_config = SettingsConfigDict(env_prefix=\"TOOLS_\")\n\n    allow: Annotated[list[str], NoDecode] = Field(\n        default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS)\n    )\n\n    @field_validator(\"allow\", mode=\"before\")\n    def _decode_allow(cls, value: object) -> object"
    },
    {
      "module": "src/cortex/tools/meta/__init__.py",
      "language": "python",
      "code": "def register_meta_tools(registry: ToolRegistry, allowed: Sequence[str] | None = None) -> list[str]:\n    ..."
    },
    {
      "module": "src/cortex/tools/registry.py",
      "language": "python",
      "code": "class ToolRegistrar:\n    def register_many(self, tools: list[Tool]) -> Self:\n        ..."
    },
    {
      "module": "src/cortex/main.py",
      "language": "python",
      "code": "async def initialize_app():\n    ..."
    }
  ],
  "data_flow": {
    "nodes": [
      {"id": "register_meta_tools", "kind": "mod", "module": "src/cortex/tools/meta/__init__.py"},
      {"id": "initialize_app", "kind": "mod", "module": "src/cortex/main.py"},
      {"id": "ToolRegistrar.register_many", "kind": "keep", "module": "src/cortex/tools/registry.py"}
    ],
    "edges": [
      {"from": "initialize_app", "to": "register_meta_tools"},
      {"from": "register_meta_tools", "to": "ToolRegistrar.register_many"}
    ]
  },
  "contracts": [
    {
      "site": "initialize_app",
      "callee": "register_meta_tools",
      "exits": [
        {"kind": "returns", "type": "list[str] — the registered tool names (logged at startup)", "handled_at": "cortex/main.py initialize_app logs them with the `Registered meta tools: ...` logger.info call added by this change, right after registering"},
        {"kind": "absent", "type": "[] when TOOLS_ALLOW is empty", "handled_at": "initialize_app proceeds with a registry that advertises no meta tool; the loop runs tool-less (chat still works)"},
        {"kind": "raises", "type": "ValueError from an unknown tool name in TOOLS_ALLOW", "handled_at": "initialize_app propagates it; the process exits before serving (fast fail on a typo, no half-registered registry)"},
        {"kind": "effect", "type": "the process-wide ToolRegistry holds exactly the allowed meta tools"}
      ]
    },
    {
      "site": "register_meta_tools",
      "callee": "ToolRegistrar.register_many",
      "exits": [
        {"kind": "returns", "type": "list[str] of registered tool names", "handled_at": "initialize_app logs it; tests/unit/test_tools_meta.py asserts the content"},
        {"kind": "absent", "type": "[] for an empty allowed list", "handled_at": "register_meta_tools registers an empty list (a no-op) and returns []; callers treat it as 'no tools'"},
        {"kind": "raises", "type": "ValueError when an allowed name is not a meta tool", "handled_at": "raised before any registration in register_meta_tools; initialize_app lets it abort startup; tests pin that the registry stayed untouched"},
        {"kind": "effect", "type": "registers the selected tools on the given registry"}
      ]
    }
  ]
}
```

## Tests (the exits above are the list)

- default: `Settings().tools.allow == ["file_read", "grep", "ask_user"]` with no
  `TOOLS_ALLOW` in the environment, and `shell`/`file_write` absent — witness:
  the default must *exclude* exactly the two host-mutating tools, so a default
  of "all" fails the test.
- `TOOLS_ALLOW=file_read` (monkeypatched) → only `file_read`; `TOOLS_ALLOW=""` →
  empty list, not the default (an explicit empty list is a decision, and pydantic
  must not fall back).
- `register_meta_tools(registry)` with no argument still registers all five
  (back-compat for existing callers/tests).
- `register_meta_tools(registry, allowed=["nope"])` raises `ValueError` **and the
  registry is empty** — witness: validating inside the loop over tools would
  leave `file_read` registered before hitting the bad name; asserting the empty
  registry after the exception is what distinguishes the two implementations.
- `allowed=[]` → `[]` and empty registry.
