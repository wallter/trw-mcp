"""Release-related CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat.

Three helpers:
- ``_run_build_release`` — handles the ``build-release`` subcommand
- ``_push_release`` — pushes release metadata to the backend
- ``_get_framework_version`` — extracts framework version from bundled FRAMEWORK.md
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog
from typing_extensions import TypedDict

from trw_mcp.exceptions import StateError
from trw_mcp.server._version_status_layers import (
    historical_installer_layer,
    live_process_layer,
)
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


def collect_version_status(project_root: Path | None = None) -> VersionStatus:
    """Collect labeled package/framework/live-server version status."""
    from trw_mcp import __version__ as live_server_version
    from trw_mcp.models.config import TRWConfig

    root = (project_root or Path.cwd()).resolve()
    warnings: list[str] = []
    errors: list[str] = []
    mismatches: list[str] = []
    asset_result = _read_installed_asset_versions(root)
    asset = asset_result.data
    extended_pyproject, extended_package_json = _extended_package_manifests(root)
    package_versions: PackageVersions = {}
    for package_key, package_dir in (*PYPROJECT_PACKAGE_KEYS, *extended_pyproject):
        result = _read_pyproject_version_or_unknown(
            root / package_dir / "pyproject.toml",
            label=package_key,
            distribution=package_key if package_key in {PACKAGE_KEY_TRW_MCP, PACKAGE_KEY_TRW_MEMORY} else None,
            fallback=live_server_version if package_key == PACKAGE_KEY_TRW_MCP else "unknown",
            kind="authoritative" if package_key == PACKAGE_KEY_TRW_MCP else "optional",
        )
        package_versions[package_key] = result.version
        _append_diagnostics(
            result,
            warnings=warnings,
            errors=errors,
            mismatches=mismatches,
            mismatch_id="trw_mcp_package_manifest_unreadable" if package_key == PACKAGE_KEY_TRW_MCP else None,
        )
    for package_key, package_dir in (*PACKAGE_JSON_KEYS, *extended_package_json):
        result = _read_package_json_version_or_unknown(
            root / package_dir / "package.json",
            label=package_key,
        )
        package_versions[package_key] = result.version
        _append_diagnostics(result, warnings=warnings, errors=errors, mismatches=mismatches)
    if asset_result.error:
        errors.append(asset_result.error)
        mismatches.append("installed_asset_manifest_unreadable")
    if not asset_result.present:
        errors.append("installed asset manifest missing at .trw/frameworks/VERSION.yaml")
        mismatches.append("installed_asset_manifest_missing")

    framework_protocol_version = TRWConfig().framework_version
    installed_asset_version = str(asset.get("framework_version", ""))
    asset_mcp_version = str(asset.get("trw_mcp_version", ""))
    if asset_result.present and not asset_result.error and not installed_asset_version:
        errors.append("installed asset manifest missing framework_version")
        mismatches.append("installed_asset_framework_version_missing")
    if asset_result.present and not asset_result.error and not asset_mcp_version:
        errors.append("installed asset manifest missing trw_mcp_version")
        mismatches.append("installed_asset_trw_mcp_version_missing")
    if installed_asset_version and installed_asset_version != framework_protocol_version:
        mismatches.append("framework_protocol_vs_installed_asset")
    mcp_package_version = package_versions["trw-mcp"]
    if asset_mcp_version and mcp_package_version != "unknown" and asset_mcp_version != mcp_package_version:
        mismatches.append("trw_mcp_package_vs_installed_asset")
    if mcp_package_version != "unknown" and live_server_version != mcp_package_version:
        mismatches.append("trw_mcp_package_vs_live_server")
    live_process = live_process_layer()
    live_currentness = str(live_process.get("currentness") or "unknown")
    if live_currentness != "current":
        mismatches.append(f"live_process_currentness_{live_currentness}")
        errors.append(f"live process currentness is {live_currentness}; release requires current")
    historical = historical_installer_layer(root)
    status: VersionStatus = {
        "taxonomy": {
            "package_version": "package manifest version (pyproject.toml/package.json)",
            "framework_protocol_version": "TRWConfig.framework_version",
            "installed_asset_version": ".trw/frameworks/VERSION.yaml framework_version",
            "live_server_version": "imported trw_mcp.__version__ for the running process",
            "live_process": "frozen connected-process fingerprint currentness (canon registry + realized surface)",
            "historical": "install-time snapshot; historical only, never a current authority",
        },
        "versions": {
            "packages": package_versions,
            "framework_protocol_version": framework_protocol_version,
            "installed_asset_version": installed_asset_version,
            "installed_asset_trw_mcp_version": asset_mcp_version,
            "installed_asset_present": asset_result.present,
            "live_server_version": live_server_version,
        },
        "compatibility_matrix": {
            "independent_packages": sorted(package for package in package_versions if package != PACKAGE_KEY_TRW_MCP),
            "must_match": [
                ["packages.trw-mcp", "installed_asset_trw_mcp_version"],
                ["packages.trw-mcp", "live_server_version"],
                ["framework_protocol_version", "installed_asset_version"],
            ],
        },
        "live_process": live_process,
        "historical": historical,
        "compatible": not mismatches,
        "mismatches": mismatches,
        "warnings": warnings,
        "errors": errors,
    }
    logger.info(
        "version_status_collected",
        op="version_status",
        outcome="compatible" if status["compatible"] else "incompatible",
        project_root=str(root),
        mismatches=mismatches,
        warnings=len(warnings),
        errors=len(errors),
    )
    return status


def assert_version_status_compatible(project_root: Path | None = None) -> VersionStatus:
    """Return status or raise SystemExit when the release version gate fails."""
    status = collect_version_status(project_root)
    compatible = bool(status["compatible"])
    mismatches = status["mismatches"]
    if not compatible:
        raise SystemExit(f"version compatibility gate failed: {','.join(mismatches)}")
    return status


def _run_build_release(args: argparse.Namespace) -> None:
    """Handle the ``build-release`` subcommand."""
    assert_version_status_compatible(Path.cwd())

    from trw_mcp.release_builder import build_release_bundle

    version: str | None = getattr(args, "version", None)
    output_dir = Path(getattr(args, "output_dir", ".")).resolve()

    result = build_release_bundle(version=version, output_dir=output_dir)

    logger.info(
        "build_release_complete",
        op="build_release",
        bundle_path=str(result["path"]),
        version=str(result["version"]),
        checksum=str(result["checksum"]),
        size_bytes=result["size_bytes"],
    )

    push = getattr(args, "push", False)
    if push:
        backend_url = getattr(args, "backend_url", None)
        api_key = getattr(args, "api_key", None)
        if not backend_url or not api_key:
            logger.error("push_missing_args", op="build_release", detail="--push requires --backend-url and --api-key")
            sys.exit(1)
        _push_release(result, backend_url, api_key)

    sys.exit(0)


def _run_version_status(args: argparse.Namespace) -> None:
    """Handle the ``version-status`` subcommand."""
    status = collect_version_status(Path(getattr(args, "project_root", ".")).resolve())
    print(json.dumps(status, indent=2, sort_keys=True))
    if getattr(args, "check", False) and not bool(status["compatible"]):
        sys.exit(1)
    sys.exit(0)


def _push_release(result: dict[str, object], backend_url: str, api_key: str) -> None:
    """Push release metadata to the backend."""
    import json as _json
    import urllib.request

    url = f"{backend_url.rstrip('/')}/v1/releases"
    payload = _json.dumps(
        {
            "version": str(result["version"]),
            "artifact_url": str(result["path"]),
            "artifact_checksum": str(result["checksum"]),
            "artifact_size_bytes": int(str(result["size_bytes"])),
            "framework_version": _get_framework_version(),
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310 — URL comes from CLI --backend-url arg (operator-supplied, not end-user input); HTTPS enforced by deployment
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — see Request comment above
            data = _json.loads(resp.read().decode("utf-8"))
            logger.info(
                "release_published",
                op="push_release",
                version=data.get("version", "?"),
                backend_url=backend_url,
            )
    except Exception as exc:  # justified: boundary, backend publish API call may fail
        logger.exception("release_publish_failed", op="push_release", error=str(exc))
        sys.exit(1)


def _get_framework_version() -> str:
    """Extract framework version from bundled FRAMEWORK.md."""
    from trw_mcp.state._helpers import read_framework_version

    return read_framework_version()
