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

import hashlib
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata as _metadata
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import Final, Iterable

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


_HASH_ALGO = "sha256"
_FRAMEWORK_VERSION_FALLBACK = "unknown"
_PACKAGE_NAME = "trw-mcp"
_DATA_PACKAGE = "trw_mcp.data"

#: Bundled-data component categories (rooted at ``trw_mcp.data/``).
#: Mapped to (category, subdir, glob patterns). Keep stable — H4 meta-tune
#: groups candidates by these keys.
_COMPONENTS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("agents", "agents", ("**/*.md",)),
    ("skills", "skills", ("**/*.md", "**/*.yaml")),
    ("hooks", "hooks", ("**/*.sh",)),
    ("prompts", "prompts", ("**/*.py", "**/*.md")),
    ("surfaces", "surfaces", ("**/*",)),
    ("config", "", ("behavioral_protocol.yaml", "semantic_checks.yaml", "settings.json")),
)

#: Repo-root governing artifacts (PRD-HPO-MEAS-001 FR-1): root CLAUDE.md,
#: FRAMEWORK.md, and any sub-CLAUDE.md files discovered under the repo
#: tree. These are the primary governing documents every agent reads; a
#: surface-identity registry that misses them cannot correlate prompt
#: changes with outcome deltas.
_REPO_ROOT_ARTIFACTS: Final[tuple[tuple[str, str], ...]] = (
    ("claude_md_root", "CLAUDE.md"),
    ("framework_md", ".trw/frameworks/FRAMEWORK.md"),
)

#: Glob patterns (relative to repo root) for sub-CLAUDE.md discovery.
#: Scoped to package source trees to bound walk depth and skip vendor dirs.
_SUB_CLAUDE_GLOBS: Final[tuple[str, ...]] = (
    "trw-mcp/src/**/CLAUDE.md",
    "trw-mcp/tests/CLAUDE.md",
    "trw-memory/src/**/CLAUDE.md",
    "trw-distill/trw_distill/**/CLAUDE.md",
    "docs/**/CLAUDE.md",
)


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


def _hash_file(path: Path) -> tuple[str, int]:
    """Return ``(sha256_hex, byte_count)`` for a single file."""
    if not path.is_file():
        return "", 0
    h = hashlib.new(_HASH_ALGO)
    size = 0
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _iter_files(root: Path, patterns: Iterable[str]) -> list[Path]:
    """Yield regular files under ``root`` matching any of ``patterns``, sorted."""
    if not root.exists():
        return []
    seen: set[Path] = set()
    for pat in patterns:
        for p in root.glob(pat):
            if p.is_file():
                seen.add(p.resolve())
    return sorted(seen, key=lambda p: p.as_posix())


def _component_rollup(root: Path, patterns: Iterable[str]) -> ComponentFingerprint:
    """Roll up a directory into a single :class:`ComponentFingerprint` (summary view)."""
    files = _iter_files(root, patterns)
    if not files:
        return ComponentFingerprint()

    rollup = hashlib.new(_HASH_ALGO)
    total = 0
    for f in files:
        digest, size = _hash_file(f)
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

    Returns ``None`` when bundled data is not layable as a real path
    (e.g. zip-imported package).
    """
    try:
        import trw_mcp.data as data_pkg

        pkg_paths = list(getattr(data_pkg, "__path__", ()))
        if pkg_paths:
            p = Path(pkg_paths[0]).resolve()
            if p.exists() and p.is_dir():
                return p
        pkg_file = getattr(data_pkg, "__file__", None)
        if isinstance(pkg_file, str) and pkg_file:
            p = Path(pkg_file).resolve().parent
            if p.exists() and p.is_dir():
                return p
        root = _pkg_files(_DATA_PACKAGE)
        p = Path(str(root))
        return p if p.exists() and p.is_dir() else None
    except (ModuleNotFoundError, FileNotFoundError) as exc:  # justified: boundary, bundled data may be zipped
        logger.warning("artifact_registry_data_root_unavailable", error=str(exc))
        return None


def _package_version() -> str:
    """Return the installed trw-mcp version, or a sentinel if unresolvable."""
    try:
        return _metadata.version(_PACKAGE_NAME)
    except _metadata.PackageNotFoundError:  # justified: boundary, tests run without install
        return _FRAMEWORK_VERSION_FALLBACK


def _framework_version() -> str:
    """Return the TRW framework version (config-driven)."""
    try:
        from trw_mcp.models.config._main import TRWConfig  # local import to avoid cycles
    except ImportError:  # justified: boundary, config module optional in tooling contexts
        return _FRAMEWORK_VERSION_FALLBACK
    try:
        return str(TRWConfig.model_fields["framework_version"].default)
    except (KeyError, AttributeError):  # justified: scan-resilience, model evolution may rename
        return _FRAMEWORK_VERSION_FALLBACK


def _artifacts_snapshot_id(artifacts: Iterable[SurfaceArtifact], *, trw_mcp_version: str, framework_version: str) -> str:
    """Compute ``snapshot_id`` from sorted artifact records + version rollup.

    Sort key is ``(surface_id, source_path)`` so content_hash changes perturb
    the digest via the hashed payload, not via ordering.
    """
    items = sorted(artifacts, key=lambda a: (a.surface_id, a.source_path))
    h = hashlib.new(_HASH_ALGO)
    h.update(trw_mcp_version.encode("utf-8"))
    h.update(b"\x00")
    h.update(framework_version.encode("utf-8"))
    h.update(b"\x00")
    for a in items:
        h.update(a.surface_id.encode("utf-8"))
        h.update(b"\x00")
        h.update(a.content_hash.encode("ascii"))
        h.update(b"\x00")
        h.update(a.source_path.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _resolve_repo_root() -> Path | None:
    """Resolve the monorepo root by walking up from the package directory.

    Returns the first parent containing both ``CLAUDE.md`` and a ``.trw``
    directory, or ``None`` if no match. This is best-effort — callers are
    expected to pass an explicit ``repo_root`` when running outside the
    repository (PyPI install, Docker distribution).
    """
    try:
        data_root = _resolve_data_root()
    except Exception:  # justified: boundary, any import failure in _resolve_data_root is already logged
        return None
    if data_root is None:
        return None
    # Walk up from trw_mcp/data/ looking for a CLAUDE.md + .trw/ pair.
    for parent in [data_root, *data_root.parents]:
        if (parent / "CLAUDE.md").is_file() and (parent / ".trw").is_dir():
            return parent
    return None


def _discover_repo_artifacts(
    repo_root: Path | None, *, version: str, now: datetime
) -> list[SurfaceArtifact]:
    """Discover repo-root governing artifacts (CLAUDE.md, FRAMEWORK.md, sub-CLAUDE.md)."""
    if repo_root is None or not repo_root.exists():
        return []
    out: list[SurfaceArtifact] = []

    # Root-level named artifacts.
    for category, rel in _REPO_ROOT_ARTIFACTS:
        candidate = repo_root / rel
        if candidate.is_file():
            digest, _ = _hash_file(candidate)
            out.append(
                SurfaceArtifact(
                    surface_id=f"{category}:{rel}",
                    content_hash=digest,
                    version=version,
                    discovered_at=now,
                    source_path=rel,
                )
            )

    # Sub-CLAUDE.md discovery (bounded by explicit glob set so we don't
    # walk node_modules / venvs / .git).
    seen: set[Path] = set()
    for pat in _SUB_CLAUDE_GLOBS:
        for hit in repo_root.glob(pat):
            if hit.is_file():
                seen.add(hit.resolve())
    for f in sorted(seen, key=lambda p: p.as_posix()):
        digest, _ = _hash_file(f)
        try:
            rel_path = f.relative_to(repo_root).as_posix()
        except ValueError:
            rel_path = f.as_posix()
        out.append(
            SurfaceArtifact(
                surface_id=f"sub_claude_md:{rel_path}",
                content_hash=digest,
                version=version,
                discovered_at=now,
                source_path=rel_path,
            )
        )

    return out


def _discover_artifacts(data_root: Path | None, *, version: str, now: datetime) -> list[SurfaceArtifact]:
    """Walk the bundled data directory and record one ``SurfaceArtifact`` per file."""
    if data_root is None or not data_root.exists():
        return []
    out: list[SurfaceArtifact] = []
    for category, subdir, patterns in _COMPONENTS:
        component_root = data_root / subdir if subdir else data_root
        files = _iter_files(component_root, patterns)
        for f in files:
            digest, _ = _hash_file(f)
            try:
                rel = f.relative_to(data_root).as_posix()
            except ValueError:
                rel = f.as_posix()
            out.append(
                SurfaceArtifact(
                    surface_id=f"{category}:{rel}",
                    content_hash=digest,
                    version=version,
                    discovered_at=now,
                    source_path=rel,
                )
            )
    return out


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
        resolved_repo_root = repo_root if repo_root is not None else _resolve_repo_root()
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
            artifacts=tuple(
                sorted(self.artifacts, key=lambda a: (a.surface_id, a.source_path))
            ),
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
def _cached_registry(cache_key: str, data_root_str: str | None) -> SurfaceRegistry:
    """Cached registry resolver keyed on a caller-supplied cache key."""
    del cache_key  # present only for lru_cache keying
    root = Path(data_root_str) if data_root_str else None
    return SurfaceRegistry.build(data_root=root)


def resolve_surface_registry(*, refresh: bool = False) -> SurfaceRegistry:
    """Resolve the :class:`SurfaceRegistry` for the current TRW installation.

    Args:
        refresh: Bypass the per-process cache to force re-walk.
    """
    data_root = _resolve_data_root()
    cache_key = f"refresh-{datetime.now(tz=timezone.utc).isoformat()}" if refresh else ""
    return _cached_registry(cache_key, str(data_root) if data_root else None)


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
