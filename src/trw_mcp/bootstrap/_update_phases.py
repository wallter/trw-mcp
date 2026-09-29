"""Phase helpers for the ``update_project`` flow.

Holds the result-dict initializer, the run-record file set, the core update
phases, the behavioral-protocol writer, the distill-channel refresh and the
dirty-file restore step. Split out of ``_update_project`` for the effective-LOC
ratchet; every name is re-exported there so callers and tests keep patching
``trw_mcp.bootstrap._update_project.X``. Phases whose callees tests patch on the
``_update_project`` module (post-update phases, hook-env rewrite, the external
effects) stay in that module so those patches keep taking effect.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp._checkout_write import write_checkout_file
from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH

from ._utils import ProgressCallback
from ._version_manifest import preserve_uncommitted_changes

logger = structlog.get_logger(__name__)


def _init_result_dict(dry_run: bool) -> dict[str, list[str]]:
    """Initialize result dict with optional dry-run warning."""
    result: dict[str, list[str]] = {
        "updated": [],
        "created": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
        "cleaned": [],
    }
    if dry_run:
        result["warnings"].append("DRY RUN — no files will be modified.")
    return result


#: Files that record THAT an update ran rather than what it installed. Written
#: only when the run changed something else, so a no-op update is a no-op
#: (PRD-INFRA-190 FR03).
_RUN_RECORDS: frozenset[str] = frozenset(
    {".trw/installer-meta.yaml", ".trw/frameworks/VERSION.yaml", str(DEPLOYMENT_RELATIVE_PATH)}
)


def _generate_behavioral_protocol_md(target_dir: Path, result: dict[str, list[str]]) -> None:
    """Generate .trw/context/behavioral_protocol.md from static sections.

    PRD-CORE-093 FR03: The session-start hook reads this file once per
    session event instead of injecting the full protocol via CLAUDE.md
    on every message.
    """
    dest = target_dir / ".trw" / "context" / "behavioral_protocol.md"
    try:
        from trw_mcp.state.claude_md._static_sections import generate_behavioral_protocol_md

        content = generate_behavioral_protocol_md()
        if dest.is_file() and dest.read_text(encoding="utf-8") == content:
            return
        write_checkout_file(target_dir, dest, content)
    except Exception as exc:  # justified: fail-open — protocol file generation must not block update
        logger.warning("behavioral_protocol_md_generation_failed", error=str(exc))
        result["warnings"].append(f"behavioral_protocol.md generation failed: {exc}")


def _run_core_update_phases(
    target_dir: Path,
    effective_data: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    manifest_hashes: dict[str, str] | None = None,
    ide: str | None = None,
) -> None:
    """Execute core update phases (framework files, config, cleanup).

    PRD-FIX-068-FR05: the *prior* install/update manifest's content hashes
    (*manifest_hashes*, read in :func:`update_project` BEFORE any files are
    rewritten) are threaded into ``_update_framework_files`` → ``_update_agents``
    so genuinely user-edited agents are detected on the live update path and
    preserved (reported in ``result['modified']``) instead of being silently
    overwritten. The NEW manifest is written once, after every writer ran.

    *ide* (G1, installer refinement 5.1.0) is threaded the same way, into
    ``_update_framework_files`` → ``_update_agents`` → ``resolve_client_write_
    targets``, so a brand-new ``--ide <client>`` selection is visible to the
    agent-materialization phase in THIS run — it previously only registered
    the new client in ``target_platforms`` in the later post-update phase,
    so the first run wrote no agents for it and a second, identical run was
    required.
    """
    # PRD-INFRA-192 FR09 §3: the Claude Code scaffold dirs are ensured only
    # for a project whose recorded target_platforms (plus *ide*, if given)
    # actually own them -- an update on a ``[opencode]`` project must not
    # conjure ``.claude/skills``/``.claude/agents`` back into existence.
    from . import _update_project as up  # late lookup: tests patch these callees on ``_update_project``
    from ._client_ownership import update_scaffold_dirs

    for rel_dir in update_scaffold_dirs(target_dir, ide):
        up._ensure_dir(target_dir / rel_dir, result, on_progress)

    if on_progress:
        on_progress("Phase", "Updating framework files...")
    up._update_framework_files(target_dir, effective_data, result, on_progress, manifest_hashes, ide=ide)

    # PRD-CORE-093 FR03: Generate behavioral_protocol.md for session-start hook
    _generate_behavioral_protocol_md(target_dir, result)

    if on_progress:
        on_progress("Phase", "Updating configuration files...")
    up._update_mcp_config(target_dir, result, on_progress, ide=ide)

    if on_progress:
        on_progress("Phase", "Cleaning stale artifacts...")
    up._cleanup_stale_artifacts(target_dir, result, effective_data, manifest_hashes=manifest_hashes)

    up._check_package_version(result)


def _refresh_distill_channels(
    target_dir: Path, manifest_hashes: dict[str, str] | None, result: dict[str, list[str]]
) -> None:
    """Refresh the Claude Code distill channels; fail-open, they are additive."""
    try:
        from ._claude_code_distill_channels import install_claude_code_distill_channels

        cc_dc = install_claude_code_distill_channels(target_dir, manifest_hashes=manifest_hashes)
        # warnings/trashed carry the CC-03 withdrawal outcomes (a refused symlink, a copy left in trash)
        # and the CC-05 "kept because it was edited" report.
        for _key in ("preserved", "removed", "errors", "warnings", "trashed"):
            _items = cc_dc.get(_key)
            if isinstance(_items, list):
                result.setdefault(_key, []).extend(_items)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(f"claude-code distill channels update skipped: {exc}")


def _restore_dirty_files(
    root: Path,
    snapshot_root: Path,
    dirty: set[str],
    manifest_hashes: dict[str, str] | None,
    retired_pins: frozenset[str],
    result: dict[str, list[str]],
    adopted: list[str],
) -> None:
    """Put uncommitted files back whole, then re-apply what the restore discarded."""
    from ._client_adoption import rerecord

    preserve_uncommitted_changes(root, snapshot_root, dirty, manifest_hashes, result)
    rerecord(root, adopted, result)
    # An uncommitted config.yaml comes back whole; the pins proven above are still retired.
    if retired_pins:
        from ._version_pins import retire_default_version_pins

        retire_default_version_pins(root, result, proven=retired_pins)
    # PRD-FIX release-window fix (2026-09-27): settings.json carries no
    # manifest content hash of its own, so the guard above just discarded
    # this run's CC-03 PreToolUse registration whenever settings.json was
    # still uncommitted (the common case right after init-project, before
    # the first commit). Re-apply it on top of whatever the guard restored
    # -- idempotent, touches no other settings.json entry. The `updated`/
    # `created` lists below are computed from the snapshot-vs-final diff,
    # so this write is picked up there without a separate report here.
    from ._claude_code_distill_channels import apply_cc03_hook_registration

    apply_cc03_hook_registration(root)
