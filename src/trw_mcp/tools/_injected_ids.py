"""The injected-learning-ids dedup file — PRD-CORE-095-FR16.

Belongs to the ``_ceremony_session_start_steps.py`` facade, which re-exports
:func:`_write_session_start_ids` so existing imports and monkeypatch targets keep
resolving. Extracted under PRD-CORE-263 to keep that module under the 350
effective-LOC gate.

The file this writes is the ONLY thing that stops the auto-injection hook from
re-injecting learnings ``trw_session_start`` already surfaced, so it is bounded,
de-duplicated by recency, and rewritten atomically. It opens no SQLite
connection, which is why PRD-CORE-263-FR06 removed the writer-pressure guard
that used to skip it: skipping it paid correctness to save nothing.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path

import structlog

from trw_mcp.tools._ceremony_degradations import record_into

logger = structlog.get_logger(__name__)


# Bound for injected_learning_ids.txt. Each session appends the IDs it
# surfaced; without a cap the file grows without limit across every session of
# a long-lived project, slowing the auto-injection hook's read and wasting
# disk. The most recent IDs are the ones the hook needs (older surfaced
# learnings age out of relevance), so keep a recency-ordered tail.
_MAX_INJECTED_IDS = 500


def _write_session_start_ids(
    trw_dir: Path,
    learnings: list[dict[str, object]],
    results: MutableMapping[str, object] | None = None,
) -> None:
    """Write learning IDs from session_start to the injected-IDs state file.

    PRD-CORE-095 FR16: Prevents the auto-injection hook from re-injecting
    learnings that session_start already surfaced.

    The file is bounded: existing IDs are merged with the new ones, de-duplicated
    preserving recency (last occurrence wins), and truncated to the most recent
    ``_MAX_INJECTED_IDS`` so it cannot grow without limit.

    PRD-CORE-263-FR06: the narrow ``OSError`` handler stays narrow, but records a
    degradation into ``results`` when one is supplied instead of only a debug
    log — a session whose surfaced ids were NOT recorded is one whose next
    prompt re-injects them, which is visible to the agent paying for it and
    should be visible in the payload too.
    """
    ids = [str(e.get("id", "")) for e in learnings if e.get("id")]
    if not ids:
        return
    state_file = trw_dir / "context" / "injected_learning_ids.txt"
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        existing: list[str] = []
        if state_file.exists():
            existing = [line.strip() for line in state_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        # Merge old + new, de-dup preserving recency (newest occurrence wins),
        # then keep only the most-recent tail.
        merged = existing + ids
        seen: set[str] = set()
        deduped_reversed: list[str] = []
        for lid in reversed(merged):
            if lid not in seen:
                seen.add(lid)
                deduped_reversed.append(lid)
        capped = list(reversed(deduped_reversed[:_MAX_INJECTED_IDS]))
        # Atomic rewrite so a crash mid-write can't corrupt the bounded file.
        tmp = state_file.with_suffix(state_file.suffix + ".tmp")
        tmp.write_text("".join(lid + "\n" for lid in capped), encoding="utf-8")
        tmp.replace(state_file)
    except OSError as exc:
        # Narrow by design: only filesystem failures are expected here, and a
        # broader catch would hide a bug in the merge/cap logic above.
        if results is not None:
            record_into(results, "injected_ids_write", exc)
        else:
            logger.warning("injected_ids_write_failed", error=type(exc).__name__, exc_info=True)


__all__ = ["_MAX_INJECTED_IDS", "_write_session_start_ids"]
