"""Shared fixtures for eval harness tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from cortex.eval.fixtures import EvalSuite, load_suite


@pytest.fixture
def sample_suite_path() -> Path:
    """Path to the sample YAML eval suite shipped with the tests."""
    return Path(__file__).parent / "fixtures" / "sample_suite.yaml"


@pytest.fixture
def sample_suite(sample_suite_path: Path) -> EvalSuite:
    """The sample suite loaded from YAML."""
    return load_suite(sample_suite_path)
