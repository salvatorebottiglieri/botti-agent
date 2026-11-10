"""Tests for the Postgres concept repository (issue #153).

``ConceptDerivationService`` emits the concept id in ``concept.validated``
before storage. The repository must persist the model's id instead of letting
Postgres generate a different one, otherwise the event points at a row that
does not exist.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from cortex.memory.models import Concept
from cortex.memory.repository import PostgresConceptRepository


@pytest.fixture
def mock_db_session():
    """Create a mock DbSession context manager."""
    mock = MagicMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    mock.fetchrow = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_store_persists_the_concept_id(mock_db_session):
    """The id the caller (and the event) knows survives the INSERT."""
    concept = Concept(
        symbolic_repr="location.office",
        natural_lang_repr="The user works at the office",
        derivation_method="llm_inference",
        proof_chain="location.office :- works_at(office)",
        confidence=0.8,
        source_facts=[uuid4()],
        validated=True,
    )
    mock_db_session.fetchrow.return_value = {
        "id": concept.id,
        "symbolic_repr": concept.symbolic_repr,
        "natural_lang_repr": concept.natural_lang_repr,
        "derivation_method": concept.derivation_method,
        "proof_chain": concept.proof_chain,
        "source_facts": [str(f) for f in concept.source_facts],
        "confidence": concept.confidence,
        "validated": concept.validated,
        "created_at": datetime.now(UTC),
    }

    with patch("cortex.memory.repository.DbSession") as db_session_class:
        db_session_class.return_value = mock_db_session
        stored = await PostgresConceptRepository().store(concept)

    _query, concept_id, *_rest = mock_db_session.fetchrow.call_args.args
    assert concept_id == concept.id
    assert stored.id == concept.id
