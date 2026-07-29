"""Typed version-status shapes and package/asset manifest readers.

Belongs to the ``_subcommands_release.py`` facade. Re-exported there for
back-compat, so ``from trw_mcp.server._subcommands_release import ...`` keeps
working for every existing caller.

Extracted so ``collect_version_status`` and the release CLI subcommand handlers
stay under the 350-effective-LOC module gate. This module owns the *reading*
half of the version taxonomy (TypedDict/dataclass shapes, the public package
key lists, the on-demand monorepo release topology, and the fail-open manifest
readers); ``_subcommands_release.py`` owns the *assembly* half
(``collect_version_status`` + the CLI handlers) and the live/historical layer
wiring that tests monkeypatch on the facade.
"""

from __future__ import annotations

import importlib.metadata
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from typing_extensions import TypedDict

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

logger = structlog.get_logger(__name__)

VersionManifestKind = Literal["authoritative", "optional"]
PackageVersions = dict[str, str]


class VersionValues(TypedDict):
    """Machine-readable versions emitted by ``version-status``."""

    packages: PackageVersions
    framework_protocol_version: str
    installed_asset_version: str
    installed_asset_trw_mcp_version: str
    installed_asset_present: bool
    live_server_version: str


class VersionStatus(TypedDict):
    """JSON-compatible shape returned by PRD-INFRA-120 version status checks."""

    taxonomy: dict[str, str]
    versions: VersionValues
    compatibility_matrix: dict[str, object]
    live_process: dict[str, object]
    historical: dict[str, object]
    compatible: bool
    mismatches: list[str]
    warnings: list[str]
    errors: list[str]


@dataclass(frozen=True)
class VersionReadResult:
    """Result of reading a package/version manifest without raising through CLI boundaries."""

    version: str
    warning: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class InstalledAssetResult:
    """Parsed installed framework asset metadata."""

    present: bool
    data: dict[str, object]
    error: str | None = None


PACKAGE_KEY_TRW_MCP = "trw-mcp"
PACKAGE_KEY_TRW_MEMORY = "trw-memory"
PACKAGE_KEY_MEMORY_TS = "memory-ts"

# Publicly-shipped packages only. This module ships inside the public ``trw-mcp``
# wheel, so it must not enumerate the monorepo's proprietary siblings. The full
# monorepo package taxonomy (proprietary API/frontend/eval/pipeline packages)
# lives in the canonical, non-shipped ``release-packages.yaml`` at the monorepo
# root and is loaded on demand by :func:`_extended_package_manifests`. When that
# file is absent (any public install), only these public packages are checked.
PYPROJECT_PACKAGE_KEYS: tuple[tuple[str, str], ...] = (
    (PACKAGE_KEY_TRW_MCP, "trw-mcp"),
    (PACKAGE_KEY_TRW_MEMORY, "trw-memory"),
)
PACKAGE_JSON_KEYS: tuple[tuple[str, str], ...] = ((PACKAGE_KEY_MEMORY_TS, "packages/memory-ts"),)

#: Canonical monorepo release-topology manifest (NOT shipped in the trw-mcp
#: subtree). Lives at the monorepo root and is the single source of truth for
#: the proprietary package taxonomy.
_RELEASE_TOPOLOGY_FILENAME = "release-packages.yaml"

#: Package keys already covered by the hardcoded public lists above — skipped
#: when merging the external topology so they are never double-counted.
_PUBLIC_PACKAGE_KEYS: frozenset[str] = frozenset({PACKAGE_KEY_TRW_MCP, PACKAGE_KEY_TRW_MEMORY, PACKAGE_KEY_MEMORY_TS})


def _extended_package_manifests(root: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Load the monorepo's non-public package taxonomy from ``release-packages.yaml``.

    Returns ``(pyproject_entries, package_json_entries)`` where each entry is
    ``(package_key, package_dir)``. Public packages are omitted (already handled
    by the hardcoded lists). Fails open to empty lists when the manifest is
    absent (public installs) or unreadable, so version status never crashes and
    the shipped wheel carries no proprietary package names.
    """
    manifest_path = root / _RELEASE_TOPOLOGY_FILENAME
    if not manifest_path.exists():
        return [], []
    try:
        data = FileStateReader(base_dir=root).read_yaml(manifest_path)
    except StateError as exc:
        logger.warning(
            "release_topology_unreadable",
            op="version_status",
            outcome="degraded",
            path=str(manifest_path),
            error=str(exc),
        )
        return [], []
    packages = data.get("packages")
    if not isinstance(packages, list):
        return [], []
    pyproject: list[tuple[str, str]] = []
    package_json: list[tuple[str, str]] = []
    for entry in packages:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        directory = entry.get("dir")
        kind = entry.get("manifest_kind")
        if not isinstance(key, str) or not isinstance(directory, str) or key in _PUBLIC_PACKAGE_KEYS:
            continue
        if kind == "pyproject":
            pyproject.append((key, directory))
        elif kind == "package.json":
            package_json.append((key, directory))
    return pyproject, package_json


def _read_pyproject_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    if isinstance(project, dict):
        return str(project.get("version", ""))
    return ""


def _installed_distribution_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _read_pyproject_version_or_unknown(
    path: Path,
    *,
    label: str,
    distribution: str | None = None,
    fallback: str = "unknown",
    kind: VersionManifestKind = "optional",
) -> VersionReadResult:
    if path.exists():
        try:
            return VersionReadResult(version=_read_pyproject_version(path))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            message = f"{label} manifest unreadable at {path}: {exc}"
            logger.warning(
                "version_manifest_unreadable",
                op="version_status",
                outcome="degraded" if kind == "optional" else "failed",
                label=label,
                path=str(path),
                error=str(exc),
            )
            if kind == "authoritative":
                return VersionReadResult(version="unknown", error=message)
            return VersionReadResult(version="unknown", warning=message)
    if fallback != "unknown":
        return VersionReadResult(version=fallback)
    if distribution:
        installed = _installed_distribution_version(distribution)
        if installed != "unknown":
            return VersionReadResult(version=installed)
    return VersionReadResult(version="unknown")


def _read_package_json_version_or_unknown(path: Path, *, label: str) -> VersionReadResult:
    if not path.exists():
        return VersionReadResult(version="unknown")
    try:
        return VersionReadResult(version=_read_package_json_version(path))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"{label} manifest unreadable at {path}: {exc}"
        logger.warning(
            "version_manifest_unreadable",
            op="version_status",
            outcome="degraded",
            label=label,
            path=str(path),
            error=str(exc),
        )
        return VersionReadResult(version="unknown", warning=message)


def _read_package_json_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get("version", "")) if isinstance(data, dict) else ""


def _read_installed_asset_versions(root: Path) -> InstalledAssetResult:
    framework_asset_path = root / ".trw" / "frameworks" / "VERSION.yaml"
    if not framework_asset_path.exists():
        return InstalledAssetResult(present=False, data={})
    try:
        return InstalledAssetResult(
            present=True,
            data=FileStateReader(base_dir=root).read_yaml(framework_asset_path),
        )
    except StateError as exc:
        logger.warning(
            "installed_asset_manifest_unreadable",
            op="version_status",
            outcome="failed",
            path=str(framework_asset_path),
            error=str(exc),
        )
        return InstalledAssetResult(
            present=True,
            data={},
            error=f"installed asset manifest unreadable at {framework_asset_path}: {exc}",
        )


def _append_diagnostics(
    result: VersionReadResult,
    *,
    warnings: list[str],
    errors: list[str],
    mismatches: list[str],
    mismatch_id: str | None = None,
) -> None:
    if result.warning:
        warnings.append(result.warning)
    if result.error:
        errors.append(result.error)
        if mismatch_id:
            mismatches.append(mismatch_id)
