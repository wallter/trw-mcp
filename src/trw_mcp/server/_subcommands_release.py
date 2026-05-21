"""Release-related CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat.

Three helpers:
- ``_run_build_release`` — handles the ``build-release`` subcommand
- ``_push_release`` — pushes release metadata to the backend
- ``_get_framework_version`` — extracts framework version from bundled FRAMEWORK.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

import structlog

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

logger = structlog.get_logger(__name__)


def _read_pyproject_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    if isinstance(project, dict):
        return str(project.get("version", ""))
    return ""


def _read_package_json_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get("version", "")) if isinstance(data, dict) else ""


def collect_version_status(project_root: Path | None = None) -> dict[str, object]:
    """Collect labeled package/framework/live-server version status."""
    root = project_root or Path.cwd()
    from trw_mcp import __version__ as live_server_version
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    framework_asset_path = root / ".trw" / "frameworks" / "VERSION.yaml"
    asset = FileStateReader(base_dir=root).read_yaml(framework_asset_path) if framework_asset_path.exists() else {}
    package_versions = {
        "trw-mcp": _read_pyproject_version(root / "trw-mcp" / "pyproject.toml"),
        "trw-memory": _read_pyproject_version(root / "trw-memory" / "pyproject.toml"),
        "memory-ts": _read_package_json_version(root / "packages" / "memory-ts" / "package.json"),
    }
    framework_protocol_version = TRWConfig().framework_version
    installed_asset_version = str(asset.get("framework_version", ""))
    asset_mcp_version = str(asset.get("trw_mcp_version", ""))
    mismatches: list[str] = []
    if installed_asset_version and installed_asset_version != framework_protocol_version:
        mismatches.append("framework_protocol_vs_installed_asset")
    if asset_mcp_version and asset_mcp_version != package_versions["trw-mcp"]:
        mismatches.append("trw_mcp_package_vs_installed_asset")
    if live_server_version != package_versions["trw-mcp"]:
        mismatches.append("trw_mcp_package_vs_live_server")
    return {
        "taxonomy": {
            "package_version": "package manifest version (pyproject.toml/package.json)",
            "framework_protocol_version": "TRWConfig.framework_version",
            "installed_asset_version": ".trw/frameworks/VERSION.yaml framework_version",
            "live_server_version": "imported trw_mcp.__version__ for the running process",
        },
        "versions": {
            "packages": package_versions,
            "framework_protocol_version": framework_protocol_version,
            "installed_asset_version": installed_asset_version,
            "installed_asset_trw_mcp_version": asset_mcp_version,
            "live_server_version": live_server_version,
        },
        "compatibility_matrix": {
            "independent_packages": ["trw-memory", "memory-ts"],
            "must_match": [
                ["packages.trw-mcp", "installed_asset_trw_mcp_version"],
                ["packages.trw-mcp", "live_server_version"],
                ["framework_protocol_version", "installed_asset_version"],
            ],
        },
        "compatible": not mismatches,
        "mismatches": mismatches,
    }


def assert_version_status_compatible(project_root: Path | None = None) -> dict[str, object]:
    """Return status or raise SystemExit when the release version gate fails."""
    status = collect_version_status(project_root)
    compatible = bool(status["compatible"])
    mismatches = cast("list[str]", status["mismatches"])
    if not compatible:
        raise SystemExit(f"version compatibility gate failed: {','.join(mismatches)}")
    return status


def _run_build_release(args: argparse.Namespace) -> None:
    """Handle the ``build-release`` subcommand."""
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
