"""Typed standard-library canon registry (PRD-INFRA-164 FR01/FR02).

This is the public facade. It turns the bundled ``framework_canons.json`` v2
manifest into immutable typed source/mirror/install/runtime/version-surface
views, computes content-bound digests for bundled bodies, and caches the loaded
registry keyed by manifest content identity (NFR03).

STRICT STANDARD LIBRARY ONLY (NFR02): dataclasses, enum, json, hashlib, re,
importlib.resources, pathlib. No third-party imports, no structlog, no Pydantic.
Consumers (source parity, runtime integrity, bootstrap, doctor, release,
fingerprint) import from here; they never keep an independent canon list.

Downstream contract for PRD-CORE-207: extend the manifest and consume
``load_registry()`` + the ``*_view`` helpers; do not add a second loader.
"""

from __future__ import annotations

import hashlib
from importlib import resources

from trw_mcp.canons._compiler import (
    COMPILER_SCHEMA_VERSION,
    CompileResult,
    ObligationClass,
    Span,
    SpanDest,
    build_inventory,
    compile_canon,
    core_byte_ratio,
    parse_source,
    provenance_footer,
    render_combined,
    render_core,
    render_reference,
)
from trw_mcp.canons._errors import CanonErrorCode, CanonRegistryError
from trw_mcp.canons._extractors import extract_version
from trw_mcp.canons._generation import (
    COMBINED_COMPATIBILITY_MIN_RELEASES,
    GeneratedOutput,
    check_generation,
    compact_generation_write_plan,
    compile_registry_canon,
    generated_outputs,
    legacy_combined_paths,
    serialize_inventory,
    write_generation,
)
from trw_mcp.canons._invariants import (
    all_families,
    core_anchors,
    covered_families,
    missing_core_anchors,
    scan_forbidden,
)
from trw_mcp.canons._loader import (
    MAX_MANIFEST_BYTES,
    SUPPORTED_SCHEMA_VERSION,
    canonical_json,
    digest_of,
    parse_registry,
)
from trw_mcp.canons._models import (
    ArtifactKind,
    CanonArtifact,
    CanonRegistry,
    CompiledCanon,
    InstallRole,
    InstallTarget,
    SurfaceUsage,
    UpdatePolicy,
    VersionBinding,
    VersionExtractor,
    VersionSurface,
)
from trw_mcp.canons._promotion import (
    CRITICAL_SCENARIOS,
    NON_INFERIORITY_FLOOR,
    PROMOTION_GATES,
    REQUIRED_RECEIPT_FIELDS,
    PromotionDecision,
    critical_scenarios_pass,
    evaluate_promotion_gates,
    scenario_failures,
    validate_comprehension_receipt,
)
from trw_mcp.canons._runtime_generation import (
    CompiledGenerationReport,
    GenerationExpectation,
    generation_digest,
    inspect_compiled_generation,
)
from trw_mcp.canons._views import (
    RuntimeArtifactView,
    SourceArtifactView,
    current_default_surfaces,
    install_view,
    managed_install_view,
    runtime_view,
    source_view,
    template_artifact,
)

_MANIFEST_RESOURCE = "framework_canons.json"
_DATA_PACKAGE = "trw_mcp.data"

# Process-local immutable cache keyed by manifest content identity (NFR03).
_CACHE: dict[str, CanonRegistry] = {}


def _bundled_resource_bytes(resource: str, *, error_context: str) -> bytes:
    """Read one bundled canon resource with consistent error translation."""
    try:
        return resources.files(_DATA_PACKAGE).joinpath(resource).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        raise CanonRegistryError(CanonErrorCode.SOURCE_UNREADABLE, f"{error_context}: {exc}") from exc


def bundled_manifest_bytes() -> bytes:
    """Read the packaged registry manifest bytes via importlib.resources."""
    return _bundled_resource_bytes(_MANIFEST_RESOURCE, error_context="bundled canon manifest unreadable")


def load_registry(manifest_bytes: bytes | None = None) -> CanonRegistry:
    """Load and validate the registry, caching by manifest content identity.

    Passing ``manifest_bytes`` loads that content (used by tests and migration
    adapters); otherwise the packaged manifest is used. The cache key is the
    SHA-256 of the manifest bytes so any manifest mutation invalidates the
    cached registry (NFR03) — matching version strings never reuse stale data.
    """
    raw = bundled_manifest_bytes() if manifest_bytes is None else manifest_bytes
    key = hashlib.sha256(raw).hexdigest()
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    registry = parse_registry(raw)
    _CACHE[key] = registry
    return registry


def clear_cache() -> None:
    """Drop the process-local registry cache (test/reload hook)."""
    _CACHE.clear()


def bundled_source_bytes(artifact: CanonArtifact) -> bytes:
    """Read an artifact's packaged authoring body via importlib.resources."""
    return _bundled_resource_bytes(
        artifact.package_resource,
        error_context=f"bundled source unreadable for {artifact.id}",
    )


def source_digest(artifact: CanonArtifact) -> str:
    """SHA-256 hex digest of an artifact's packaged authoring body."""
    return hashlib.sha256(bundled_source_bytes(artifact)).hexdigest()


def managed_source_digests(registry: CanonRegistry) -> dict[str, str]:
    """Ordered ``{artifact_id: source_digest}`` for every managed artifact.

    Used by the live-process fingerprint (FR07) to bind loaded bytes.
    """
    return {a.id: source_digest(a) for a in registry.artifacts}


def bundled_source_version(artifact: CanonArtifact) -> str | None:
    """Deterministic version token read from the packaged authoring body."""
    text = bundled_source_bytes(artifact).decode("utf-8")
    return extract_version(artifact.version.extractor, text)


def bundled_compiled_source_bytes(compiled: CompiledCanon) -> bytes:
    """Read a compiled canon's packaged marked authoring source bytes.

    The marked source is bundled under ``trw_mcp.data`` by basename so the
    compiler runs from an installed package as well as a raw checkout.
    """
    resource = compiled.authoring_source.rsplit("/", 1)[-1]
    return _bundled_resource_bytes(
        resource,
        error_context=f"bundled marked source unreadable for {compiled.id}",
    )


__all__ = [
    "COMBINED_COMPATIBILITY_MIN_RELEASES",
    "COMPILER_SCHEMA_VERSION",
    "CRITICAL_SCENARIOS",
    "MAX_MANIFEST_BYTES",
    "NON_INFERIORITY_FLOOR",
    "PROMOTION_GATES",
    "REQUIRED_RECEIPT_FIELDS",
    "SUPPORTED_SCHEMA_VERSION",
    "ArtifactKind",
    "CanonArtifact",
    "CanonErrorCode",
    "CanonRegistry",
    "CanonRegistryError",
    "CompileResult",
    "CompiledCanon",
    "CompiledGenerationReport",
    "GeneratedOutput",
    "GenerationExpectation",
    "InstallRole",
    "InstallTarget",
    "ObligationClass",
    "PromotionDecision",
    "RuntimeArtifactView",
    "SourceArtifactView",
    "Span",
    "SpanDest",
    "SurfaceUsage",
    "UpdatePolicy",
    "VersionBinding",
    "VersionExtractor",
    "VersionSurface",
    "all_families",
    "build_inventory",
    "bundled_compiled_source_bytes",
    "bundled_manifest_bytes",
    "bundled_source_bytes",
    "bundled_source_version",
    "canonical_json",
    "check_generation",
    "clear_cache",
    "compact_generation_write_plan",
    "compile_canon",
    "compile_registry_canon",
    "core_anchors",
    "core_byte_ratio",
    "covered_families",
    "critical_scenarios_pass",
    "current_default_surfaces",
    "digest_of",
    "evaluate_promotion_gates",
    "extract_version",
    "generated_outputs",
    "generation_digest",
    "inspect_compiled_generation",
    "install_view",
    "legacy_combined_paths",
    "load_registry",
    "managed_install_view",
    "managed_source_digests",
    "missing_core_anchors",
    "parse_source",
    "provenance_footer",
    "render_combined",
    "render_core",
    "render_reference",
    "runtime_view",
    "scan_forbidden",
    "scenario_failures",
    "serialize_inventory",
    "source_digest",
    "source_view",
    "template_artifact",
    "validate_comprehension_receipt",
    "write_generation",
]
