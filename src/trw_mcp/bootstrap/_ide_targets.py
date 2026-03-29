# Parent facade: bootstrap/_template_updater.py
"""IDE-specific artifact update logic.

Extracted from ``_template_updater.py`` to keep the facade focused on
CLAUDE.md marker management and framework file copying.  All public
names are re-exported from ``_template_updater.py`` so existing import
paths are preserved.

Covers:
- OpenCode artifact updates (FR15)
- Cursor artifact updates (FR05, FR06, FR07)
- Config target_platforms patching
- CLAUDE.md sync (learnings promotion during update)
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

from ._utils import (
    ProgressCallback,
    _minimal_claude_md,
    resolve_ide_targets,
)

logger = structlog.get_logger(__name__)

# CLAUDE.md markers — needed by _extract_trw_section_content and _run_claude_md_sync
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


# ---------------------------------------------------------------------------
# OpenCode update helper (FR15)
# ---------------------------------------------------------------------------


def _update_opencode_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
) -> None:
    """Update opencode artifacts when opencode is detected (FR15).

    Checks IDE targets and, when opencode is included, calls
    ``generate_opencode_config()`` to smart-merge ``opencode.json`` and
    ``generate_agents_md()`` to sync ``AGENTS.md``.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._opencode import generate_agents_md, generate_opencode_config

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "opencode" not in ide_targets:
        return

    # Update opencode.json (smart merge)
    try:
        oc_result = generate_opencode_config(target_dir)
        result["created"].extend(oc_result.get("created", []))
        result["updated"].extend(oc_result.get("updated", []))
        result["errors"].extend(oc_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, opencode update is best-effort
        result["warnings"].append(f"opencode.json update skipped: {exc}")
        return

    # Update AGENTS.md with platform-generic TRW section
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.claude_md._static_sections import (
            render_agents_trw_section,
            render_minimal_protocol,
        )

        _cfg = get_config()
        if _cfg.effective_ceremony_mode == "light":
            agents_section = render_minimal_protocol()
        else:
            agents_section = render_agents_trw_section()
        agents_result = generate_agents_md(target_dir, agents_section)
        result["created"].extend(agents_result.get("created", []))
        result["updated"].extend(agents_result.get("updated", []))
        result["errors"].extend(agents_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, AGENTS.md update is best-effort
        result["warnings"].append(f"AGENTS.md update skipped: {exc}")


def _extract_trw_section_content() -> str:
    """Extract the content between trw:start and trw:end from _minimal_claude_md."""
    full = _minimal_claude_md()
    start_idx = full.find(_TRW_START_MARKER)
    end_idx = full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        # Return content between the markers (exclusive)
        inner_start = start_idx + len(_TRW_START_MARKER)
        return full[inner_start:end_idx].strip()
    return ""


# ---------------------------------------------------------------------------
# Cursor update helper (FR05, FR06, FR07)
# ---------------------------------------------------------------------------


def _update_cursor_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
) -> None:
    """Update Cursor artifacts when Cursor is detected (FR05, FR06, FR07).

    Checks IDE targets and, when cursor is included, calls
    ``generate_cursor_hooks()`` (FR05), ``generate_cursor_rules()`` (FR06),
    and ``generate_cursor_mcp_config()`` (FR07) to smart-merge/update the
    respective ``.cursor/`` files.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._cursor import (
        generate_cursor_hooks,
        generate_cursor_mcp_config,
        generate_cursor_rules,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "cursor" not in ide_targets:
        return

    # FR05: Update .cursor/hooks.json (smart merge)
    try:
        hooks_result = generate_cursor_hooks(target_dir)
        result["created"].extend(hooks_result.get("created", []))
        result["updated"].extend(hooks_result.get("updated", []))
        result["errors"].extend(hooks_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, cursor update is best-effort
        result["warnings"].append(f".cursor/hooks.json update skipped: {exc}")

    # FR06: Update .cursor/rules/trw-ceremony.mdc
    try:
        trw_section = _extract_trw_section_content()
        rules_result = generate_cursor_rules(target_dir, trw_section)
        result["created"].extend(rules_result.get("created", []))
        result["updated"].extend(rules_result.get("updated", []))
        result["errors"].extend(rules_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, cursor rules update is best-effort
        result["warnings"].append(f".cursor/rules/trw-ceremony.mdc update skipped: {exc}")

    # FR07: Update .cursor/mcp.json (smart merge)
    try:
        mcp_result = generate_cursor_mcp_config(target_dir)
        result["created"].extend(mcp_result.get("created", []))
        result["updated"].extend(mcp_result.get("updated", []))
        result["errors"].extend(mcp_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, cursor mcp update is best-effort
        result["warnings"].append(f".cursor/mcp.json update skipped: {exc}")


# ---------------------------------------------------------------------------
# Config target_platforms update helper
# ---------------------------------------------------------------------------


def _update_config_target_platforms(
    target_dir: Path,
    ide_targets: list[str],
    result: dict[str, list[str]],
) -> None:
    """Update target_platforms in config.yaml to match detected/override IDE targets.

    Preserves all other config fields. Fail-open: errors go to result["warnings"].
    """
    import yaml

    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.exists():
        return

    try:
        content = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content) or {}
        current: list[str] = data.get("target_platforms", ["claude-code"])
        if sorted(current) == sorted(ide_targets):
            result["preserved"].append(str(config_path))
            return
        data["target_platforms"] = ide_targets
        config_path.write_text(
            yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        result["updated"].append(str(config_path))
    except Exception as exc:  # justified: fail-open, config update is best-effort
        result["warnings"].append(f"target_platforms config update skipped: {exc}")


# ---------------------------------------------------------------------------
# CLAUDE.md sync
# ---------------------------------------------------------------------------


def _run_claude_md_sync(
    target_dir: Path,
    result: dict[str, list[str]],
    timeout: int = 30,
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

        # Skip LLM-dependent sync when no Anthropic API key is available
        # (installer runs outside Claude Code sessions -- no auth)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            result["warnings"].append(
                "CLAUDE.md LLM sync skipped (no ANTHROPIC_API_KEY) \u2014 will complete on next trw_session_start()"
            )
            return

        def _do_sync() -> dict[str, object]:
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
        result["updated"].append(f"CLAUDE.md synced (learnings promoted: {learnings_promoted})")
    except concurrent.futures.TimeoutError:
        logger.warning(
            "claude_md_sync_timeout",
            timeout_seconds=timeout,
            target_dir=str(target_dir),
        )
        result["warnings"].append(
            f"CLAUDE.md sync timed out ({timeout}s) \u2014 will complete on next trw_session_start()"
        )
    except Exception as exc:  # justified: fail-open, CLAUDE.md sync is best-effort
        logger.warning(
            "claude_md_sync_failed",
            error=str(exc),
            target_dir=str(target_dir),
        )
        result["warnings"].append(f"CLAUDE.md sync skipped: {exc}")
    finally:
        os.chdir(original_cwd)
        # Reset config back to original project
        try:
            from trw_mcp.models.config import _reset_config

            _reset_config()
        except Exception:  # justified: cleanup, config reset is best-effort during finally
            logger.debug("config_reset_failed", exc_info=True)
