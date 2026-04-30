"""Memory module data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FactType(str, Enum):
    """Types of facts stored in memory."""

    # Core facts
    USER_PREFERENCE = "user_preference"
    USER_FACT = "user_fact"
    LOCATION = "location"
    TIME = "time"
    ACTIVITY = "activity"

    # Contextual
    CALENDAR = "calendar"
    WEATHER = "weather"
    DEVICE_STATUS = "device_status"

    # Knowledge
    ENTITY = "entity"
    RELATIONSHIP = "relationship"

    # Derived
    CONCEPT = "concept"

    # Other
    CUSTOM = "custom"


class FactMutability(str, Enum):
    """How mutable a fact is."""

    STATIC = "static"  # Never changes (e.g., birth date)
    SEMI_STATIC = "semi_static"  # Rarely changes (e.g., home address)
    MUTABLE = "mutable"  # Changes over time (e.g., current location)
    EPHEMERAL = "ephemeral"  # Very short-lived (e.g., active app)


class ConfidenceLevel(str, Enum):
    """Confidence in a fact's accuracy."""

    HIGH = "high"  # Direct observation or user confirmation
    MEDIUM = "medium"  # Inferred with reasonable certainty
    LOW = "low"  # Weak inference, may be incorrect


@dataclass
class Fact:
    """
    A single fact stored in memory.

    Facts are atomic pieces of knowledge that can be:
    - Observed directly (from sensors)
    - Derived from observations
    - User-provided
    - Extracted from conversations
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    type: FactType = FactType.CUSTOM
    mutability: FactMutability = FactMutability.MUTABLE

    # Content representation
    symbolic_repr: str = ""  # Structured representation (e.g., "location.home")
    natural_lang_repr: str = ""  # Human-readable (e.g., "User is at home")

    # Associated data
    payload: dict[str, Any] = field(default_factory=dict)

    # Metadata
    confidence: float = 0.5  # 0.0 to 1.0
    layer: int = 0  # 0 = observed, 1 = derived, etc.

    # Access tracking
    access_count: int = 0
    last_accessed_at: datetime | None = None

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retracted_at: datetime | None = None

    def is_active(self) -> bool:
        """Check if the fact is still valid."""
        return self.retracted_at is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "type": self.type.value,
            "mutability": self.mutability.value,
            "symbolic_repr": self.symbolic_repr,
            "natural_lang_repr": self.natural_lang_repr,
            "payload": self.payload,
            "confidence": self.confidence,
            "layer": self.layer,
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            "created_at": self.created_at.isoformat(),
            "retracted_at": self.retracted_at.isoformat() if self.retracted_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        """Create from dictionary."""
        return cls(
            id=uuid.UUID(data["id"]),
            type=FactType(data["type"]),
            mutability=FactMutability(data.get("mutability", "mutable")),
            symbolic_repr=data.get("symbolic_repr", ""),
            natural_lang_repr=data.get("natural_lang_repr", ""),
            payload=data.get("payload", {}),
            confidence=data.get("confidence", 0.5),
            layer=data.get("layer", 0),
            access_count=data.get("access_count", 0),
            last_accessed_at=datetime.fromisoformat(data["last_accessed_at"]) if data.get("last_accessed_at") else None,
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            retracted_at=datetime.fromisoformat(data["retracted_at"]) if data.get("retracted_at") else None,
        )

    def to_search_text(self) -> str:
        """Get text for semantic search."""
        return f"{self.symbolic_repr} {self.natural_lang_repr}"


@dataclass
class Concept:
    """
    A derived concept from multiple facts.

    Concepts are higher-level abstractions derived from facts
    through reasoning or LLM-powered extraction.
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)

    # Content
    symbolic_repr: str = ""
    natural_lang_repr: str = ""
    derivation_method: str = ""  # How it was derived (e.g., "llm_inference", "rule")
    proof_chain: str = ""  # Explanation of derivation

    # Associated data
    payload: dict[str, Any] = field(default_factory=dict)

    # Confidence
    confidence: float = 0.5

    # Source tracking
    source_facts: list[uuid.UUID] = field(default_factory=list)

    # Validation
    validated: bool = False

    # Timestamps
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    retracted_at: datetime | None = None

    def is_active(self) -> bool:
        """Check if the concept is still valid."""
        return self.retracted_at is None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "symbolic_repr": self.symbolic_repr,
            "natural_lang_repr": self.natural_lang_repr,
            "derivation_method": self.derivation_method,
            "proof_chain": self.proof_chain,
            "payload": self.payload,
            "confidence": self.confidence,
            "source_facts": [str(f) for f in self.source_facts],
            "validated": self.validated,
            "created_at": self.created_at.isoformat(),
            "retracted_at": self.retracted_at.isoformat() if self.retracted_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Concept:
        """Create from dictionary."""
        return cls(
            id=uuid.UUID(data["id"]),
            symbolic_repr=data.get("symbolic_repr", ""),
            natural_lang_repr=data.get("natural_lang_repr", ""),
            derivation_method=data.get("derivation_method", ""),
            proof_chain=data.get("proof_chain", ""),
            payload=data.get("payload", {}),
            confidence=data.get("confidence", 0.5),
            source_facts=[uuid.UUID(f) for f in data.get("source_facts", [])],
            validated=data.get("validated", False),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            retracted_at=datetime.fromisoformat(data["retracted_at"]) if data.get("retracted_at") else None,
        )