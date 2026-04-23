"""Artifact Registry — hash-pinned TRW surface identity (PRD-HPO-MEAS-001 S1).

This module resolves a deterministic ``surface_snapshot_id`` for the
running TRW surface: packaged agents, skills, hooks, prompts, behavioral
protocol + framework/package versions. Every :class:`HPOTelemetryEvent`
stamped with this id is reconstructible under NIST's 24-hour provenance
mandate and correlatable by H4 meta-tune with the exact surface version
that produced it.

Design invariants (FR-2, §7.2 Resolution):

1. **Pure + cache-friendly.** :func:`resolve_surface_snapshot` is a pure
   function of disk state at call time. A module-level LRU cache memoizes
   the last result per ``(package_root, cache_key)`` tuple so repeated
   ``trw_session_start`` calls in the same process do not rehash the full
   data directory.
2. **Deterministic digest.** File contents are hashed individually, sorted
   by their repo-relative POSIX path, then folded into the rollup hash.
   Hash algorithm is SHA-256; digest is hex-encoded to 64 chars.
3. **Best-effort, never raise.** Missing optional components (e.g. a
   project without bundled hooks) produce an empty-fingerprint component
   and a WARN log. :func:`resolve_surface_snapshot` must not raise on any
   disk-state anomaly — Phase 1 can default ``surface_snapshot_id=""``
   (see event_base §PRD-HPO-MEAS-001 §9 Rollout).
4. **Size-stable output.** The returned :class:`SurfaceSnapshot` is a
   frozen Pydantic v2 model; adding fields requires PRD + migration note.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata as _metadata
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import Iterable

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


_HASH_ALGO = "sha256"
_FRAMEWORK_VERSION_FALLBACK = "unknown"
_PACKAGE_NAME = "trw-mcp"
_DATA_PACKAGE = "trw_mcp.data"

#: Component keys used in the manifest. Keep stable — H4 meta-tune groups
#: candidates by these keys.
_COMPONENT_KEYS: tuple[str, ...] = (
    "agents",
    "skills",
    "hooks",
    "prompts",
    "surfaces",
    "config",
)


class ComponentFingerprint(BaseModel):
    """Per-component rollup fingerprint.

    ``file_count`` + ``total_bytes`` are included for human-readable
    manifest summaries; they do **not** factor into ``digest`` so a
    symlink or whitespace-preserving rewrite of the same files yields
    identical digests.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    digest: str = Field(default="", description="Hex-encoded SHA-256 of content rollup.")
    file_count: int = 0
    total_bytes: int = 0


class SurfaceSnapshot(BaseModel):
    """Resolved surface identity (PRD-HPO-MEAS-001 FR-2).

    Every new :class:`HPOTelemetryEvent` subtype carries
    ``surface_snapshot_id = SurfaceSnapshot.snapshot_id``. The full
    :class:`SurfaceSnapshot` is serialized to
    ``<run>/surface_manifest.yaml`` once per session.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    snapshot_id: str
    trw_mcp_version: str
    framework_version: str
    generated_at: datetime
    components: dict[str, ComponentFingerprint] = Field(default_factory=dict)


def _hash_file(path: Path) -> tuple[str, int]:
    """Return ``(sha256_hex, byte_count)`` for a single file.

    Reads in 64 KiB chunks to stay memory-friendly for any size file.
    Returns an empty-digest marker when the path is not a regular file.
    """
    if not path.is_file():
        return "", 0
    h = hashlib.new(_HASH_ALGO)
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _iter_files(root: Path, patterns: Iterable[str] = ("**/*",)) -> list[Path]:
    """Yield regular files under ``root`` matching any of ``patterns``, sorted."""
    if not root.exists():
        return []
    seen: set[Path] = set()
    for pat in patterns:
        for p in root.glob(pat):
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen, key=lambda p: p.as_posix())


def _fingerprint_component(root: Path, patterns: Iterable[str] = ("**/*",)) -> ComponentFingerprint:
    """Roll up a directory into a single :class:`ComponentFingerprint`.

    Per-file hashes are combined in **sorted-path order** to keep the
    rollup deterministic across filesystems. A missing ``root`` returns a
    zero-state fingerprint and emits a debug log (not a warning — many
    profiles opt out of bundled components).
    """
    files = _iter_files(root, patterns)
    if not files:
        logger.debug("artifact_registry_component_empty", root=str(root))
        return ComponentFingerprint()

    rollup = hashlib.new(_HASH_ALGO)
    total = 0
    for f in files:
        digest, size = _hash_file(f)
        # Stable key: path relative to root in POSIX form, then digest.
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            rel = f.as_posix()
        rollup.update(rel.encode("utf-8"))
        rollup.update(b"\x00")
        rollup.update(digest.encode("ascii"))
        rollup.update(b"\x00")
        total += size

    return ComponentFingerprint(
        digest=rollup.hexdigest(),
        file_count=len(files),
        total_bytes=total,
    )


def _resolve_data_root() -> Path | None:
    """Resolve ``trw_mcp.data`` to a filesystem path.

    Returns ``None`` when the bundled data is not layable as a real path
    (e.g. when the package is zip-imported). In that case component
    fingerprints resolve to empty and ``snapshot_id`` still stabilizes on
    the version-only rollup.
    """
    try:
        root = _pkg_files(_DATA_PACKAGE)
        p = Path(str(root))
        return p if p.exists() and p.is_dir() else None
    except (ModuleNotFoundError, FileNotFoundError) as exc:  # justified: boundary, bundled data may be zipped in distribution
        logger.warning("artifact_registry_data_root_unavailable", error=str(exc))
        return None


def _package_version() -> str:
    """Return the installed trw-mcp version, or a sentinel if unresolvable."""
    try:
        return _metadata.version(_PACKAGE_NAME)
    except _metadata.PackageNotFoundError:  # justified: boundary, tests run without install
        return _FRAMEWORK_VERSION_FALLBACK


def _framework_version() -> str:
    """Return the TRW framework version (config-driven, not package-driven).

    Imported lazily to avoid circular imports when ``models.config`` is
    mid-initialization.
    """
    try:
        from trw_mcp.models.config._main import TRWConfig  # local import
    except ImportError:  # justified: boundary, config module optional in some tooling contexts
        return _FRAMEWORK_VERSION_FALLBACK
    try:
        return str(TRWConfig.model_fields["framework_version"].default)
    except (KeyError, AttributeError):  # justified: scan-resilience, model evolution may rename the field
        return _FRAMEWORK_VERSION_FALLBACK


def _snapshot_digest(
    *,
    trw_mcp_version: str,
    framework_version: str,
    components: dict[str, ComponentFingerprint],
) -> str:
    """Combine version + component digests into the canonical snapshot id.

    Ordering is canonical (alphabetical component key) so the id depends
    only on the **content** of the surface, not on iteration order.
    """
    h = hashlib.new(_HASH_ALGO)
    h.update(trw_mcp_version.encode("utf-8"))
    h.update(b"\x00")
    h.update(framework_version.encode("utf-8"))
    h.update(b"\x00")
    for key in sorted(components):
        h.update(key.encode("utf-8"))
        h.update(b"\x00")
        h.update(components[key].digest.encode("ascii"))
        h.update(b"\x00")
    return h.hexdigest()


@lru_cache(maxsize=8)
def _cached_snapshot(cache_key: str, data_root_str: str | None) -> SurfaceSnapshot:
    """Cached snapshot resolver keyed on a caller-supplied cache key.

    The cache key lets tests force a refresh (pass a unique value) while
    production call sites pass a stable empty string to share the cache
    across invocations in the same process. ``cache_key`` is intentionally
    unread inside the body — its only role is to differentiate lru_cache
    entries.
    """
    del cache_key  # explicit no-op; parameter exists only for lru_cache keying
    pkg_ver = _package_version()
    fw_ver = _framework_version()

    data_root = Path(data_root_str) if data_root_str else None
    components: dict[str, ComponentFingerprint] = {}
    if data_root is not None and data_root.exists():
        components["agents"] = _fingerprint_component(data_root / "agents", ("**/*.md",))
        components["skills"] = _fingerprint_component(data_root / "skills", ("**/*.md", "**/*.yaml"))
        components["hooks"] = _fingerprint_component(data_root / "hooks", ("**/*.sh",))
        components["prompts"] = _fingerprint_component(data_root / "prompts", ("**/*.py", "**/*.md"))
        components["surfaces"] = _fingerprint_component(data_root / "surfaces", ("**/*",))
        components["config"] = _fingerprint_component(
            data_root,
            (
                "behavioral_protocol.yaml",
                "semantic_checks.yaml",
                "settings.json",
            ),
        )
    else:
        for key in _COMPONENT_KEYS:
            components[key] = ComponentFingerprint()

    snapshot_id = _snapshot_digest(
        trw_mcp_version=pkg_ver,
        framework_version=fw_ver,
        components=components,
    )

    return SurfaceSnapshot(
        snapshot_id=snapshot_id,
        trw_mcp_version=pkg_ver,
        framework_version=fw_ver,
        generated_at=datetime.now(tz=timezone.utc),
        components=components,
    )


def resolve_surface_snapshot(*, refresh: bool = False) -> SurfaceSnapshot:
    """Resolve the surface snapshot for the current TRW installation.

    Args:
        refresh: When True, bypass the per-process cache and force a new
            fingerprint computation. Useful in tests that mutate bundled
            data between assertions.

    Returns:
        A :class:`SurfaceSnapshot` — always; never ``None``. On disk-state
        anomalies, component fingerprints fall back to empty and the
        snapshot id still stabilizes on the version-only rollup.
    """
    data_root = _resolve_data_root()
    cache_key = f"refresh-{datetime.now(tz=timezone.utc).isoformat()}" if refresh else ""
    return _cached_snapshot(cache_key, str(data_root) if data_root else None)


def clear_snapshot_cache() -> None:
    """Drop the per-process snapshot cache.

    Test-only helper; production code should not call this — force a
    refresh via ``resolve_surface_snapshot(refresh=True)`` instead.
    """
    _cached_snapshot.cache_clear()


__all__ = [
    "ComponentFingerprint",
    "SurfaceSnapshot",
    "resolve_surface_snapshot",
    "clear_snapshot_cache",
]
