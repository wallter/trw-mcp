"""Cursor IDE + cursor-cli distill channel bootstrap — install entry-point.

Installs all five Cursor distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .cursor/rules/distill-conventions.mdc     (CUR-01 T0 stub)
  - .cursor/rules/distill-dangerous-edits.mdc (CUR-03 T0 stub)
  - .trw/channels/manifest.yaml               (five CUR channel entries merged)

T0 stub MDC files are written via MdcEmitter.bootstrap_stubs() which handles
gitignore management for the hotspot pattern (CUR-02).

PRD-DIST-2401 FR41-FR43.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.bootstrap._file_ops import _new_result
from trw_mcp.channels._manifest_loader import ManifestValidationError

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_cursor_channel_manifest",
    "install_cursor_distill_channels",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "cursor" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-cursor.yaml"


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_cursor_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Add Cursor channel entries while preserving other clients."""
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "cursor")
    log.debug(
        "cursor_manifest_bootstrapped",
        added=added,
        total=total,
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_cursor_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Cursor distill channel artifacts.

    Writes T0 stub MDC files via MdcEmitter.bootstrap_stubs() (handles
    gitignore management). Merges channel manifest entries.

    Args:
        target_dir: Repository root directory.
        force: When True, overwrite existing artifacts unconditionally.

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors`` lists.
    """
    result = _new_result()

    # 1. Bootstrap T0 stub MDC files via MdcEmitter (CUR-01, CUR-03)
    try:
        from trw_mcp.channels.cursor import MdcEmitter

        emitter = MdcEmitter(target_dir)
        stubs_result = emitter.bootstrap_stubs()
        for rel in stubs_result.get("created", []):
            result["created"].append(str(rel))
    except Exception as exc:  # justified: fail-open, MDC stubs are best-effort
        log.warning("cursor_mdc_stubs_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Cursor MDC stub install failed: {exc}")

    # 2. Bootstrap channel manifest (five CUR channel entries)
    try:
        bootstrap_cursor_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "cursor_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"Cursor manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("cursor_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Cursor manifest bootstrap failed: {exc}")

    log.debug(
        "cursor_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
