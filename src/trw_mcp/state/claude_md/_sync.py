"""CLAUDE.md sync orchestration — coordinates promotion, rendering, and merge.

AGENTS.md sync logic is in ``_agents_md.py``.
REVIEW.md constants and shared helpers are in ``_review_md.py``.
``generate_review_md`` remains here because tests patch ``_sync.recall_learnings``
and ``_sync.tempfile`` at the module level.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig

# --- AGENTS.md functions (extracted to _agents_md.py) ---
from trw_mcp.state.claude_md._agents_md import (
    _determine_write_targets as _determine_write_targets,
    _inject_learnings_to_agents as _inject_learnings_to_agents,
    _sync_agents_md_if_needed as _sync_agents_md_if_needed,
)
from trw_mcp.state.claude_md._parser import (
    load_claude_md_template,
    merge_trw_section,
    render_template,
)
from trw_mcp.state.claude_md._promotion import (
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
)

# --- REVIEW.md constants and helpers (extracted to _review_md.py) ---
from trw_mcp.state.claude_md._review_md import (
    _sanitize_summary as _sanitize_summary,
    recall_learnings as recall_learnings,
)
from trw_mcp.state.claude_md._review_md import (
    _REVIEW_MAX_LEARNINGS as _REVIEW_MAX_LEARNINGS,
    _REVIEW_MIN_IMPACT as _REVIEW_MIN_IMPACT,
    _REVIEW_TAGS as _REVIEW_TAGS,
    _REVIEW_TEMPLATE as _REVIEW_TEMPLATE,
)
from trw_mcp.state.claude_md._static_sections import (
    render_behavioral_protocol,
    render_ceremony_quick_ref,
    render_closing_reminder,
    render_framework_reference,
    render_imperative_opener,
    render_memory_harmonization,
)
from trw_mcp.state.claude_md._templates import (
    CLAUDEMD_LEARNING_CAP,
    CLAUDEMD_PATTERN_CAP,
    render_adherence,
    render_architecture,
    render_categorized_learnings,
    render_conventions,
    render_patterns,
)
from trw_mcp.state.persistence import FileStateReader

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger(__name__)

# FR04 (PRD-FIX-053): Hash file name within .trw/context/
_HASH_FILE_NAME = "claude_md_hash.txt"


def _compute_sync_hash(
    high_impact: list[dict[str, object]],
    patterns: list[dict[str, object]],
) -> str:
    """Compute a stable SHA-256 hash of the sync inputs.

    FR04 (PRD-FIX-053): Hash only deterministic inputs — sorted learning
    summaries and pattern filenames. Exclude timestamps and rendered output
    to avoid false cache invalidations.

    Includes the package version so that any trw-mcp upgrade automatically
    invalidates the cache and forces a re-render with the new rendering logic.

    Returns:
        64-character hex SHA-256 digest.
    """
    from importlib.metadata import PackageNotFoundError, version

    h = hashlib.sha256()

    # Package version — invalidates cache on any trw-mcp upgrade
    try:
        pkg_version = version("trw-mcp")
    except PackageNotFoundError:
        pkg_version = "unknown"
        logger.warning("claude_md_hash_version_unknown")
    h.update(pkg_version.encode("utf-8"))
    h.update(b"\x00")

    # Sorted learning summaries (deterministic order)
    learning_summaries = sorted(str(entry.get("summary", "")) for entry in high_impact)
    for summary in learning_summaries:
        h.update(summary.encode("utf-8"))
        h.update(b"\x00")  # null separator

    # Sorted pattern names
    pattern_names = sorted(str(p.get("name", p.get("id", ""))) for p in patterns)
    for name in pattern_names:
        h.update(name.encode("utf-8"))
        h.update(b"\x00")

    return h.hexdigest()


def _hash_file_path(trw_dir: Path) -> Path:
    """Return the hash file path."""
    return trw_dir / "context" / _HASH_FILE_NAME


def _read_stored_hash(trw_dir: Path) -> str | None:
    """Read the stored hash from .trw/context/claude_md_hash.txt."""
    hash_file = _hash_file_path(trw_dir)
    try:
        return hash_file.read_text(encoding="utf-8").strip() if hash_file.exists() else None
    except OSError:
        return None


def _write_stored_hash(trw_dir: Path, digest: str) -> None:
    """Write the hash to .trw/context/claude_md_hash.txt."""
    hash_file = _hash_file_path(trw_dir)
    try:
        hash_file.parent.mkdir(parents=True, exist_ok=True)
        hash_file.write_text(digest, encoding="utf-8")
    except OSError:
        logger.debug("claude_md_hash_write_failed", path=str(hash_file))


def invalidate_claude_md_hash(trw_dir: Path) -> None:
    """Delete the stored hash to force re-render on next sync.

    FR04 (PRD-FIX-053): Called by store_learning, update_learning, and
    auto_prune_excess_entries to ensure the cache never serves stale content.
    """
    hash_file = _hash_file_path(trw_dir)
    try:
        hash_file.unlink(missing_ok=True)
    except OSError:
        logger.debug("claude_md_hash_invalidate_failed", path=str(hash_file))



# ---------------------------------------------------------------------------
# REVIEW.md generation (PRD-CORE-084 FR08)
# ---------------------------------------------------------------------------


def _get_repo_root() -> Path | None:
    """Detect git repository root via ``git rev-parse``.

    Kept in _sync.py (not re-exported from _review_md) because tests
    patch ``_sync.subprocess.run`` at the module level.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607 — git is a well-known VCS tool; all args are static literals, no user input
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except Exception:  # justified: fail-open, git root detection failure is non-fatal
        logger.debug("git_repo_root_detection_skipped", exc_info=True)
    return None
# Constants and helpers (_sanitize_summary, _get_repo_root, recall_learnings)
# are re-exported from _review_md.py. generate_review_md stays here because
# tests patch _sync.recall_learnings and _sync.tempfile at module level.
# AGENTS.md functions re-exported from _agents_md.py:
#   _determine_write_targets, _inject_learnings_to_agents, _sync_agents_md_if_needed


def generate_review_md(
    trw_dir: Path,
    repo_root: Path | None = None,
) -> dict[str, object]:
    """Generate REVIEW.md at repo root with auto-injected learning rules.

    Full regeneration on every call. Atomic write via temp+rename.
    Fail-open: never blocks CLAUDE.md sync or delivery.

    Returns dict with keys: path, rules_count, status.
    """
    if repo_root is None:
        repo_root = _get_repo_root()
    if repo_root is None:
        logger.warning("review_md_no_repo_root")
        return {
            "path": None,
            "rules_count": 0,
            "status": "failed",
            "error": "could not determine repo root",
        }

    target_path = repo_root / "REVIEW.md"

    # Query learnings with review-relevant tags, high impact, active status
    all_learnings = recall_learnings(
        trw_dir,
        tags=_REVIEW_TAGS,
        min_impact=_REVIEW_MIN_IMPACT,
        status="active",
        max_results=_REVIEW_MAX_LEARNINGS,
    )

    # Sort by impact descending, cap at 20
    def _impact_key(entry: dict[str, object]) -> float:
        try:
            return float(str(entry.get("impact", 0.0)))
        except (ValueError, TypeError):
            return 0.0

    all_learnings.sort(key=_impact_key, reverse=True)
    selected = all_learnings[:_REVIEW_MAX_LEARNINGS]

    # Build learning entries section
    if selected:
        lines: list[str] = []
        for entry in selected:
            lid = str(entry.get("id", "unknown"))
            summary = _sanitize_summary(str(entry.get("summary", "")))
            lines.append(f"- Flag: {summary} ({lid})")
        learning_entries = "\n".join(lines)
    else:
        learning_entries = "<!-- No qualifying learnings (impact >= 0.7) found -->"

    content = _REVIEW_TEMPLATE.replace("{learning_entries}", learning_entries)

    # Atomic write: temp file + os.rename
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(target_path.parent),
            prefix=".review-md-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.rename(tmp_path, str(target_path))
        except Exception:  # justified: cleanup — remove temp file on write failure, re-raise
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
    except Exception:  # justified: fail-open — REVIEW.md write failure falls back to status dict
        logger.warning("review_md_write_failed", exc_info=True)
        return {
            "path": str(target_path),
            "rules_count": 0,
            "status": "failed",
            "error": "write failed",
        }

    rules_count = len(selected)
    logger.info(
        "review_md_generated",
        path=str(target_path),
        rules_count=rules_count,
    )
    return {
        "path": str(target_path),
        "rules_count": rules_count,
        "status": "generated",
    }


def execute_claude_md_sync(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    llm: LLMClient,
    client: str = "auto",
) -> dict[str, object]:
    """Execute the CLAUDE.md sync operation.

    Core logic extracted from the ``trw_claude_md_sync`` tool to keep
    ``tools/learning.py`` under 400 lines (Sprint 12 GAP-FR-001).

    FR04 (PRD-FIX-053): Computes a SHA-256 hash of the sync inputs before
    rendering. If the hash matches the stored hash, returns immediately with
    ``{"status": "unchanged"}`` without re-rendering the full CLAUDE.md.

    Args:
        scope: Sync scope -- "root" or "sub".
        target_dir: Target directory for sub-CLAUDE.md generation.
        config: TRW configuration.
        reader: File state reader.
        llm: LLM client instance.
        client: Target client(s) to write instructions for.
            "auto" (default) -- detect via IDE config dirs;
            "claude-code" -- write CLAUDE.md only;
            "opencode" -- write AGENTS.md only;
            "all" -- write both CLAUDE.md and AGENTS.md.

    Returns:
        Result dictionary with sync metadata.
    """
    import trw_mcp.state.claude_md as _pkg
    from trw_mcp.state.analytics import mark_promoted, update_analytics_sync
    from trw_mcp.state.llm_helpers import llm_summarize_learnings

    trw_dir = _pkg.resolve_trw_dir()
    project_root = _pkg.resolve_project_root()

    high_impact = collect_promotable_learnings(trw_dir, config, reader)
    patterns = collect_patterns(trw_dir, config, reader)
    arch_data, conv_data = collect_context_data(trw_dir, config, reader)

    # FR04 (PRD-FIX-053): Content-hash change detection.
    # Only applies to root scope — sub-CLAUDE.md generation always renders.
    if scope != "sub":
        current_hash = _compute_sync_hash(high_impact, patterns)
        stored_hash = _read_stored_hash(trw_dir)
        if stored_hash is not None and stored_hash == current_hash:
            logger.debug("claude_md_sync_cache_hit", hash=current_hash[:12])
            logger.info(
                "claude_md_sync_skip",
                reason="no_changes",
                learnings_count=len(high_impact),
                patterns_count=len(patterns),
            )
            # Determine target path for the response (consistent with full render)
            target = project_root / "CLAUDE.md"
            early_return_dict: dict[str, object] = {
                "path": str(target),
                "scope": scope,
                "status": "unchanged",
                "hash": current_hash,
                "learnings_promoted": len(high_impact),
                "patterns_included": len(patterns),
                "total_lines": 0,
                "llm_used": False,
                "agents_md_synced": False,
                "agents_md_path": None,
                "bounded_contexts_synced": 0,
            }
            # PRD-CORE-084: AGENTS.md must still sync on cache hit because
            # learning injection content may differ from the CLAUDE.md hash.
            _, write_agents = _determine_write_targets(
                client, config, project_root, scope,
            )
            synced, path = _sync_agents_md_if_needed(
                write_agents, config, project_root, trw_dir,
                recall_fn=recall_learnings,
            )
            early_return_dict["agents_md_synced"] = synced
            early_return_dict["agents_md_path"] = path
            # PRD-CORE-084 FR08: Ensure REVIEW.md stays fresh even on cache hits.
            try:
                review_result = generate_review_md(trw_dir, repo_root=project_root)
                early_return_dict["review_md"] = review_result
            except Exception:  # justified: fail-open — REVIEW.md generation must not block cache-hit return
                logger.warning("review_md_generation_failed_cache_hit", exc_info=True)
                early_return_dict["review_md"] = {"status": "failed"}
            return early_return_dict

    llm_summary: str | None = None
    if (high_impact or patterns) and config.llm_enabled and llm.available:  # pragma: no cover
        llm_summary = llm_summarize_learnings(
            high_impact,
            patterns,
            llm,
            CLAUDEMD_LEARNING_CAP,
            CLAUDEMD_PATTERN_CAP,
        )

    template = load_claude_md_template(trw_dir)

    # CLAUDE.md is the "always-on" prompt (loads every message). Keep it compact
    # and focused on triggering trw_session_start(). The session-start hook
    # delivers the full operational briefing (delegation, phases, watchlist,
    # Agent Teams) as a one-time injection per session event.
    tpl_context: dict[str, str] = {
        # Always in CLAUDE.md — role identity + session_start trigger
        "imperative_opener": render_imperative_opener(),
        "ceremony_quick_ref": render_ceremony_quick_ref(),
        "memory_harmonization": render_memory_harmonization(),
        "framework_reference": render_framework_reference(),
        "closing_reminder": render_closing_reminder(),
        # Delivered by session-start hook (one-time per session event)
        "delegation_section": "",
        "agent_teams_section": "",
        "behavioral_protocol": render_behavioral_protocol(),
        "rationalization_watchlist": "",
        "ceremony_phases": "",
        "ceremony_table": "",
        "ceremony_flows": "",
    }

    # Content sections: LLM summary replaces manual rendering when available
    if llm_summary is not None:
        tpl_context.update({
            "architecture_section": "",
            "conventions_section": "",
            "categorized_learnings": llm_summary + "\n",
            "patterns_section": "",
            "adherence_section": "",
        })
    else:
        tpl_context.update({
            "architecture_section": render_architecture(arch_data),
            "conventions_section": render_conventions(conv_data),
            "categorized_learnings": render_categorized_learnings(high_impact),
            "patterns_section": render_patterns(patterns),
            "adherence_section": render_adherence(high_impact),
        })

    trw_section = render_template(template, tpl_context)

    # PRD-CORE-061-FR04: Enforce max_auto_lines gate before writing
    auto_gen_lines = trw_section.count("\n")
    if auto_gen_lines > config.max_auto_lines:
        msg = (
            f"Auto-gen section is {auto_gen_lines} lines, "
            f"exceeds max_auto_lines={config.max_auto_lines}. "
            f"Refactor rendering before syncing."
        )
        raise StateError(msg)

    if scope == "sub" and target_dir:
        target = Path(target_dir).resolve() / "CLAUDE.md"
        max_lines = config.sub_claude_md_max_lines
    else:
        target = project_root / "CLAUDE.md"
        max_lines = config.claude_md_max_lines

    write_claude, write_agents = _determine_write_targets(client, config, project_root, scope)

    total_lines = 0
    if write_claude:
        total_lines = merge_trw_section(target, trw_section, max_lines)

    update_analytics_sync(trw_dir)

    for learning in high_impact:
        lid = learning.get("id", "")
        if isinstance(lid, str) and lid:
            mark_promoted(trw_dir, lid)

    agents_md_synced, agents_md_path = _sync_agents_md_if_needed(
        write_agents,
        config,
        project_root,
        trw_dir,
        recall_fn=recall_learnings,
    )

    # FR04 (PRD-FIX-053): Store hash after successful render (root scope only).
    if scope != "sub":
        rendered_hash = _compute_sync_hash(high_impact, patterns)
        _write_stored_hash(trw_dir, rendered_hash)

    # PRD-CORE-084 FR08: Generate REVIEW.md after CLAUDE.md sync completes.
    # Fail-open — REVIEW.md failure must not block CLAUDE.md sync.
    review_md_result: dict[str, object]
    try:
        review_md_result = generate_review_md(trw_dir, repo_root=project_root)
    except Exception:  # justified: fail-open — REVIEW.md failure must not block CLAUDE.md sync
        logger.warning("review_md_generation_failed", exc_info=True)
        review_md_result = {"status": "failed", "error": "generation failed"}

    logger.info(
        "claude_md_sync_ok",
        scope=scope,
        path=str(target),
        promoted_count=len(high_impact),
        total_learnings=len(high_impact) + len(patterns),
        client=client,
        write_claude=write_claude,
        write_agents=write_agents,
    )
    logger.debug(
        "claude_md_sync_detail",
        high_impact_count=len(high_impact),
        patterns_count=len(patterns),
        total_lines=total_lines,
        agents_md_path=agents_md_path if agents_md_synced else None,
    )
    return {
        "path": str(target),
        "scope": scope,
        "status": "synced",
        "learnings_promoted": len(high_impact),
        "patterns_included": len(patterns),
        "total_lines": total_lines,
        "llm_used": llm_summary is not None,
        "agents_md_synced": agents_md_synced,
        "agents_md_path": agents_md_path,
        "bounded_contexts_synced": 0,
        "review_md": review_md_result,
    }
