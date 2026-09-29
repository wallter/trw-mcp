"""Post-IDE-update finalization helpers.

Belongs to the ``_ide_targets.py`` facade. Re-exported there for back-compat.

Two finalization helpers run after per-IDE artifact updates complete:
- ``_update_config_target_platforms`` — augment ``.trw/config.yaml``
  ``target_platforms`` list (PRD-FIX-076 — append-only, never narrow).
- ``_run_claude_md_sync`` — invoke the instruction-file sync to
  resolve placeholders and promote learnings.

Plus the ``_LEGACY_PROFILE_RENAMES`` rename map.

Extracted as DIST-243 batch 41 to keep the parent ``_ide_targets.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from trw_mcp._checkout_write import UnsafeWriteError, write_checkout_file
from trw_mcp.models.typed_dicts import ClaudeMdSyncResultDict

logger = structlog.get_logger(__name__)


_LEGACY_PROFILE_RENAMES: dict[str, str] = {
    # Sprint 91 (PRD-CORE-136 / PRD-CORE-137): bare `cursor` was split into
    # cursor-ide (full ceremony, GUI) and cursor-cli (light, headless).
    # Migrate the legacy identifier to cursor-ide so existing dev configs
    # still resolve to a sensible profile after upgrade.
    "cursor": "cursor-ide",
}


def _recorded_platforms(data: dict[str, Any], *, default: list[str]) -> list[str]:
    """The ``target_platforms`` list in loaded config *data*; a null value reads as empty.

    An absent key takes *default*; ``target_platforms:`` with nothing under it
    (a hand-emptied list) parses to ``None`` and means "no clients recorded".
    """
    if "target_platforms" not in data:
        return list(default)
    return list(data["target_platforms"] or [])


def _update_config_target_platforms(
    target_dir: Path,
    ide_targets: list[str],
    result: dict[str, list[str]],
) -> None:
    """Augment target_platforms in config.yaml without narrowing the user list.

    Behavior contract (v0.44.1 — fixes PRD-FIX-076):
      - The user's existing target_platforms list is **never narrowed**. New
        entries from ``ide_targets`` are appended in order; existing entries
        are preserved.
      - Legacy profile identifiers (currently: ``cursor`` → ``cursor-ide``)
        are silently migrated. See ``_LEGACY_PROFILE_RENAMES``.
      - Retired identifiers (``aider`` — 2026-07-11) are DROPPED from the list
        (not migrated to a replacement, since the artifacts differ) and a result
        warning records the retirement + migration hint. Existing on-disk files
        are left untouched; uninstall handles their cleanup on demand.
      - Duplicates are de-duplicated, preserving first occurrence.
      - When the merged list equals the existing list (no new IDEs, no legacy
        rename, and no retired id dropped), the file is preserved (not rewritten).
      - All other config fields preserved.

    Prior behavior (pre-0.44.1) replaced the entire list with ``ide_targets``,
    which destroyed multi-platform configurations when ``--ide <single>`` was
    passed. The current contract guarantees augmentation, never narrowing.

    Fail-open: errors go to result["warnings"]; YAML/IO failures do not block
    other dispatch steps.
    """
    import yaml

    from ._utils import _RETIRED_IDES

    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.exists():
        return

    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        existing = _recorded_platforms(data, default=["claude-code"])

        # Build the merged list:
        #   1. Migrate legacy identifiers in existing entries
        #   2. Drop retired identifiers (record a warning + migration hint)
        #   3. Deduplicate (first occurrence wins)
        #   4. Append any ide_targets entries not already present
        merged: list[str] = []
        for entry in existing:
            normalized = _LEGACY_PROFILE_RENAMES.get(entry, entry)
            if normalized in _RETIRED_IDES:
                result.setdefault("warnings", []).append(f"{normalized} support retired — {_RETIRED_IDES[normalized]}")
                logger.info("target_platform_retired_dropped", client=normalized)
                continue
            if normalized not in merged:
                merged.append(normalized)
        added: list[str] = []
        for new_id in ide_targets:
            if new_id in _RETIRED_IDES:
                continue
            if new_id not in merged:
                merged.append(new_id)
                added.append(new_id)

        if merged == existing:
            result.setdefault("preserved", []).append(str(config_path))
            logger.debug(
                "config_target_platforms_unchanged",
                target_platforms=merged,
                requested=ide_targets,
            )
            return

        data["target_platforms"] = merged
        write_checkout_file(target_dir, config_path, yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
        result.setdefault("updated", []).append(str(config_path))
        logger.info(
            "config_target_platforms_augmented",
            outcome="success",
            previous=existing,
            current=merged,
            added=added,
            requested=ide_targets,
        )
    except (OSError, UnsafeWriteError, yaml.YAMLError) as exc:  # justified: fail-open, config update is best-effort
        result.setdefault("warnings", []).append(f"target_platforms config update skipped: {type(exc).__name__}: {exc}")
        logger.warning(
            "config_target_platforms_update_failed",
            error_class=type(exc).__name__,
            error=str(exc),
        )


def _remove_config_target_platform(
    target_dir: Path,
    client_id: str,
    result: dict[str, list[str]],
) -> None:
    """Drop *client_id* from ``target_platforms`` in ``.trw/config.yaml``.

    CLIENT-REMOVE (installer refinement 5.1.0): the one deliberate NARROWING
    counterpart to :func:`_update_config_target_platforms`'s append-only
    contract (PRD-FIX-076) — a user who explicitly asked to remove a client's
    surfaces must not see it come back on the next bare ``update-project``,
    which reads this list. A missing config, or a client not currently
    listed, is a no-op (not an error): removal is idempotent.

    Fail-open: errors go to ``result["warnings"]``; a config update failure
    here never blocks the surface removal it accompanies.
    """
    import yaml

    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.exists():
        return
    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        existing = _recorded_platforms(data, default=[])
        if client_id not in existing:
            return
        data["target_platforms"] = [entry for entry in existing if entry != client_id]
        write_checkout_file(target_dir, config_path, yaml.safe_dump(data, default_flow_style=False, sort_keys=False))
        result.setdefault("updated", []).append(str(config_path))
        logger.info(
            "config_target_platform_removed",
            outcome="success",
            client=client_id,
            remaining=data["target_platforms"],
        )
    except (OSError, UnsafeWriteError, yaml.YAMLError) as exc:  # justified: fail-open, config update is best-effort
        result.setdefault("warnings", []).append(
            f"target_platforms removal of {client_id!r} skipped: {type(exc).__name__}: {exc}"
        )
        logger.warning(
            "config_target_platform_removal_failed",
            client=client_id,
            error_class=type(exc).__name__,
            error=str(exc),
        )


#: Human-readable gloss per ``InstructionRefusalReason``. A refusal reaches an
#: operator who is watching an installer, not a maintainer reading the PRD, so
#: the bare enum value ("non_generated_shrink") is not a message on its own.
_REFUSAL_REASON_TEXT: dict[str, str] = {
    "oversized": "the file is over the instruction-surface line limit",
    "non_generated_shrink": "the write would have deleted your own (non-generated) content",
    "total_shrink": "the write would have shrunk the file more than the guard allows",
    "unreadable_target": "the existing file could not be read",
    "backup_failed": "the safety backup could not be written",
    "backup_path_escape": "the backup path resolved outside the project",
    "write_failed": "the write itself failed",
}


def _record_sync_refusals(
    sync_result: ClaudeMdSyncResultDict,
    result: dict[str, list[str]],
    target_dir: Path,
) -> bool:
    """Surface PRD-FIX-123 policy refusals; return True when any was recorded.

    ``execute_claude_md_sync`` reports a guarded write it declined to perform in
    ``refusals`` and still returns normally. Reading only the return value
    turned that into "synced" — the operator was told their instruction
    file had been updated when the writer had deliberately left it alone, which
    is the one outcome they need to know about (their AGENTS.md is now
    stale and only they can fix it).
    """
    refusals = sync_result.get("refusals") or []
    if not refusals:
        return False
    warnings = result.setdefault("warnings", [])
    for refusal in refusals:
        name = refusal.get("file") or "instruction file"
        reason = str(refusal.get("reason") or "unspecified")
        explanation = _REFUSAL_REASON_TEXT.get(reason, reason)
        limit = refusal.get("limit")
        limit_text = f"; limit {limit} lines, file is {refusal.get('lines')}" if limit else ""
        warnings.append(f"{name} NOT updated — refused ({reason}): {explanation}{limit_text}")
        logger.warning(
            "claude_md_sync_write_refused",
            refused_file=name,
            reason=reason,
            lines=refusal.get("lines"),
            limit=limit,
            detail=refusal.get("detail"),
            target_dir=str(target_dir),
        )
    return True


def _run_claude_md_sync(
    target_dir: Path,
    result: dict[str, list[str]],
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Run the instruction-file sync after update to resolve placeholders and promote learnings.

    *target_dir* is bound as "the project" (``state._project_root_binding``), so
    resolve_project_root() and get_config() answer for it inside the sync. That
    binding is context-local: this process's cwd and cached config, which other
    threads share, are never touched (B71-118).
    Fail-open: rendering errors are logged as warnings but never break the update.
    """
    # B71-118 (sol round 2): the sync runs to completion in the CALLER's thread.
    # It used to run on a pool thread under a 30 s timeout followed by
    # shutdown(wait=False), which does not stop a running thread: a slow sync kept
    # writing instruction files after update-project's transaction had taken its
    # final diff or restored its snapshot. The timeout guarded LLM/network stalls,
    # but the sync never reaches an LLM (see the NOTE below) -- it is file I/O,
    # like every other writer in the transaction, none of which run under a timeout.
    # It also swapped sys.stdout/sys.stderr for the whole sync, which are process
    # globals: every other thread's output was swallowed meanwhile. Nothing in the
    # sync prints; its logging goes through the handlers, to stderr, never to the
    # installer's stdout progress pipe.
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._project_root_binding import installing_into
        from trw_mcp.state.claude_md import execute_claude_md_sync
        from trw_mcp.state.llm_helpers import LLMClient
        from trw_mcp.state.persistence import FileStateReader

        with installing_into(target_dir):
            # NOTE: there is deliberately no ANTHROPIC_API_KEY guard here.
            # This sync is pure file I/O and never reaches an LLM: dispatch_for_profile
            # does `del reader, llm`, and _build_sync_result hardcodes `llm_used: False`.
            # A previous guard returned early whenever the key was unset, which is the
            # normal case for a Claude Code *subscription* user. On that path
            # update-project ran only the carrier-unaware writer
            # in _template_updater
            # and silently bypassed the profile sync on every run, reporting
            # success with the warning buried in result["warnings"]. Gating a
            # deterministic write on an unrelated credential is what made the
            # deterministic half unreachable. Pinned by TestSyncRunsWithoutApiKey.
            # LLMClient() constructs fine without a key; if it ever raises, the
            # except-Exception handler below records it as a warning (fail-open).
            sync_result: ClaudeMdSyncResultDict = execute_claude_md_sync(
                scope="root",
                target_dir=None,
                config=get_config(),  # a fresh binding's own cache: the target's config
                reader=FileStateReader(),
                llm=LLMClient(),
                instruction_manifest_hashes=manifest_hashes,
            )
        logger.info("claude_md_sync_completed", target_dir=str(target_dir))
        if _record_sync_refusals(sync_result, result, target_dir):
            # A refused write did not happen. Claiming "synced" on top
            # of the warning would leave the truthful line and the false one in
            # the same report, and update-project's summary shows `updated`.
            return
        result["updated"].append("Instruction files synced")
    except Exception as exc:  # justified: fail-open, instruction sync is best-effort
        logger.warning(
            "claude_md_sync_failed",
            error=str(exc),
            target_dir=str(target_dir),
        )
        result.setdefault("warnings", []).append(f"Instruction sync skipped: {exc}")
