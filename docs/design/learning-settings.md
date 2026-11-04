# Program Design — LearningSettings wiring (#12)

## Context

`LearningSettings` exists as a stub from #35 (`src/cortex/config/learning.py`):
it carries `reservoir_size`, `spectral_radius` and `leak_rate`, declares the
`LEARNING_` environment namespace, and is reachable from the root `Settings`
(`Settings.learning`). Nothing consumes it: `LearningModule` still takes a
pre-built `EchoStateNetwork`, and the reservoir's own defaults (notably
`spectral_radius=1.0`) are the de-facto configuration.

This ticket closes that gap without touching the ESN maths: the settings become
the single source of the reservoir hyperparameters, and the module gains a
constructor path that builds its ESN from them.

## Decisions

- **`from_settings` classmethod, not a breaking `__init__` change.** The
  existing `__init__(event_bus, esn, *, encoder=None)` remains the injection
  seam: tests and the future training flow (#13) pass a pre-built ESN. The new
  `from_settings(event_bus, settings, *, encoder=None, seed=None)` is the
  production path that builds `Reservoir` + `EchoStateNetwork` from config.
  This keeps the signature compatible (the design checker compares declared
  signatures against the code) and leaves #13 free to train readouts on the
  module's ESN.
- **`esn` becomes a read-only property.** #13 registers readouts on the
  reservoir the module owns; tests need the same handle. It is a view, not a
  second owner.
- **`ridge_lambda` is carried, not consumed here.** Readouts are constructed by
  the training flow (#13); #12 only makes the value configurable and reachable
  from `Settings.learning`.
- **Reconcile the default.** `Reservoir` changes its default
  `spectral_radius` from `1.0` to `0.9`, matching ADR-0001 and the settings
  default. No production caller relied on the old default (grep: tests only).
- **No `main.py` wiring.** Runtime construction/subscription of the learning
  module waits for #13 (training): wiring an untrained module into startup
  would make `get_*` raise in production for no consumer.

## Call-stack tree

```diff
 LearningModule.from_settings                       # NEW — production path
+  MinionEventEncoder()                            # default encoder
+  Reservoir.__init__                              # n_input from encoder, rest from settings
+  EchoStateNetwork.__init__                       # wraps the reservoir
+  LearningModule.__init__                         # existing injection seam
 LearningModule.__init__                           # unchanged — stores the ESN it already received
```

## File-tree diff

```diff
 src/cortex/config
~  learning.py          # MODIFIED — input_scale + ridge_lambda, pydantic range validation, wired docstring
 src/cortex/learning
~  module.py            # MODIFIED — from_settings classmethod + esn property, no logic change
~  reservoir.py         # MODIFIED — default spectral_radius 0.9 (ADR-0001)
 tests/learning
+  test_settings.py     # NEW — defaults, validation, module built from settings
 test_module.py        # unchanged — the existing injected-ESN path and its mismatch test remain valid
~  test_reservoir.py    # MODIFIED — default radius expectation 0.9
 tests/config
~  test_models.py       # MODIFIED — stub test becomes the wired-settings test
 config.yaml            # MODIFIED — learning section with the five knobs
 .env.example           # MODIFIED — LEARNING_* names
```

## Type signatures

```python
# src/cortex/config/learning.py
class LearningSettings(CortexSettings):
    reservoir_size: int                    # default 500, ge=1
    spectral_radius: float                 # default 0.9, gt=0, lt=1
    input_scale: float                     # default 1.0, gt=0
    leak_rate: float                       # default 0.3, gt=0, le=1
    ridge_lambda: float                    # default 1e-6, gt=0


# src/cortex/learning/reservoir.py — default change only
class Reservoir:
    def __init__(
        self,
        n_input: int,
        n_reservoir: int = 500,
        *,
        alpha: float = 0.3,
        spectral_radius: float = 0.9,      # was 1.0
        input_scaling: float = 1.0,
        seed: int | None = None,
    ) -> None: ...


# src/cortex/learning/module.py
class LearningModule:
    def __init__(
        self,
        event_bus: EventBus,
        esn: EchoStateNetwork,
        *,
        encoder: MinionEventEncoder | None = None,
    ) -> None: ...                          # unchanged

    @classmethod
    def from_settings(
        cls,
        event_bus: EventBus,
        settings: LearningSettings,
        *,
        encoder: MinionEventEncoder | None = None,
        seed: int | None = None,
    ) -> LearningModule: ...                # NEW

    @property
    def esn(self) -> EchoStateNetwork: ...  # NEW
```

## Data flow

```
from_settings ──► Reservoir.__init__
       │                │
       │                ▼
       ├────────► EchoStateNetwork.__init__
       │                │
       ▼                ▼
  LearningModule.__init__ (stores the ESN; no second owner)
```

## Contracts

| site | callee | returns | absent | raises | effect |
|---|---|---|---|---|---|
| `from_settings` | `Reservoir.__init__` | `None` | never — both objects are always constructed | `ValueError` on a negative `n_input` (numpy rejects it); `n_input=0` is accepted but impossible here because `encoder.n_features >= 8` | allocates a new reservoir |
| `from_settings` | `EchoStateNetwork.__init__` | `None` | never — `Reservoir` is passed positionally | none — plain attribute assignment | wraps the reservoir |
| `from_settings` | `LearningModule.__init__` | `LearningModule` | never — the only branch returns the module | `ValueError` on encoder/reservoir mismatch; impossible here, both come from the same encoder | stores bus, ESN, encoder |

```json design
{
  "feature": "learning-settings",
  "call_stack": {
    "name": "LearningModule.from_settings",
    "children": [
      { "name": "Reservoir.__init__", "note": "existing — n_input from encoder, hyperparameters from settings" },
      { "name": "EchoStateNetwork.__init__", "note": "existing — wraps the reservoir" },
      { "name": "LearningModule.__init__", "note": "existing — stores bus, ESN, encoder" }
    ]
  },
  "file_tree": [
    { "kind": "mod", "path": "src/cortex/config/learning.py", "note": "add input_scale + ridge_lambda, range validation" },
    { "kind": "mod", "path": "src/cortex/learning/module.py", "note": "from_settings classmethod + esn property" },
    { "kind": "mod", "path": "src/cortex/learning/reservoir.py", "note": "default spectral_radius 1.0 -> 0.9" },
    { "kind": "add", "path": "tests/learning/test_settings.py", "note": "defaults, ranges, module built from settings" },
    { "kind": "keep", "path": "tests/learning/test_module.py", "note": "unchanged — the existing injected-ESN path and its mismatch test remain valid" },
    { "kind": "mod", "path": "tests/learning/test_reservoir.py", "note": "default radius expectation" },
    { "kind": "mod", "path": "tests/config/test_models.py", "note": "stub test becomes wired test" },
    { "kind": "mod", "path": "config.yaml", "note": "learning section" },
    { "kind": "mod", "path": ".env.example", "note": "LEARNING_* names" }
  ],
  "signatures": [
    {
      "module": "src/cortex/learning/module.py",
      "language": "python",
      "code": "class LearningModule:\n    def __init__(\n        self,\n        event_bus: EventBus,\n        esn: EchoStateNetwork,\n        *,\n        encoder: MinionEventEncoder | None = None,\n    ) -> None: ...\n\n    @classmethod\n    def from_settings(\n        cls,\n        event_bus: EventBus,\n        settings: LearningSettings,\n        *,\n        encoder: MinionEventEncoder | None = None,\n        seed: int | None = None,\n    ) -> LearningModule: ...\n\n    @property\n    def esn(self) -> EchoStateNetwork: ..."
    },
    {
      "module": "src/cortex/learning/reservoir.py",
      "language": "python",
      "code": "class Reservoir:\n    def __init__(\n        self,\n        n_input: int,\n        n_reservoir: int = 500,\n        *,\n        alpha: float = 0.3,\n        spectral_radius: float = 0.9,\n        input_scaling: float = 1.0,\n        seed: int | None = None,\n    ) -> None: ..."
    },
    {
      "module": "src/cortex/learning/reservoir.py",
      "language": "python",
      "code": "class EchoStateNetwork:\n    def __init__(self, reservoir: Reservoir) -> None: ..."
    }
  ],
  "data_flow": {
    "nodes": [
      { "id": "module.from_settings", "module": "src/cortex/learning/module.py", "kind": "add" },
      { "id": "module.__init__", "module": "src/cortex/learning/module.py", "kind": "keep" },
      { "id": "reservoir.__init__", "module": "src/cortex/learning/reservoir.py", "kind": "keep" },
      { "id": "reservoir.EchoStateNetwork.__init__", "module": "src/cortex/learning/reservoir.py", "kind": "keep" }
    ],
    "edges": [
      { "from": "module.from_settings", "to": "reservoir.__init__" },
      { "from": "module.from_settings", "to": "reservoir.EchoStateNetwork.__init__" },
      { "from": "module.from_settings", "to": "module.__init__" }
    ]
  },
  "contracts": [
    {
      "site": "module.from_settings",
      "callee": "reservoir.__init__",
      "exits": [
        { "kind": "returns", "type": "None", "handled_at": "src/cortex/learning/module.py:from_settings" },
        { "kind": "absent", "type": "never", "handled_at": "both objects are always constructed; no optional branch" },
        { "kind": "raises", "type": "ValueError", "handled_at": "impossible: numpy rejects a negative n_input; n_input = encoder.n_features >= 8" },
        { "kind": "effect", "type": "allocates a new Reservoir", "handled_at": null }
      ]
    },
    {
      "site": "module.from_settings",
      "callee": "reservoir.EchoStateNetwork.__init__",
      "exits": [
        { "kind": "returns", "type": "None", "handled_at": "src/cortex/learning/module.py:from_settings" },
        { "kind": "absent", "type": "never", "handled_at": "the reservoir is passed positionally" },
        { "kind": "raises", "type": "none", "handled_at": "plain attribute assignment in EchoStateNetwork.__init__" },
        { "kind": "effect", "type": "wraps the reservoir", "handled_at": null }
      ]
    },
    {
      "site": "module.from_settings",
      "callee": "module.__init__",
      "exits": [
        { "kind": "returns", "type": "LearningModule", "handled_at": "src/cortex/learning/module.py:from_settings returns cls(...)" },
        { "kind": "absent", "type": "never", "handled_at": "the only branch returns the constructed module" },
        { "kind": "raises", "type": "ValueError", "handled_at": "impossible here: encoder and reservoir share encoder.n_features" },
        { "kind": "effect", "type": "stores bus, ESN, encoder", "handled_at": null }
      ]
    }
  ]
}
```

## Premises

- **Premise**: #35 is merged and `Settings.learning` is reachable.
  - verdict: confirmed
  - evidence: `src/cortex/config/models.py:60`; merge `1a76346` (PR #126).
- **Premise**: no production caller constructs `Reservoir` without an explicit
  `spectral_radius`, so changing the default from `1.0` to `0.9` is safe.
  - verdict: confirmed
  - evidence: `grep -rn "Reservoir(" src` → no hits outside `learning/reservoir.py`;
    tests pass explicit or default values and are updated.
- **Premise**: `LearningModule` is not constructed in `main.py`; #12 is config
  wiring only.
  - verdict: confirmed
  - evidence: `grep -rn "LearningModule" src` → only the class definition;
    `main.py` has no learning wiring.
- **Premise**: pydantic `Field` constraints provide the required range
  validation.
  - verdict: confirmed
  - evidence: `src/cortex/config/llm.py` uses `ge=1`; `src/cortex/config/app.py`
    uses range fields; `Settings(app={"port": 0})` already raises.
- **Premise**: `ridge_lambda` has no consumer before #13.
  - verdict: confirmed
  - evidence: `grep -rn "ridge_lambda" src` → only `Readout.__init__` and
    readout subclasses; no caller passes it from config.
