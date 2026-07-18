"""Artifact-discovery helpers for SurfaceRegistry (PRD-HPO-MEAS-001 FR-1).

Belongs to the ``artifact_registry.py`` facade. Re-exported there for
back-compat (tests import ``_component_rollup`` and
``_artifacts_snapshot_id`` directly).

Hosts the constants + private helpers that walk bundled data, repo-root
governing documents, and roll them up into ``SurfaceArtifact`` records:

- Constants: ``_HASH_ALGO``, ``_COMPONENTS``, ``_REPO_ROOT_ARTIFACTS``,
  ``_SUB_CLAUDE_GLOBS``, ``_FRAMEWORK_VERSION_FALLBACK``,
  ``_PACKAGE_NAME``, ``_DATA_PACKAGE``
- Helpers: ``_hash_file``, ``_iter_files``, ``_component_rollup``,
  ``_resolve_data_root``, ``_package_version``, ``_framework_version``,
  ``_artifacts_snapshot_id``, ``_resolve_repo_root``,
  ``_discover_repo_artifacts``, ``_discover_artifacts``

Extracted as DIST-243 batch 31 to keep the parent ``artifact_registry.py``
under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from importlib import metadata as _metadata
from importlib.resources import files as _pkg_files
from pathlib import Path
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from trw_mcp.telemetry.artifact_registry import ComponentFingerprint, SurfaceArtifact

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
    "docs/**/CLAUDE.md",
)


def _hash_file(path: Path) -> tuple[str, int]:
    """Return ``(sha256_hex, byte_count)`` for a single file.

    Fail-open on any read anomaly. The registry's invariant #3
    (``artifact_registry`` module docstring) mandates that ``build`` must not
    raise on a disk-state anomaly. A governing file can become unreadable
    between the caller's ``is_file()`` gate and this open — a permission
    change, a torn read, or a TOCTOU vanish while a concurrent agent rewrites
    it (common in this monorepo). Without containment, the resulting
    ``OSError`` propagates out of ``_discover_artifacts`` → ``build`` and, in
    the session-start path, collapses the *entire* surface snapshot to ``""``,
    discarding identity for every other artifact rather than skipping the one
    bad file. Returning the same ``("", 0)`` sentinel as the non-file branch
    contains the fault to a single record.
    """
    if not path.is_file():
        return "", 0
    h = hashlib.new(_HASH_ALGO)
    size = 0
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                h.update(chunk)
                size += len(chunk)
    except OSError as exc:  # justified: invariant #3, one unreadable file must not abort the walk
        logger.warning("artifact_hash_read_failed", path=str(path), error=type(exc).__name__, detail=str(exc))
        return "", 0
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
    from trw_mcp.telemetry.artifact_registry import ComponentFingerprint as _ComponentFingerprint

    files = _iter_files(root, patterns)
    if not files:
        return _ComponentFingerprint()

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
    return _ComponentFingerprint(
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


def _artifacts_snapshot_id(
    artifacts: Iterable[SurfaceArtifact], *, trw_mcp_version: str, framework_version: str
) -> str:
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


def _discover_repo_artifacts(repo_root: Path | None, *, version: str, now: datetime) -> list[SurfaceArtifact]:
    """Discover repo-root governing artifacts (CLAUDE.md, FRAMEWORK.md, sub-CLAUDE.md)."""
    from trw_mcp.telemetry.artifact_registry import SurfaceArtifact as _SurfaceArtifact

    if repo_root is None or not repo_root.exists():
        return []
    out: list[SurfaceArtifact] = []

    # Root-level named artifacts.
    for category, rel in _REPO_ROOT_ARTIFACTS:
        candidate = repo_root / rel
        if candidate.is_file():
            digest, _ = _hash_file(candidate)
            out.append(
                _SurfaceArtifact(
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
            _SurfaceArtifact(
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
    from trw_mcp.telemetry.artifact_registry import SurfaceArtifact as _SurfaceArtifact

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
                _SurfaceArtifact(
                    surface_id=f"{category}:{rel}",
                    content_hash=digest,
                    version=version,
                    discovered_at=now,
                    source_path=rel,
                )
            )
    return out
