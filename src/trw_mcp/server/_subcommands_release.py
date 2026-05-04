"""Release-related CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat.

Three helpers:
- ``_run_build_release`` — handles the ``build-release`` subcommand
- ``_push_release`` — pushes release metadata to the backend
- ``_get_framework_version`` — extracts framework version from bundled FRAMEWORK.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


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
