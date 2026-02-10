"""Framework models — versioning, phase overlays, vocabulary.

PRD-CORE-017: Phase-specific framework splitting. These models support
the overlay system, hierarchical versioning, and drift detection.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# Version string pattern: v{major}.{minor}[.{patch}][suffix]
_VERSION_RE = re.compile(r"v(\d+)\.(\d+)(?:\.(\d+))?([a-zA-Z_]*)")


class FrameworkVersion(BaseModel):
    """Semantic version for framework documents (e.g., 'v18.1.2').

    Format: v{major}.{minor}[.{patch}]
    Compatibility rule: same major version = compatible.
    """

    model_config = ConfigDict(strict=True)

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0, default=0)
    suffix: str = ""  # e.g., "_TRW"

    @staticmethod
    def parse(version_str: str) -> "FrameworkVersion":
        """Parse a version string like 'v18.1_TRW' or 'v18.1.2'.

        Raises ValueError if the version string cannot be parsed.
        """
        match = _VERSION_RE.match(version_str)
        if not match:
            msg = f"Cannot parse version: {version_str!r}"
            raise ValueError(msg)
        return FrameworkVersion(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3) or 0),
            suffix=match.group(4) or "",
        )

    def render(self) -> str:
        """Render as version string (e.g., 'v18.1_TRW')."""
        base = f"v{self.major}.{self.minor}"
        if self.patch > 0:
            base += f".{self.patch}"
        return base + self.suffix

    def is_compatible_with(self, other: "FrameworkVersion") -> bool:
        """Check compatibility (same major version)."""
        return self.major == other.major


class OverlayPhase(str, Enum):
    """Phases that have dedicated overlays.

    Mirrors run.Phase values — kept separate to avoid circular imports
    between framework and run model modules.
    """

    RESEARCH = "research"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VALIDATE = "validate"
    REVIEW = "review"
    DELIVER = "deliver"


class PhaseOverlay(BaseModel):
    """Metadata for a single phase overlay file.

    Each overlay augments the shared core with phase-specific content.
    """

    model_config = ConfigDict(strict=True, use_enum_values=True)

    phase: OverlayPhase
    version: str = "v18.1"
    filename: str = ""
    content_hash: str = ""
    line_count: int = Field(ge=0, default=0)
    token_estimate: int = Field(ge=0, default=0)
    sections: list[str] = Field(default_factory=list)


class OverlayRegistry(BaseModel):
    """VERSION.yaml schema — tracks overlay versions and hashes.

    Deployed alongside overlay files for drift detection.
    """

    model_config = ConfigDict(strict=True)

    core_version: str = "v18.1"
    core_hash: str = ""
    overlays: list[PhaseOverlay] = Field(default_factory=list)
    generated_at: str = ""

    def get_overlay(self, phase: str) -> PhaseOverlay | None:
        """Return overlay metadata for a phase, or None if not found."""
        return next(
            (o for o in self.overlays if o.phase == phase),
            None,
        )


class VocabularyEntry(BaseModel):
    """Single term in the framework vocabulary.

    Used for drift detection — ensures consistent terminology
    across core and overlay documents.
    """

    model_config = ConfigDict(strict=True)

    term: str
    definition: str = ""
    section: str = ""
    aliases: list[str] = Field(default_factory=list)


class VocabularyRegistry(BaseModel):
    """Framework vocabulary registry for consistency checking."""

    model_config = ConfigDict(strict=True)

    version: str = "v18.1"
    terms: list[VocabularyEntry] = Field(default_factory=list)

    def get_term(self, name: str) -> VocabularyEntry | None:
        """Look up a term by name or alias (case-insensitive)."""
        name_lower = name.lower()
        for entry in self.terms:
            if entry.term.lower() == name_lower:
                return entry
            if any(a.lower() == name_lower for a in entry.aliases):
                return entry
        return None
