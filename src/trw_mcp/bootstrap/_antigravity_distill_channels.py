"""Antigravity CLI distill channel bootstrap — install entry-point.

Installs the remaining Antigravity distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .agents/agents/trw-distill-explorer.md                       (AG-02 T1 stub; PRD-CORE-252 destination)
  - .trw/channels/manifest.yaml                                  (three AG channel entries merged)

AG-01 ANTIGRAVITY.md segment is a runtime channel managed by
``render_antigravity_distill_segment()`` — no stub file is written at install.
AG-03 before-edit hook is NOT written (UF-BOOT-08, 2026-10-02). It was confirmed only on
agy v1.0.2 (.antigravitycli/hooks.json, flat schema); agy 1.2.14 lists hooks from
<workspace>/.agents/hooks.json in a grouped named-hook schema
({"<name>": {"PreToolUse": [{"matcher", "hooks": [{"type", "command"}]}]}}) and does not
list the legacy file. The step logs ``ag03_hook_skipped``; ``trw-mcp doctor``'s
``antigravity_hook`` row reports installs left by earlier versions.
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
    The AG-03 hook is withheld (see the module docstring).

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

    # 2. AG-03 before-edit hook: WITHHELD (UF-BOOT-08). The installer wrote the hook to
    #    .antigravitycli/hooks.json in a flat schema confirmed only on agy v1.0.2. agy 1.2.14
    #    (checked 2026-10-02 with `agy -p /hooks` in a scratch workspace) lists hooks from
    #    <workspace>/.agents/hooks.json in a grouped, named-hook schema and does not list the
    #    legacy file, and the hook script's camelCase/decision contract differs too. Writing a
    #    registration agy never reads is worse than none, and a guessed schema is not allowed,
    #    so nothing is written until the installer, uninstall surfaces and managed-artifact
    #    recorder are moved together. `trw-mcp doctor` (antigravity_hook) flags old installs.
    log.info(
        "ag03_hook_skipped",
        reason="unverified_path_and_schema",
        agy_reads=".agents/hooks.json (grouped named-hook schema, agy 1.2.14)",
        legacy_path=".antigravitycli/hooks.json",
        outcome="skipped",
    )

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
