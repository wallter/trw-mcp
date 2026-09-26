"""opencode distill channel bootstrap — single entry-point.

Installs the remaining opencode distill channel artifacts at ``init-project`` and
``update-project`` time.  Called as a facade from ``bootstrap/_opencode.py``.

Install ordering (FR26):
  1. ``generate_opencode_config()``          (existing — called before this)
  2. ``generate_agents_md()``               (existing — modified to acquire agents-md.lock)
  3. ``install_opencode_distill_channels()`` (this module — all distill artifacts)

Artifacts written:
  - AGENTS.md distill segment (between trw:distill:start / trw:distill:end markers)
  - .opencode/commands/trw-before-edit.md
  - .opencode/commands/trw-distill-hotspots.md
  - .opencode/commands/trw-distill-conventions.md
  - .opencode/agents/trw-distill-explorer.md
  - .trw/client-profile.env (TRW_CLIENT_PROFILE=opencode)
  - .trw/channels/manifest.yaml merged with six opencode ChannelEntry records
  - .gitignore entries for channel-events.jsonl and client-profile.env

PRD-DIST-2403 FR25-FR30.

PRD-CORE-239 FR01 removed this client's instruction-file segment channel(s);
the counts above are the post-removal reality. Prose that outlives the code it
describes is defect pattern P7 — the class this whole removal was about.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.channels._gitignore import add_gitignore_entry
from trw_mcp.channels._manifest_loader import ManifestValidationError
from trw_mcp.channels.opencode._custom_commands import install_custom_commands
from trw_mcp.channels.opencode._explorer_agent import install_explorer_agent

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_channel_manifest",
    "install_opencode_distill_channels",
]

# Gitignore entries (FR28)
_GITIGNORE_ENTRIES = [
    ".trw/telemetry/channel-events.jsonl",
    ".trw/client-profile.env",
]

# Client profile env file (FR19)
_CLIENT_PROFILE_ENV_PATH = ".trw/client-profile.env"
_CLIENT_PROFILE_ENV_CONTENT = "TRW_CLIENT_PROFILE=opencode\n"
_MANIFEST_DATA = Path(__file__).parent.parent / "data" / "opencode" / "channels" / "manifest-opencode.yaml"


# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Load manifest-opencode.yaml and merge five ChannelEntry records.

    Merge is additive — existing entries for other clients are preserved (FR30).
    All-or-nothing: if any entry fails validation, raises ManifestValidationError
    and no partial state is written (FR29).

    Args:
        repo_root: Repository root directory.

    Returns:
        Dict with ``status`` and ``count`` of entries added.

    Raises:
        ManifestValidationError: If any entry fails ChannelEntry.model_validate().
    """
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "opencode")

    log.debug(
        "opencode_manifest_bootstrapped",
        added=added,
        total=total,
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_opencode_distill_channels(
    repo_root: Path,
    sidecar_data: object | None = None,
    sidecar_sha: str | None = None,
) -> dict[str, object]:
    """Install the opencode distill channel artifacts.

    This is the single entry-point called from ``bootstrap/_opencode.py``
    as a thin facade.

    Args:
        repo_root: Repository root directory.
        sidecar_data: Accepted and ignored. PRD-CORE-239 FR01 removed the
            AGENTS.md distill segment this fed; the parameter is retained so the
            `bootstrap/_opencode.py` facade and its callers keep their signature
            through the removal rather than changing two contracts at once.
        sidecar_sha: Accepted and ignored, same reason.

    Returns:
        Dict with install status for each artifact group.
    """
    del sidecar_data, sidecar_sha  # see Args — retained, not used

    results: dict[str, object] = {}
    # Both production call sites (_init_project_ide.py and _ide_targets_distill.py)
    # do `errors = dc_result.get("errors"); if isinstance(errors, list): ...`.
    # This key never existed, so every install failure below was collected into
    # a value nobody could read and then dropped — the caller could not tell a
    # clean install from one where the manifest was rejected and no .gitignore
    # entry was written. The five sibling installers all return this bucket.
    errors: list[str] = []
    results["errors"] = errors

    # 1. The pre-run baseline for the user-edit guard. These files are recorded
    #    by the manifest recorder registry (PRD-INFRA-192 FR12), not written here.
    from trw_mcp.bootstrap._version_manifest import _manifest_content_hashes, _read_manifest

    manifest_hashes = _manifest_content_hashes(_read_manifest(repo_root))

    # 2. The AGENTS.md distill segment is gone (PRD-CORE-239 FR01). opencode was
    #    the one client whose segment was invoked directly by its installer
    #    rather than through the placeholder-only `trw_channel_render`, so it is
    #    also the only one whose removal changes real behaviour: the marker
    #    block is no longer written. `uninstall` still strips any block a
    #    previous version left behind, via the `trw:distill:start/end` pair in
    #    MARKER_REGISTRY.
    results["agents_md_segment"] = "removed_prd_core_239"

    # 3. Custom command files
    cmd_results = install_custom_commands(repo_root, manifest_hashes=manifest_hashes)
    results["custom_commands"] = {k: v["status"] for k, v in cmd_results.items()}
    errors.extend(
        f"opencode command {name} not written: {res.get('error')}"
        for name, res in cmd_results.items()
        if res["status"] == "error"
    )

    # 4. Explorer agent — PRD-CORE-239: licence-gated. Third sibling of cc-05
    #    and ag-02; all three install an agent that cannot function without the
    #    proprietary package. Note the CUSTOM COMMANDS above are deliberately
    #    NOT gated: their bodies call `trw_code(mode="hint")` and `trw_recall`
    #    over MCP, and run `trw-mcp code risk` from a shell (PRD-CORE-300 S4),
    #    all of which work on the free tier. Only the distill-dependent agent
    #    is withheld.
    from trw_mcp.bootstrap._distill_entitlement import distill_artifacts_entitled

    if distill_artifacts_entitled(artifact="opencode-explorer-agent", repo_root=repo_root):
        explorer_result = install_explorer_agent(repo_root, manifest_hashes=manifest_hashes)
        results["explorer_agent"] = explorer_result["status"]
        if explorer_result["status"] == "error":
            errors.append(f"opencode distill explorer agent not written: {explorer_result.get('error')}")
    else:
        results["explorer_agent"] = "skipped_unentitled"

    # 5. Write client-profile.env (FR19)
    env_path = repo_root / _CLIENT_PROFILE_ENV_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(_CLIENT_PROFILE_ENV_CONTENT, encoding="utf-8")
    results["client_profile_env"] = "written"

    # 6. Bootstrap channel manifest (FR27 / FR30)
    # Fail-soft: a bad manifest data file surfaces via result dict, not a raised
    # exception, so a single invalid entry does not abort the entire install
    # (matches cursor's bootstrap pattern — OC-M2 audit fix).
    try:
        manifest_result = bootstrap_channel_manifest(repo_root)
        results["manifest"] = manifest_result
    except ManifestValidationError as exc:
        log.info(
            "opencode_manifest_validation_error",
            error=str(exc),
            outcome="error",
        )
        results["manifest"] = {"status": "error", "error": str(exc)}
        errors.append(f"opencode channel manifest invalid: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.info(
            "opencode_manifest_bootstrap_failed",
            error=str(exc),
            outcome="error",
        )
        results["manifest"] = {"status": "error", "error": str(exc)}
        errors.append(f"opencode channel manifest bootstrap failed: {exc}")

    # 7. Gitignore entries (FR28)
    failed_entries: list[str] = []
    for entry_str in _GITIGNORE_ENTRIES:
        try:
            add_gitignore_entry(repo_root, entry_str)
        except Exception as exc:
            log.info(
                "opencode_gitignore_entry_error",
                entry=entry_str,
                error=str(exc),
                outcome="error",
            )
            failed_entries.append(entry_str)
            errors.append(f"opencode .gitignore entry {entry_str!r} failed: {exc}")
    # ``results["gitignore"]`` used to be set to "updated" unconditionally,
    # immediately after a loop that swallows every write failure — so a run in
    # which no entry was written at all was byte-identical to a clean one.
    results["gitignore"] = "updated" if not failed_entries else "partial"

    log.debug(
        "opencode_distill_channels_installed",
        repo_root=str(repo_root),
        outcome="ok",
    )
    return results
