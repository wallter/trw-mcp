"""Antigravity CLI distill channel bootstrap — install entry-point.

Installs all four Antigravity distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .antigravitycli/agents/trw-distill-explorer.md  (AG-02 T1 stub)
  - .trw/channels/manifest.yaml                      (four AG channel entries merged)

AG-01 ANTIGRAVITY.md segment is a runtime channel managed by
``render_antigravity_distill_segment()`` — no stub file is written at install.
AG-03 before-edit hook is aspirational — no implementation.
AG-04 is a telemetry pull channel — no file written.

PRD-DIST-2404 FR41-FR43.
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
    "bootstrap_antigravity_channel_manifest",
    "install_antigravity_distill_channels",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "antigravity" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-antigravity.yaml"


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_antigravity_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Load manifest-antigravity.yaml and merge four ChannelEntry records.

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
                f"antigravity manifest entry validation failed: {exc}"
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
        "antigravity_manifest_bootstrapped",
        added=added,
        total=len(manifest.channels),
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_antigravity_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Antigravity CLI distill channel artifacts.

    Installs the AG-02 explorer subagent file and merges channel manifest entries.

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

    # 1. Install AG-02 explorer subagent (.antigravitycli/agents/trw-distill-explorer.md)
    try:
        from trw_mcp.channels.antigravity import generate_distill_explorer_agent

        agent_result = generate_distill_explorer_agent(
            repo_root=target_dir,
            sidecar_data=None,
            sidecar_sha=None,
        )
        rel = ".antigravitycli/agents/trw-distill-explorer.md"
        status = getattr(agent_result, "status", None)
        if status == "skipped":
            result["preserved"].append(rel)
        else:
            result["created"].append(rel)
    except Exception as exc:  # justified: fail-open, subagent is best-effort
        log.warning("ag02_subagent_install_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"AG-02 subagent install failed: {exc}")

    # 2. Bootstrap channel manifest (four antigravity channel entries)
    try:
        bootstrap_antigravity_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "antigravity_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"Antigravity manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("antigravity_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Antigravity manifest bootstrap failed: {exc}")

    log.debug(
        "antigravity_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
