"""Cursor IDE + cursor-cli distill channel bootstrap — install entry-point.

Installs the remaining Cursor distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .cursor/rules/distill-conventions.mdc     (CUR-01 T0 stub)
  - .cursor/rules/distill-dangerous-edits.mdc (CUR-03 T0 stub)
  - .trw/channels/manifest.yaml               (five CUR channel entries merged)

T0 stub MDC files are written via MdcEmitter.bootstrap_stubs() which handles
gitignore management for the hotspot pattern (CUR-02).

PRD-DIST-2401 FR41-FR43.

PRD-CORE-239 FR01 removed this client's instruction-file segment channel(s);
the counts above are the post-removal reality. Prose that outlives the code it
describes is defect pattern P7 — the class this whole removal was about.
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

    # 1. PRD-CORE-239: T0 stub MDC files are NO LONGER WRITTEN.
    #    `render_presence_beacon_mdc` hardcoded the rule description to
    #    "TRW distill data available — quota exceeded, use
    #    trw_codebase_risk_report() for full analysis" — text Cursor surfaces to
    #    the agent as the rule's summary. For a T0 stub that is false twice
    #    over: no data exists, and no quota was ever hit. It shipped on every
    #    `init-project` for every Cursor project regardless of licence.
    #    The CUR MDC channels never rendered real content in production
    #    (wiring gate: NEVER_FIRED, ledger UF-010). Cursor keeps the
    #    cursor-mcp-tool-return and cursor-pretooluse-hint surfaces, which
    #    reach the free MCP tools.

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
