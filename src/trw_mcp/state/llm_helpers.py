"""LLM helper functions for the TRW self-learning layer.

Extracted from tools/learning.py (PRD-FIX-010) to separate LLM integration
from tool orchestration logic.  All helpers are ``pragma: no cover`` since
they require the ``claude-agent-sdk`` package (core dependency).
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.clients.llm import LLMClient

# Named caps for list truncation (not user-tunable)
LLM_BATCH_CAP = 20
LLM_EVENT_CAP = 30


def llm_assess_learnings(
    entries: list[tuple[Path, dict[str, object]]],
    llm: LLMClient,
    batch_cap: int = LLM_BATCH_CAP,
) -> list[dict[str, object]]:  # pragma: no cover
    """Use LLM to assess whether active learnings are still relevant.

    Asks Haiku to classify each learning as ACTIVE, RESOLVED, or OBSOLETE.

    Args:
        entries: List of (file_path, entry_data) tuples.
        llm: LLM client instance.
        batch_cap: Maximum entries to include in a single batch.

    Returns:
        List of candidate dicts with id, summary, suggested_status, and reason.
    """
    import json as _json

    candidates: list[dict[str, object]] = []

    # Build batch prompt for efficiency
    summaries: list[str] = []
    for _path, data in entries[:batch_cap]:
        lid = str(data.get("id", ""))
        summary = str(data.get("summary", ""))
        detail = str(data.get("detail", ""))
        created = str(data.get("created", ""))
        summaries.append(
            f"- ID: {lid} | Created: {created} | Summary: {summary} | Detail: {detail}"
        )

    if not summaries:
        return candidates

    prompt = (
        "Review these learning entries and assess whether each is still relevant.\n"
        "For each, respond with a JSON line: "
        '{"id": "...", "status": "ACTIVE|RESOLVED|OBSOLETE", "reason": "..."}\n'
        "Only include entries you recommend changing (not ACTIVE ones).\n\n"
        + "\n".join(summaries)
    )

    response = llm.ask_sync(
        prompt,
        system="You are a learning lifecycle manager. Assess learning relevance concisely.",
    )

    if response is None:
        return candidates

    # Parse response — each line should be a JSON object
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            parsed = _json.loads(line)
            status_raw = str(parsed.get("status", "ACTIVE")).upper()
            if status_raw in ("RESOLVED", "OBSOLETE"):
                candidates.append({
                    "id": parsed.get("id", ""),
                    "summary": next(
                        (
                            str(d.get("summary", ""))
                            for _, d in entries
                            if d.get("id") == parsed.get("id")
                        ),
                        "",
                    ),
                    "suggested_status": status_raw.lower(),
                    "reason": parsed.get("reason", "LLM assessment"),
                })
        except (ValueError, KeyError):
            continue

    return candidates


def llm_extract_learnings(
    events: list[dict[str, object]],
    llm: LLMClient,
    event_cap: int = LLM_EVENT_CAP,
) -> list[dict[str, object]] | None:  # pragma: no cover
    """Use LLM to extract structured learnings from events.

    Returns None if LLM is unavailable or call fails, signaling
    the caller to fall back to mechanical extraction.

    Args:
        events: List of event dictionaries from events.jsonl.
        llm: LLM client instance.
        event_cap: Maximum events to include in the prompt.

    Returns:
        List of learning dicts with summary, detail, tags, impact, or None.
    """
    import json as _json

    # Build a condensed event summary for the prompt
    event_summaries: list[str] = []
    for evt in events[:event_cap]:
        event_summaries.append(
            f"- {evt.get('event', 'unknown')}: {str(evt.get('data', ''))[:100]}"
        )

    if not event_summaries:
        return None

    prompt = (
        "Analyze these events from a software development session and extract key learnings.\n"
        "For each learning, respond with a JSON line:\n"
        '{"summary": "one-line", "detail": "explanation", "tags": ["tag1"], "impact": 0.5}\n'
        "Extract 1-5 learnings. Focus on actionable insights.\n\n"
        + "\n".join(event_summaries)
    )

    response = llm.ask_sync(
        prompt,
        system="You are a software engineering learning extractor. Be concise and actionable.",
    )

    if response is None:
        return None

    learnings: list[dict[str, object]] = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            parsed = _json.loads(line)
            if "summary" in parsed:
                learnings.append({
                    "summary": str(parsed["summary"]),
                    "detail": str(parsed.get("detail", "")),
                    "tags": parsed.get("tags", ["auto-discovered", "llm"]),
                    "impact": str(parsed.get("impact", "0.6")),
                })
        except (ValueError, KeyError):
            continue

    return learnings if learnings else None


def llm_summarize_learnings(
    learnings: list[dict[str, object]],
    patterns: list[dict[str, object]],
    llm: LLMClient,
    learning_cap: int,
    pattern_cap: int,
) -> str | None:  # pragma: no cover
    """Use LLM to generate a concise categorized summary for CLAUDE.md.

    Returns None if LLM unavailable, signaling fallback to bullet-point listing.

    Args:
        learnings: High-impact active learning entries.
        patterns: Discovered patterns.
        llm: LLM client instance.
        learning_cap: Maximum learnings to include.
        pattern_cap: Maximum patterns to include.

    Returns:
        Formatted markdown string for CLAUDE.md, or None.
    """
    if not learnings and not patterns:
        return None

    items: list[str] = []
    for entry in learnings[:learning_cap]:
        items.append(f"- Learning: {entry.get('summary', '')} | Detail: {entry.get('detail', '')}")
    for pat in patterns[:pattern_cap]:
        items.append(f"- Pattern: {pat.get('name', '')} | {pat.get('description', '')}")

    prompt = (
        "Summarize these learnings and patterns into a concise CLAUDE.md section.\n"
        "Use 3-5 markdown H3 categories with actionable bullet points. Max 30 lines.\n"
        "Do NOT include markdown fences or top-level headers. Start directly with ### categories.\n\n"
        + "\n".join(items)
    )

    return llm.ask_sync(
        prompt,
        system="You are a technical documentation writer. Be concise and organized.",
        model="sonnet",
    )
