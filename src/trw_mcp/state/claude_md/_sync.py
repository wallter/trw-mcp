"""CLAUDE.md sync orchestration — coordinates promotion, rendering, and merge.

AGENTS.md sync logic is in ``_agents_md.py``.
REVIEW.md constants and shared helpers are in ``_review_md.py``.
``generate_review_md`` remains here because tests patch ``_sync.recall_learnings``
and ``_sync.tempfile`` at the module level.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts._ceremony import (
    ClaudeMdSyncResultDict,
    InstructionPointerSkipDict,
    ReviewMdResultDict,
)

# --- AGENTS.md functions (extracted to _agents_md.py) ---
from trw_mcp.state.claude_md._agents_md import (
    _determine_write_target_decision as _determine_write_target_decision,
)
from trw_mcp.state.claude_md._agents_md import (
    _determine_write_targets as _determine_write_targets,
)
from trw_mcp.state.claude_md._agents_md import (
    _inject_learnings_to_agents as _inject_learnings_to_agents,
)
from trw_mcp.state.claude_md._agents_md import (
    _sync_agents_md_if_needed as _sync_agents_md_if_needed,
)
from trw_mcp.state.claude_md._agents_md import (
    _sync_instruction_targets as _sync_instruction_targets,
)
from trw_mcp.state.claude_md._promotion import (
    collect_context_data as collect_context_data,
)
from trw_mcp.state.claude_md._promotion import (
    collect_patterns as collect_patterns,
)
from trw_mcp.state.claude_md._promotion import (
    collect_promotable_learnings as collect_promotable_learnings,
)
from trw_mcp.state.claude_md._review_md import (
    _REVIEW_MAX_LEARNINGS as _REVIEW_MAX_LEARNINGS,
)
from trw_mcp.state.claude_md._review_md import (
    _REVIEW_MIN_IMPACT as _REVIEW_MIN_IMPACT,
)
from trw_mcp.state.claude_md._review_md import (
    _REVIEW_TAGS as _REVIEW_TAGS,
)
from trw_mcp.state.claude_md._review_md import (
    _REVIEW_TEMPLATE as _REVIEW_TEMPLATE,
)

# --- REVIEW.md constants and helpers (extracted to _review_md.py) ---
from trw_mcp.state.claude_md._review_md import (
    _sanitize_summary as _sanitize_summary,
)
from trw_mcp.state.claude_md._review_md import (
    recall_learnings as recall_learnings,
)

# --- sync-cache hashing (extracted to _sync_hash.py) ---
# Re-exported so ``_profile_dispatcher`` and tests keep importing them from
# ``_sync`` and so any ``_sync.*`` monkeypatch of a hash symbol still resolves.
from trw_mcp.state.claude_md._sync_hash import (
    _compute_sync_hash as _compute_sync_hash,
)
from trw_mcp.state.claude_md._sync_hash import (
    _hash_file_path as _hash_file_path,
)
from trw_mcp.state.claude_md._sync_hash import (
    _read_stored_hash as _read_stored_hash,
)
from trw_mcp.state.claude_md._sync_hash import (
    _write_stored_hash as _write_stored_hash,
)
from trw_mcp.state.claude_md._sync_hash import (
    invalidate_claude_md_hash as invalidate_claude_md_hash,
)
from trw_mcp.state.persistence import FileStateReader

if TYPE_CHECKING:
    from trw_mcp.clients.llm import LLMClient

logger = structlog.get_logger(__name__)


def _review_md_failed_result(error: str) -> ReviewMdResultDict:
    """Build a typed failed REVIEW.md result."""
    return {
        "status": "failed",
        "path": None,
        "rules_count": 0,
        "error": error,
    }


def _build_sync_result(
    *,
    path: str,
    scope: str,
    status: Literal["synced", "unchanged"],
    total_lines: int,
    agents_md_synced: bool,
    agents_md_path: str | None,
    instruction_file_synced: bool,
    instruction_file_path: str | None,
    instruction_file_paths: list[str],
    review_md: ReviewMdResultDict,
    hash_value: str | None = None,
    carrier_mode: str | None = None,
    pointer_skips: list[InstructionPointerSkipDict] | None = None,
    external_path: str | None = None,
    capability_parity_drift: list[str] | None = None,
) -> ClaudeMdSyncResultDict:
    """Construct the stable sync result shape used by the tool and tests."""
    result: ClaudeMdSyncResultDict = {
        "path": path,
        "scope": scope,
        "status": status,
        "learnings_promoted": 0,
        "patterns_included": 0,
        "total_lines": total_lines,
        "llm_used": False,
        "agents_md_synced": agents_md_synced,
        "agents_md_path": agents_md_path,
        "instruction_file_synced": instruction_file_synced,
        "instruction_file_path": instruction_file_path,
        "instruction_file_paths": instruction_file_paths,
        "bounded_contexts_synced": 0,
        "review_md": review_md,
    }
    if hash_value is not None:
        result["hash"] = hash_value
    # PRD-CORE-203 FR07: carrier detectability fields (render path only).
    if carrier_mode is not None:
        result["carrier_mode"] = carrier_mode
    if pointer_skips is not None:
        result["pointer_skips"] = pointer_skips
    if external_path is not None:
        result["external_path"] = external_path
    # PRD-CORE-218-FR06: present (possibly empty) whenever AGENTS.md is written.
    if capability_parity_drift is not None:
        result["capability_parity_drift"] = capability_parity_drift
    return result


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
) -> ReviewMdResultDict:
    """Generate REVIEW.md at repo root with auto-injected learning rules.

    Full regeneration on every call. Atomic write via temp+rename.
    Fail-open: never blocks CLAUDE.md sync or delivery.

    Returns dict with keys: path, rules_count, status.
    """
    if repo_root is None:
        repo_root = _get_repo_root()
    if repo_root is None:
        logger.warning("review_md_no_repo_root")
        return _review_md_failed_result("could not determine repo root")

    target_path = repo_root / "REVIEW.md"

    # PRD-FIX-085 FR05: use named factory.
    from trw_mcp.state.recall_factories import recall_for_review_tags

    all_learnings = recall_for_review_tags(
        trw_dir,
        tags=_REVIEW_TAGS,
        min_impact=_REVIEW_MIN_IMPACT,
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


# PRD-CORE-149-FR11: ``execute_claude_md_sync`` moved to
# ``_profile_dispatcher.py`` so the per-profile routing logic lives next to
# the dispatch helper. Re-exported here to preserve the legacy import path
# used by ``tools/learning.py`` and assorted tests.
from trw_mcp.state.claude_md._profile_dispatcher import (  # noqa: E402 — late import avoids circular dep with _profile_dispatcher
    dispatch_for_profile as _dispatch_for_profile,
)


def execute_claude_md_sync(
    scope: str,
    target_dir: str | None,
    config: TRWConfig,
    reader: FileStateReader,
    llm: LLMClient,
    client: str = "auto",
) -> ClaudeMdSyncResultDict:
    """Thin facade over ``dispatch_for_profile`` — see that function for docs."""
    return _dispatch_for_profile(
        scope=scope,
        target_dir=target_dir,
        config=config,
        reader=reader,
        llm=llm,
        client=client,
    )
