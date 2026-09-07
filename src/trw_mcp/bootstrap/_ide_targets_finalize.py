"""Post-IDE-update finalization helpers.

Belongs to the ``_ide_targets.py`` facade. Re-exported there for back-compat.

Two finalization helpers run after per-IDE artifact updates complete:
- ``_update_config_target_platforms`` — augment ``.trw/config.yaml``
  ``target_platforms`` list (PRD-FIX-076 — append-only, never narrow).
- ``_run_claude_md_sync`` — invoke the LLM-backed CLAUDE.md sync to
  resolve placeholders and promote learnings.

Plus the ``_LEGACY_PROFILE_RENAMES`` rename map.

Extracted as DIST-243 batch 41 to keep the parent ``_ide_targets.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import ClaudeMdSyncResultDict

logger = structlog.get_logger(__name__)


_LEGACY_PROFILE_RENAMES: dict[str, str] = {
    # Sprint 91 (PRD-CORE-136 / PRD-CORE-137): bare `cursor` was split into
    # cursor-ide (full ceremony, GUI) and cursor-cli (light, headless).
    # Migrate the legacy identifier to cursor-ide so existing dev configs
    # still resolve to a sensible profile after upgrade.
    "cursor": "cursor-ide",
}


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
        existing: list[str] = list(data.get("target_platforms", ["claude-code"]))

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
            result["preserved"].append(str(config_path))
            logger.debug(
                "config_target_platforms_unchanged",
                target_platforms=merged,
                requested=ide_targets,
            )
            return

        data["target_platforms"] = merged
        config_path.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        result["updated"].append(str(config_path))
        logger.info(
            "config_target_platforms_augmented",
            outcome="success",
            previous=existing,
            current=merged,
            added=added,
            requested=ide_targets,
        )
    except (OSError, yaml.YAMLError) as exc:  # justified: fail-open, config update is best-effort
        result.setdefault("warnings", []).append(f"target_platforms config update skipped: {type(exc).__name__}: {exc}")
        logger.warning(
            "config_target_platforms_update_failed",
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
    ``refusals`` and still returns normally. Reading only ``learnings_promoted``
    turned that into "CLAUDE.md synced" — the operator was told their instruction
    file had been updated when the writer had deliberately left it alone, which
    is the one outcome they need to know about (their CLAUDE.md/AGENTS.md is now
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
    timeout: int = 30,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Run CLAUDE.md sync after update to resolve placeholders and promote learnings.

    Temporarily changes cwd to the target project so that resolve_project_root()
    finds the correct .trw/ directory and learnings database.
    Fail-open: rendering errors are logged as warnings but never break the update.

    Stdout/stderr are suppressed during sync to prevent structlog noise and
    SDK error messages from leaking into the installer's progress pipe.

    A *timeout* (seconds, default 30) prevents the sync from blocking the
    installer indefinitely when LLM initialisation or network calls stall.
    """
    import concurrent.futures
    import io
    import sys

    original_cwd = Path.cwd()
    try:
        os.chdir(target_dir)

        from trw_mcp.models.config import _reset_config, get_config
        from trw_mcp.state.claude_md import execute_claude_md_sync
        from trw_mcp.state.llm_helpers import LLMClient
        from trw_mcp.state.persistence import FileStateReader

        # Reset config so it picks up the target project's .trw/config.yaml
        _reset_config()
        config = get_config()
        reader = FileStateReader()

        # NOTE: there is deliberately no ANTHROPIC_API_KEY guard here.
        # This sync is pure file I/O and never reaches an LLM: dispatch_for_profile
        # does `del reader, llm`, and _build_sync_result hardcodes `llm_used: False`.
        # A previous guard returned early whenever the key was unset, which is the
        # normal case for a Claude Code *subscription* user. On that path
        # update-project ran only the carrier-unaware writer
        # (_update_project.py -> _template_updater -> _update_claude_md_trw_section)
        # and silently reverted CLAUDE.md externalization on every run, reporting
        # success with the warning buried in result["warnings"]. Gating a
        # deterministic write on an unrelated credential is what made the
        # deterministic half unreachable. Pinned by TestSyncRunsWithoutApiKey.
        # LLMClient() constructs fine without a key; if it ever raises, the
        # except-Exception handler below records it as a warning (fail-open).

        def _do_sync() -> ClaudeMdSyncResultDict:
            # Suppress stdout/stderr so structlog noise and SDK auth errors
            # don't leak into the installer's subprocess pipe.
            saved_stdout, saved_stderr = sys.stdout, sys.stderr
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            try:
                llm = LLMClient()
                return execute_claude_md_sync(
                    scope="root",
                    target_dir=None,
                    config=config,
                    reader=reader,
                    llm=llm,
                    instruction_manifest_hashes=manifest_hashes,
                )
            finally:
                sys.stdout, sys.stderr = saved_stdout, saved_stderr

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(_do_sync)
            sync_result = future.result(timeout=timeout)
        finally:
            # shutdown(wait=False) so a hung worker thread (e.g. LLMClient
            # blocking on network) doesn't block the installer indefinitely.
            pool.shutdown(wait=False, cancel_futures=True)

        learnings_promoted = sync_result.get("learnings_promoted", 0)
        logger.info(
            "claude_md_sync_completed",
            learnings_promoted=learnings_promoted,
            target_dir=str(target_dir),
        )
        if _record_sync_refusals(sync_result, result, target_dir):
            # A refused write did not happen. Claiming "CLAUDE.md synced" on top
            # of the warning would leave the truthful line and the false one in
            # the same report, and update-project's summary shows `updated`.
            return
        result["updated"].append(f"CLAUDE.md synced (learnings promoted: {learnings_promoted})")
    except concurrent.futures.TimeoutError:
        logger.warning(
            "claude_md_sync_timeout",
            timeout_seconds=timeout,
            target_dir=str(target_dir),
        )
        result.setdefault("warnings", []).append(
            f"CLAUDE.md sync timed out ({timeout}s) — will complete on next trw_session_start()"
        )
    except Exception as exc:  # justified: fail-open, CLAUDE.md sync is best-effort
        logger.warning(
            "claude_md_sync_failed",
            error=str(exc),
            target_dir=str(target_dir),
        )
        result.setdefault("warnings", []).append(f"CLAUDE.md sync skipped: {exc}")
    finally:
        os.chdir(original_cwd)
        # Reset config back to original project
        try:
            from trw_mcp.models.config import _reset_config

            _reset_config()
        except Exception:  # justified: cleanup, config reset is best-effort during finally
            logger.debug("config_reset_failed", exc_info=True)
