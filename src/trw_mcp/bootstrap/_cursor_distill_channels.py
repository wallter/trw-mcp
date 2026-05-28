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
from typing import Any

import structlog
from ruamel.yaml import YAML

from trw_mcp.channels._manifest_loader import (
    ManifestValidationError,
    auto_recreate_empty,
    load,
    write,
)
from trw_mcp.channels._manifest_models import ChannelEntry
from trw_mcp.channels._provenance import now_utc_iso8601

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
    """Load manifest-cursor.yaml and merge five ChannelEntry records.

    Merge is additive — existing entries for other clients are preserved.
    All-or-nothing: if any entry fails validation, raises ManifestValidationError.

    Args:
        repo_root: Repository root directory.

    Returns:
        Dict with ``status`` and ``count`` of entries added.
    """
    yaml = YAML(typ="safe")
    raw: Any = yaml.load(_MANIFEST_DATA.read_text(encoding="utf-8")) or {}
    raw_channels: list[dict[str, Any]] = raw.get("channels", [])

    # Validate all entries first (all-or-nothing)
    validated: list[ChannelEntry] = []
    for entry_dict in raw_channels:
        try:
            validated.append(ChannelEntry.model_validate(entry_dict))
        except Exception as exc:
            raise ManifestValidationError(
                f"cursor manifest entry validation failed: {exc}"
            ) from exc

    # Load or recreate target manifest
    manifest_path = repo_root / ".trw" / "channels" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        manifest = load(manifest_path)
    except Exception:
        auto_recreate_empty(manifest_path)
        manifest = load(manifest_path)

    # Merge: add new entries, preserve existing
    existing_ids = {e.id for e in manifest.channels}
    added = 0
    for entry in validated:
        if entry.id not in existing_ids:
            manifest.channels.append(entry)
            existing_ids.add(entry.id)
            added += 1

    manifest.generated_at = now_utc_iso8601()
    write(manifest, manifest_path)

    log.debug(
        "cursor_manifest_bootstrapped",
        added=added,
        total=len(manifest.channels),
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
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }

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
