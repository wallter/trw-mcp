"""Registry-derived consumer views (PRD-INFRA-164 FR02).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

Every consumer plan derives ONLY from loaded registry records so a new artifact
or target reaches every opted-in role without a second hard-coded list. Views
are frozen and standard-library only (NFR02).
"""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.canons._models import (
    ArtifactKind,
    CanonArtifact,
    CanonRegistry,
    InstallRole,
    VersionBinding,
    VersionSurface,
)


@dataclass(frozen=True)
class SourceArtifactView:
    """What source parity needs: authoring source, tracked mirrors, version binding."""

    id: str
    authoring_source: str
    tracked_mirrors: tuple[str, ...]
    version: VersionBinding


@dataclass(frozen=True)
class RuntimeArtifactView:
    """What deployed runtime integrity needs: bundled resource + runtime targets."""

    id: str
    package_resource: str
    authoring_source: str
    runtime_targets: tuple[str, ...]
    version: VersionBinding


def source_view(registry: CanonRegistry) -> tuple[SourceArtifactView, ...]:
    """Source-parity plan: authoring sources, tracked mirrors, and version bindings."""
    return tuple(
        SourceArtifactView(
            id=a.id,
            authoring_source=a.authoring_source,
            tracked_mirrors=a.tracked_mirrors,
            version=a.version,
        )
        for a in registry.artifacts
    )


def runtime_view(registry: CanonRegistry) -> tuple[RuntimeArtifactView, ...]:
    """Deployed-runtime plan: only artifacts with at least one runtime target."""
    return tuple(
        RuntimeArtifactView(
            id=a.id,
            package_resource=a.package_resource,
            authoring_source=a.authoring_source,
            runtime_targets=tuple(t.path for t in a.runtime_targets),
            version=a.version,
        )
        for a in registry.artifacts
        if a.runtime_targets
    )


def install_view(registry: CanonRegistry) -> tuple[tuple[str, str], ...]:
    """``(package_resource, destination_path)`` pairs for every managed install target.

    This is the registry-derived projection that ``_DATA_FILE_MAP`` and
    ``_ALWAYS_UPDATE`` become (NFR06): no literal canon list survives.
    """
    return tuple((a.package_resource, target.path) for a in registry.artifacts for target in a.install_targets)


def managed_install_view(registry: CanonRegistry, role: InstallRole) -> tuple[tuple[str, str], ...]:
    """``(package_resource, destination_path)`` pairs limited to ``role`` targets."""
    return tuple((a.package_resource, t.path) for a in registry.artifacts for t in a.install_targets if t.role is role)


def template_artifact(registry: CanonRegistry) -> CanonArtifact:
    """Return the single managed PRD template artifact.

    Raises ``KeyError`` if the registry declares no template — a fail-closed
    condition for FR06 (missing canonical template).
    """
    templates = registry.artifacts_of_kind(ArtifactKind.TEMPLATE)
    if not templates:
        raise KeyError("no template artifact in registry")
    return templates[0]


def current_default_surfaces(registry: CanonRegistry) -> tuple[VersionSurface, ...]:
    """Governed surfaces that must equal the registry's current version."""
    return tuple(s for s in registry.version_surfaces if s.is_current_authority)


__all__ = [
    "RuntimeArtifactView",
    "SourceArtifactView",
    "current_default_surfaces",
    "install_view",
    "managed_install_view",
    "runtime_view",
    "source_view",
    "template_artifact",
]
