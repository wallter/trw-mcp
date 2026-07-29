"""Release-related CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat.

Handlers:
- ``_run_build_release`` — handles the ``build-release`` subcommand
- ``_run_version_status`` — handles the ``version-status`` subcommand
- ``_push_release`` — pushes release metadata to the backend
- ``_get_framework_version`` — extracts framework version from bundled FRAMEWORK.md

This module is also the facade for the version-status taxonomy: the typed
shapes and manifest readers live in ``_version_status_manifests.py`` and the
live/historical layers in ``_version_status_layers.py``; both are re-exported
here so ``from trw_mcp.server._subcommands_release import ...`` keeps working.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog

from trw_mcp.server._version_status_layers import (
    historical_installer_layer,
    live_process_layer,
)
from trw_mcp.server._version_status_manifests import (
    PACKAGE_JSON_KEYS,
    PACKAGE_KEY_MEMORY_TS,
    PACKAGE_KEY_TRW_MCP,
    PACKAGE_KEY_TRW_MEMORY,
    PYPROJECT_PACKAGE_KEYS,
    InstalledAssetResult,
    PackageVersions,
    VersionManifestKind,
    VersionReadResult,
    VersionStatus,
    VersionValues,
    _append_diagnostics,
    _extended_package_manifests,
    _read_installed_asset_versions,
    _read_package_json_version_or_unknown,
    _read_pyproject_version_or_unknown,
)

logger = structlog.get_logger(__name__)

__all__ = [
    "PACKAGE_JSON_KEYS",
    "PACKAGE_KEY_MEMORY_TS",
    "PACKAGE_KEY_TRW_MCP",
    "PACKAGE_KEY_TRW_MEMORY",
    "PYPROJECT_PACKAGE_KEYS",
    "InstalledAssetResult",
    "PackageVersions",
    "VersionManifestKind",
    "VersionReadResult",
    "VersionStatus",
    "VersionValues",
    "assert_version_status_compatible",
    "collect_version_status",
    "historical_installer_layer",
    "live_process_layer",
]


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
