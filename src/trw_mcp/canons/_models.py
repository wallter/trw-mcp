"""Immutable typed records for the canon registry (PRD-INFRA-164 FR01).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

Every record is a frozen dataclass so a loaded registry cannot be mutated by a
consumer, and enums close each categorical field. Standard-library only
(NFR02): dataclasses + enum, no third-party imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ArtifactKind(str, Enum):
    """Managed-artifact category."""

    CANON = "canon"
    TEMPLATE = "template"


class InstallRole(str, Enum):
    """Role a deployed copy plays in a target project."""

    RUNTIME = "runtime"
    PROJECT_REFERENCE = "project_reference"


class UpdatePolicy(str, Enum):
    """How an install target is refreshed on update-project."""

    MANAGED = "managed"
    CREATE_ONLY = "create_only"


class VersionExtractor(str, Enum):
    """Supported deterministic version readers for an authoring body."""

    FRAMEWORK_HEADER = "framework_header"
    AAREF_HEADER = "aaref_header"
    TEMPLATE_FOOTER = "template_footer"


class SurfaceUsage(str, Enum):
    """Closed current-vs-historical classification (PRD-QUAL-115 vocabulary)."""

    CURRENT_DEFAULT = "current_default"
    HISTORICAL_RECORD = "historical_record"
    VERSION_AGNOSTIC = "version_agnostic"
    HISTORICAL_INSTALL_SNAPSHOT = "historical_install_snapshot"


@dataclass(frozen=True)
class InstallTarget:
    """A managed destination path in a deployed target project."""

    path: str
    role: InstallRole
    update_policy: UpdatePolicy


@dataclass(frozen=True)
class VersionBinding:
    """Binds an artifact body to a deterministic extractor + config field."""

    extractor: VersionExtractor
    config_field: str | None


@dataclass(frozen=True)
class CanonArtifact:
    """One managed canon/template: authoring source, mirrors, targets, version."""

    id: str
    kind: ArtifactKind
    package_resource: str
    authoring_source: str
    tracked_mirrors: tuple[str, ...]
    install_targets: tuple[InstallTarget, ...]
    version: VersionBinding

    @property
    def runtime_targets(self) -> tuple[InstallTarget, ...]:
        """Install targets whose role is the operative runtime body."""
        return tuple(t for t in self.install_targets if t.role is InstallRole.RUNTIME)


@dataclass(frozen=True)
class VersionSurface:
    """A governed version-bearing occurrence with an explicit semantic usage."""

    id: str
    path: str
    selector: str | None
    usage: SurfaceUsage
    expected_value: str | None = None
    rationale: str | None = None

    @property
    def is_current_authority(self) -> bool:
        """Only current_default surfaces must equal the registry version."""
        return self.usage is SurfaceUsage.CURRENT_DEFAULT


@dataclass(frozen=True)
class CanonRegistry:
    """The whole loaded registry: schema version, artifacts, version surfaces, digest."""

    schema_version: int
    artifacts: tuple[CanonArtifact, ...]
    version_surfaces: tuple[VersionSurface, ...]
    digest: str

    def artifact(self, artifact_id: str) -> CanonArtifact:
        """Return the artifact with ``artifact_id`` or raise ``KeyError``."""
        for art in self.artifacts:
            if art.id == artifact_id:
                return art
        raise KeyError(artifact_id)

    def artifacts_of_kind(self, kind: ArtifactKind) -> tuple[CanonArtifact, ...]:
        """Return all artifacts of ``kind`` in deterministic order."""
        return tuple(a for a in self.artifacts if a.kind is kind)


__all__ = [
    "ArtifactKind",
    "CanonArtifact",
    "CanonRegistry",
    "InstallRole",
    "InstallTarget",
    "SurfaceUsage",
    "UpdatePolicy",
    "VersionBinding",
    "VersionExtractor",
    "VersionSurface",
]
