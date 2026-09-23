"""Env-var names the test suite must scrub from the ambient environment (CFG4).

``tests/conftest.py``'s autouse ``_no_ambient_settings_env`` fixture deletes
every name declared here, so a variable exported in the developer's shell cannot
silently configure a settings object mid-run: with the environment outranking
constructor arguments (ADR-0019), an ambient ``APP_PORT`` rewrites the value a
test passed as a kwarg, and a bare ``APP``/``TRACE`` rewrites a whole root
field. Tests that need one of these variables set it themselves.

Both lists are hand-listed rather than derived from the models, deliberately:
the guard in ``tests/config/test_models.py::TestAmbientEnvGuard`` pins each name
against the slices and the root, so a new slice namespace or a new root field
fails the suite instead of leaking silently into every test.
"""

#: Env-var prefixes that configure a settings slice (compared case-insensitively).
#: Not derived from each slice's ``env_prefix``: ``DATABASE_``/``DB_``/
#: ``CIRCUIT_BREAKER_`` are aliases of one slice, and the unprefixed
#: ``DatabaseSettings`` reads its field names directly.
SETTINGS_ENV_PREFIXES = (
    "APP_",
    "CIRCUIT_BREAKER_",
    "DATABASE_",
    "DB_",
    "LEARNING_",
    "LLM_",
    "LOG_",
    "MQTT_",
    "TRACE_",
)

#: The root ``Settings``' own unprefixed field names. Exported bare, one of
#: these reaches a nested slice as the whole field value (``APP=1`` fails app
#: validation) or a scalar field (``VERSION=1``).
SETTINGS_ENV_NAMES = frozenset(SETTINGS_ENV_PREFIXES) | {
    "APP",
    "DATABASE",
    "LLM",
    "LEARNING",
    "LOGGING",
    "MQTT",
    "TRACE",
    "VERSION",
}
