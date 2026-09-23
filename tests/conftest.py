"""Pytest configuration for asyncio tests."""

import os

import pytest

from tests.settings_env import SETTINGS_ENV_NAMES, SETTINGS_ENV_PREFIXES


@pytest.fixture(autouse=True)
def _no_ambient_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every settings variable from the ambient environment (CFG4).

    With the environment outranking constructor arguments (ADR-0019), an
    ambient variable from the developer's shell silently rewrites the value a
    test passed as a kwarg. Deleted here, case-insensitively: every name in
    ``tests.settings_env.SETTINGS_ENV_NAMES`` (the slice namespaces and the root
    ``Settings``' own field names — ``APP``, ``VERSION``, …) and every name
    carrying one of ``SETTINGS_ENV_PREFIXES``. Tests that need one of these
    variables set it themselves; none relies on a process-start default.

    Residual hole: a slice that drops its ``env_prefix`` and reads a bare field
    name outside those prefixes escapes the scrub — the name lists are only as
    complete as ``SETTINGS_ENV_PREFIXES`` plus the root's fields.
    """
    for name in [
        name
        for name in os.environ
        if name.upper() in SETTINGS_ENV_NAMES or name.upper().startswith(SETTINGS_ENV_PREFIXES)
    ]:
        monkeypatch.delenv(name)


# pytest-asyncio handles event loop setup automatically
# No custom event_loop_policy fixture needed
