"""Artifact Registry — content-addressed surface identity (PRD-HPO-MEAS-001 FR-1/FR-2).

This module implements ``SurfaceRegistry`` — the sole authority for TRW
surface identity — plus its serializable view ``SurfaceSnapshot``. Every
:class:`HPOTelemetryEvent` stamped with a resolved ``surface_snapshot_id``
is reconstructible per NIST's 24-hour reasoning-reconstruction mandate and
correlatable by H4 meta-tune with the exact surface version that produced
it.

Design invariants (FR-1, FR-2):

1. **Per-artifact records.** The registry records one
   :class:`SurfaceArtifact` per governing file (agent prompts, hook scripts,
   skill definitions, sub-CLAUDE.md, prompt Python sources). Each record
   carries ``{surface_id, content_hash, version, discovered_at, source_path}``
   exactly as FR-1 mandates. Content-addressed: two registries with the same
   file contents produce identical artifact hashes and identical
   ``snapshot_id``.
2. **Stateless + idempotent.** :meth:`SurfaceRegistry.build` re-walks the
   bundled data directory on every call. A module-level LRU cache memoizes
   the result per ``(data_root, refresh_key)`` tuple so repeated
   ``trw_session_start`` calls in the same process are cheap.
3. **Best-effort, never raise.** Missing optional components (a project
   without bundled hooks, a zip-imported package) produce an empty artifact
   list and a WARN log. Build must not raise on any disk-state anomaly —
   Phase 1 can default ``surface_snapshot_id=""`` (event_base §PRD §9).
4. **Stable digest.** ``snapshot_id = sha256(sorted(artifact.content_hash + "\\0" + artifact.source_path))``
   so a rename OR a content change perturbs the digest (FR-2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.telemetry._artifact_discovery import (
    _COMPONENTS,
    _discover_artifacts,
    _discover_repo_artifacts,
    _framework_version,
    _package_version,
    _resolve_data_root,
    _resolve_repo_root,
)
from trw_mcp.telemetry._artifact_discovery import (
    _artifacts_snapshot_id as _artifacts_snapshot_id,
)
from trw_mcp.telemetry._artifact_discovery import (
    _component_rollup as _component_rollup,
)

logger = structlog.get_logger(__name__)


class ComponentFingerprint(BaseModel):
    """Per-component rollup fingerprint (convenience summary view).

    Not part of the FR-1 contract — this is a secondary view for dashboards
    and human-readable summaries. The canonical per-artifact records live
    on :class:`SurfaceRegistry.artifacts`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    digest: str = Field(default="", description="Hex-encoded SHA-256 of content rollup.")
    file_count: int = 0
    total_bytes: int = 0


class SurfaceArtifact(BaseModel):
    """Per-artifact record (PRD-HPO-MEAS-001 FR-1).

    Each governing artifact discovered by the registry is recorded as one
    ``SurfaceArtifact``. ``surface_id`` is the canonical stable id
    ``<category>:<relpath>`` (e.g. ``agent:trw-implementer.md``).
    ``source_path`` is the repo-relative POSIX path under the bundled
    data root so the record remains portable across installations.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    surface_id: str
    content_hash: str
    version: str
    discovered_at: datetime
    source_path: str


class SurfaceSnapshot(BaseModel):
    """Serializable frozen view of :class:`SurfaceRegistry` (FR-2).

    ``snapshot_id = sha256(sorted(artifacts))`` — two sessions with
    identical governing surfaces produce identical ``snapshot_id``. Every
    :class:`HPOTelemetryEvent` emitted during a session carries this id.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    snapshot_id: str
    trw_mcp_version: str
    framework_version: str
    generated_at: datetime
    artifacts: tuple[SurfaceArtifact, ...] = Field(default_factory=tuple)


class SurfaceRegistry(BaseModel):
    """Content-addressed registry of all governing TRW artifacts (FR-1).

    The registry is the sole authority for surface identity. It walks the
    bundled data directory at construction time, hashes every governing
    artifact, and exposes:

    - :attr:`artifacts` — the per-artifact records (FR-1 shape)
    - :attr:`snapshot_id` — the stable digest across the sorted artifact set
    - :meth:`to_snapshot` — a frozen serializable :class:`SurfaceSnapshot`
      suitable for ``run_surface_snapshot.yaml`` output (FR-2)

    Stateless: constructing a registry does NOT write anywhere. Serialization
    and run-directory stamping happen in ``surface_manifest.py``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    trw_mcp_version: str
    framework_version: str
    generated_at: datetime
    artifacts: tuple[SurfaceArtifact, ...] = Field(default_factory=tuple)

    @classmethod
    def build_and_emit(
        cls,
        *,
        session_id: str,
        run_id: str | None = None,
        run_dir: Path | None = None,
        fallback_dir: Path | None = None,
        data_root: Path | None = None,
        repo_root: Path | None = None,
        now: datetime | None = None,
    ) -> SurfaceRegistry:
        """Build the registry AND emit one ``SurfaceRegistered`` event per artifact.

        PRD-HPO-MEAS-001 FR-10 AC-8: every newly-discovered artifact
        produces a ``SurfaceRegistered`` event so cross-session analytics
        can answer "when did this CLAUDE.md first appear in the manifest?"
        without re-walking disk.

        Emission is fail-open: any writer failure is logged and skipped;
        the registry itself is always returned.
        """
        registry = cls.build(data_root=data_root, repo_root=repo_root, now=now)
        try:
            from trw_mcp.state.persistence import FileStateWriter
            from trw_mcp.telemetry.event_base import SurfaceRegistered
            from trw_mcp.telemetry.unified_events import emit as _emit

            snapshot_id = registry.snapshot_id
            writer = FileStateWriter()
            registry_log = (run_dir / "meta" / "artifact_registry.jsonl") if run_dir is not None else None
            for art in registry.artifacts:
                category = art.surface_id.split(":", 1)[0] if ":" in art.surface_id else "unknown"
                event = SurfaceRegistered(
                    session_id=session_id,
                    run_id=run_id,
                    surface_snapshot_id=snapshot_id,
                    payload={
                        "surface_id": art.surface_id,
                        "content_hash": art.content_hash,
                        "source_path": art.source_path,
                        "category": category,
                    },
                )
                _emit(event, run_dir=run_dir, fallback_dir=fallback_dir)
                if registry_log is not None:
                    writer.append_jsonl(
                        registry_log,
                        {
                            "session_id": session_id,
                            "run_id": run_id,
                            "snapshot_id": snapshot_id,
                            "surface_id": art.surface_id,
                            "content_hash": art.content_hash,
                            "source_path": art.source_path,
                            "category": category,
                            "discovered_at": art.discovered_at.isoformat(),
                        },
                    )
        except Exception:  # justified: fail-open, event emission must not break registry resolution
            logger.debug("surface_registered_emit_failed", exc_info=True)
        return registry

    @classmethod
    def build(
        cls,
        *,
        data_root: Path | None = None,
        repo_root: Path | None = None,
        now: datetime | None = None,
    ) -> SurfaceRegistry:
        """Walk the bundled surface and materialize one record per governing artifact.

        FR-1 artifact coverage:
            - Bundled ``trw_mcp.data/`` contents (agents, skills, hooks,
              prompts, surfaces, config) — resolved from ``data_root``.
            - Repo-root governing documents: root ``CLAUDE.md``,
              ``FRAMEWORK.md``, and sub-``CLAUDE.md`` files under the
              package source trees — resolved from ``repo_root``.

        Args:
            data_root: Override the bundled data root (tests pass a tmp
                directory). When None, resolves ``trw_mcp.data`` via
                importlib.resources.
            repo_root: Override the monorepo root. When None, walks up from
                the resolved data root looking for a ``CLAUDE.md`` +
                ``.trw/`` pair. May be None in PyPI-only installs — then
                repo-root artifacts are simply skipped.
            now: Override the discovered_at timestamp (tests use this for
                reproducible snapshots).
        """
        resolved_data_root = data_root if data_root is not None else _resolve_data_root()
        # Test and tooling callers often pass a synthetic data_root to inspect
        # only bundled-data behavior. In that mode, do not implicitly pull in
        # the live repository's root CLAUDE.md/FRAMEWORK.md. Production
        # resolvers pass repo_root explicitly when they want repo surfaces.
        resolved_repo_root = (
            repo_root if repo_root is not None else (_resolve_repo_root() if data_root is None else None)
        )
        pkg_ver = _package_version()
        fw_ver = _framework_version()
        ts = now or datetime.now(tz=timezone.utc)

        artifacts = _discover_artifacts(resolved_data_root, version=pkg_ver, now=ts)
        artifacts.extend(_discover_repo_artifacts(resolved_repo_root, version=pkg_ver, now=ts))

        return cls(
            trw_mcp_version=pkg_ver,
            framework_version=fw_ver,
            generated_at=ts,
            artifacts=tuple(artifacts),
        )

    @property
    def snapshot_id(self) -> str:
        """Stable content-addressed id for this registry."""
        return _artifacts_snapshot_id(
            self.artifacts,
            trw_mcp_version=self.trw_mcp_version,
            framework_version=self.framework_version,
        )

    def to_snapshot(self) -> SurfaceSnapshot:
        """Serialize to a frozen :class:`SurfaceSnapshot` (FR-2 artifact)."""
        return SurfaceSnapshot(
            snapshot_id=self.snapshot_id,
            trw_mcp_version=self.trw_mcp_version,
            framework_version=self.framework_version,
            generated_at=self.generated_at,
            artifacts=tuple(sorted(self.artifacts, key=lambda a: (a.surface_id, a.source_path))),
        )

    def component_rollup(self) -> dict[str, ComponentFingerprint]:
        """Return per-category rollup summary (secondary view for dashboards)."""
        data_root = _resolve_data_root()
        if data_root is None:
            return {cat: ComponentFingerprint() for cat, _, _ in _COMPONENTS}
        out: dict[str, ComponentFingerprint] = {}
        for category, subdir, patterns in _COMPONENTS:
            component_root = data_root / subdir if subdir else data_root
            out[category] = _component_rollup(component_root, patterns)
        return out


@lru_cache(maxsize=8)
def _cached_registry(cache_key: str, data_root_str: str | None, repo_root_str: str | None) -> SurfaceRegistry:
    """Cached registry resolver keyed on a caller-supplied cache key."""
    del cache_key  # present only for lru_cache keying
    root = Path(data_root_str) if data_root_str else None
    repo_root = Path(repo_root_str) if repo_root_str else None
    return SurfaceRegistry.build(data_root=root, repo_root=repo_root)


def resolve_surface_registry(*, refresh: bool = False) -> SurfaceRegistry:
    """Resolve the :class:`SurfaceRegistry` for the current TRW installation.

    Args:
        refresh: Bypass the per-process cache to force re-walk.
    """
    data_root = _resolve_data_root()
    repo_root = _resolve_repo_root()
    cache_key = f"refresh-{datetime.now(tz=timezone.utc).isoformat()}" if refresh else ""
    return _cached_registry(cache_key, str(data_root) if data_root else None, str(repo_root) if repo_root else None)


def resolve_surface_snapshot(*, refresh: bool = False) -> SurfaceSnapshot:
    """Back-compat wrapper — resolve the registry and return its snapshot view."""
    return resolve_surface_registry(refresh=refresh).to_snapshot()


def clear_snapshot_cache() -> None:
    """Drop the per-process registry cache. Test-only."""
    _cached_registry.cache_clear()


__all__ = [
    "ComponentFingerprint",
    "SurfaceArtifact",
    "SurfaceRegistry",
    "SurfaceSnapshot",
    "clear_snapshot_cache",
    "resolve_surface_registry",
    "resolve_surface_snapshot",
]
