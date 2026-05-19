# Per-module config composition

The `Settings` model was a single flat Pydantic class with 10+ fields. Every module
imported the same `Settings` object, creating hidden coupling — you couldn't test a
module with different LLM config without mutating global MQTT and DB state.

We split into composed models: `LLMSettings`, `DatabaseSettings`, `MQTTSettings`,
`LearningSettings`. The root `Settings` holds one instance of each. Each module takes
only its config slice via constructor injection, not the full Settings object.

This was deferred to be done alongside the Learning Module (Wave 7.1), which needs
`LearningSettings` for reservoir hyperparameters. Until then, the flat config is
adequate and carries no bugs.
