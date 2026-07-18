"""Antigravity CLI distill channel bootstrap — install entry-point.

Installs all four Antigravity distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .antigravitycli/agents/trw-distill-explorer.md              (AG-02 T1 stub)
  - .antigravitycli/hooks.json                                   (AG-03 PreToolUse hook entry)
  - .antigravitycli/hooks/trw_before_edit_telemetry.py           (AG-03 hook script)
  - .trw/channels/manifest.yaml                                  (four AG channel entries merged)

AG-01 ANTIGRAVITY.md segment is a runtime channel managed by
``render_antigravity_distill_segment()`` — no stub file is written at install.
AG-03 before-edit hook empirically confirmed 2026-05-28 (agy v1.0.2):
  hooks file is .antigravitycli/hooks.json (separate from settings.json),
  event key "PreToolUse", format {"PreToolUse": [{"matcher": "...", "command": "..."}]}.
AG-04 is a telemetry pull channel — no file written.

PRD-DIST-2404 FR41-FR43.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.bootstrap._file_ops import _new_result
from trw_mcp.channels._manifest_loader import ManifestValidationError

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
    """Add Antigravity channel entries while preserving other clients."""
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "antigravity")
    log.debug(
        "antigravity_manifest_bootstrapped",
        added=added,
        total=total,
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
    result = _new_result()

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

    # 2. Install AG-03 before-edit hook
    #    Empirically confirmed 2026-05-28: hooks.json is separate from settings.json,
    #    event key "PreToolUse", format {"PreToolUse": [{"matcher": "...", "command": "..."}]}
    try:
        from trw_mcp.channels.antigravity import install_before_edit_hook

        hook_result = install_before_edit_hook(target_dir, overwrite=force)
        hook_script_rel = ".antigravitycli/hooks/trw_before_edit_telemetry.py"
        hooks_json_rel = ".antigravitycli/hooks.json"
        if hook_result.get("skipped"):
            result["preserved"].append(hook_script_rel)
            result["preserved"].append(hooks_json_rel)
        elif hook_result.get("installed"):
            result["created"].append(hook_script_rel)
            result["created"].append(hooks_json_rel)
        elif hook_result.get("error"):
            result["errors"].append(f"AG-03 hook install failed: {hook_result['error']}")
    except Exception as exc:  # justified: fail-open, hook is best-effort
        log.warning("ag03_hook_install_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"AG-03 hook install failed: {exc}")

    # 3. Bootstrap channel manifest (four antigravity channel entries)
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
