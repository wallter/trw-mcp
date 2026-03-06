"""Tool usage profiling and hot set computation — PRD-CORE-067-FR03/FR04.

Tracks which tools are invoked per session and computes the "hot set"
of most frequently used tools for pre-loading at server startup.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

DEFAULT_HOT_SET: list[str] = ["trw_session_start", "trw_checkpoint", "trw_deliver"]

TOOL_GROUPS: dict[str, list[str]] = {
    "ceremony": ["trw_session_start", "trw_checkpoint", "trw_deliver"],
    "learning": ["trw_learn", "trw_recall", "trw_learn_update"],
    "orchestration": ["trw_init", "trw_status", "trw_run_report"],
    "requirements": ["trw_prd_create", "trw_prd_validate"],
    "build": [
        "trw_build_check",
        "trw_analytics_report",
        "trw_usage_report",
        "trw_review",
        "trw_knowledge_sync",
    ],
}


def compute_hot_set(
    trw_dir: Path,
    *,
    max_entries: int = 20,
    hot_size: int = 5,
    min_sessions: int = 3,
) -> list[str]:
    """Compute top-N tools from usage profile.

    Reads the last ``max_entries`` lines from tool-usage-profile.jsonl,
    counts tool frequency, and returns the top ``hot_size`` tools.

    Falls back to DEFAULT_HOT_SET when the profile has fewer than
    ``min_sessions`` entries.

    Args:
        trw_dir: Path to the .trw directory.
        max_entries: Number of recent profile entries to consider.
        hot_size: Number of tools to include in the hot set.
        min_sessions: Minimum profile entries required to use profile data.

    Returns:
        List of tool names in the hot set.
    """
    profile_path = trw_dir / "context" / "tool-usage-profile.jsonl"
    if not profile_path.exists():
        return list(DEFAULT_HOT_SET)

    try:
        lines = profile_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        logger.debug("usage_profiler_read_failed", path=str(profile_path))
        return list(DEFAULT_HOT_SET)

    if len(lines) < min_sessions:
        return list(DEFAULT_HOT_SET)

    # Take the last max_entries lines
    recent = lines[-max_entries:]
    counter: Counter[str] = Counter()
    for line in recent:
        try:
            entry = json.loads(line)
            tools_used = entry.get("tools_used", [])
            if isinstance(tools_used, list):
                for tool_name in tools_used:
                    if isinstance(tool_name, str):
                        counter[tool_name] += 1
        except (json.JSONDecodeError, TypeError):
            continue

    if not counter:
        return list(DEFAULT_HOT_SET)

    return [name for name, _ in counter.most_common(hot_size)]


def record_session_usage(
    trw_dir: Path,
    session_id: str,
    tools_used: list[str],
) -> None:
    """Append session usage to tool-usage-profile.jsonl.

    Fire-and-forget: failures are logged at DEBUG and never propagate.

    Args:
        trw_dir: Path to the .trw directory.
        session_id: Unique session identifier.
        tools_used: List of tool names invoked during the session.
    """
    try:
        context_dir = trw_dir / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        profile_path = context_dir / "tool-usage-profile.jsonl"

        entry = {
            "session_id": session_id,
            "tools_used": sorted(set(tools_used)),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with open(profile_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except OSError:
        logger.debug("usage_profiler_write_failed", session_id=session_id)
