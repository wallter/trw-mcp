"""Antigravity CLI distill channel bootstrap — install entry-point.

Installs the remaining Antigravity distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .agents/agents/trw-distill-explorer.md                       (AG-02 T1 stub; PRD-CORE-252 destination)
  - .antigravitycli/hooks.json                                   (AG-03 PreToolUse hook entry)
  - .antigravitycli/hooks/trw_before_edit_telemetry.py           (AG-03 hook script)
  - .trw/channels/manifest.yaml                                  (three AG channel entries merged)

AG-01 ANTIGRAVITY.md segment is a runtime channel managed by
``render_antigravity_distill_segment()`` — no stub file is written at install.
AG-03 before-edit hook empirically confirmed 2026-05-28 (agy v1.0.2):
  hooks file is .antigravitycli/hooks.json (separate from settings.json),
  event key "PreToolUse", format {"PreToolUse": [{"matcher": "...", "command": "..."}]}.
AG-04 is a telemetry pull channel — no file written.

PRD-DIST-2404 FR41-FR43.

PRD-CORE-239 FR01 removed this client's instruction-file segment channel(s);
the counts above are the post-removal reality. Prose that outlives the code it
describes is defect pattern P7 — the class this whole removal was about.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from typing_extensions import assert_never

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

    # 1. Install AG-02 explorer subagent (.agents/agents/trw-distill-explorer.md,
    #    the FR01 format registry's antigravity-cli destination).
    #    PRD-CORE-239: licence-gated — sibling of cc-05 and the opencode
    #    explorer. All three install an agent that cannot work without the
    #    proprietary package; gating one and not the others would have been a
    #    subset defect inside the fix.
    try:
        from trw_mcp.agents.agent_formats import agent_format_for
        from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled
        from trw_mcp.channels.antigravity import generate_distill_explorer_agent

        # A plain guard, not an exception: routing the skip through the
        # fail-open handler below would log "AG-02 subagent install failed",
        # which is false — nothing failed, the project is simply unlicensed.
        if distill_artifacts_entitled(artifact="ag-02-distill-explorer", repo_root=target_dir):
            agent_result = generate_distill_explorer_agent(
                repo_root=target_dir,
                sidecar_data=None,
                sidecar_sha=None,
            )
            # Read from the same registry the writer itself uses (never
            # restated) so this report can't drift from where the file
            # actually landed the way the hardcoded literal it replaced did.
            rel = agent_result.path or agent_format_for("antigravity-cli").destination_for("trw-distill-explorer")
            # Exhaustive over AgentWriteResult.status's real Literal set
            # (P4/P10 fix): the prior `status == "skipped"` comparison could
            # never match `"skipped_same_sha"`, so every outcome -- including
            # `"error"` -- fell into the `else` branch and was reported as a
            # successful create, silently swallowing write failures.
            status = agent_result.status
            if status == "written":
                result["created"].append(rel)
            elif status == "skipped_same_sha":
                result["preserved"].append(rel)
            elif status == "error":
                result["errors"].append(f"AG-02 subagent install failed: {agent_result.error}")
            else:
                assert_never(status)
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
