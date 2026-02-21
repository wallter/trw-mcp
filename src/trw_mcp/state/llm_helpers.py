"""LLM helper functions for the TRW self-learning layer.

Extracted from tools/learning.py (PRD-FIX-010) to separate LLM integration
from tool orchestration logic.  Uses the ``anthropic`` SDK (optional [ai]
dependency).
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.clients.llm import LLMClient

# Named caps for list truncation (not user-tunable)
LLM_BATCH_CAP = 20
LLM_EVENT_CAP = 30


def _parse_json_lines(text: str) -> list[dict[str, object]]:
    """Parse newline-delimited JSON objects from LLM response text.

    Skips blank lines and lines that do not start with ``{``.
    Silently drops lines that fail to parse.
    """
    results: list[dict[str, object]] = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            parsed: dict[str, object] = json.loads(line)
            results.append(parsed)
        except ValueError:
            continue
    return results


def llm_assess_learnings(
    entries: list[tuple[Path, dict[str, object]]],
    llm: LLMClient,
    batch_cap: int = LLM_BATCH_CAP,
) -> list[dict[str, object]]:
    """Use LLM to assess whether active learnings are still relevant.

    Asks Haiku to classify each learning as ACTIVE, RESOLVED, or OBSOLETE.

    Args:
        entries: List of (file_path, entry_data) tuples.
        llm: LLM client instance.
        batch_cap: Maximum entries to include in a single batch.

    Returns:
        List of candidate dicts with id, summary, suggested_status, and reason.
    """
    summaries: list[str] = [
        f"- ID: {data.get('id', '')} | Created: {data.get('created', '')}"
        f" | Summary: {data.get('summary', '')} | Detail: {data.get('detail', '')}"
        for _path, data in entries[:batch_cap]
    ]

    if not summaries:
        return []

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
        return []

    # Build id->summary lookup for matching parsed results to entries
    summary_by_id: dict[object, str] = {
        d.get("id"): str(d.get("summary", ""))
        for _, d in entries
    }

    candidates: list[dict[str, object]] = []
    for parsed in _parse_json_lines(response):
        status_raw = str(parsed.get("status", "ACTIVE")).upper()
        if status_raw not in ("RESOLVED", "OBSOLETE"):
            continue
        entry_id = parsed.get("id", "")
        candidates.append({
            "id": entry_id,
            "summary": summary_by_id.get(entry_id, ""),
            "suggested_status": status_raw.lower(),
            "reason": parsed.get("reason", "LLM assessment"),
        })

    return candidates


def llm_extract_learnings(
    events: list[dict[str, object]],
    llm: LLMClient,
    event_cap: int = LLM_EVENT_CAP,
) -> list[dict[str, object]] | None:
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
    event_summaries: list[str] = [
        f"- {evt.get('event', 'unknown')}: {str(evt.get('data', ''))[:100]}"
        for evt in events[:event_cap]
    ]

    if not event_summaries:
        return None

    prompt = (
        "Analyze these events from a software development session and extract key learnings.\n"
        "For each learning, respond with a JSON line:\n"
        '{"summary": "one-line", "detail": "explanation", "tags": ["tag1"], "impact": 0.5}\n'
        "Extract 1-5 learnings. Focus on actionable insights.\n"
        "Do NOT extract learnings about event frequencies, repeated operations, or success counts"
        " — these are tracked separately as analytics data.\n\n"
        + "\n".join(event_summaries)
    )

    response = llm.ask_sync(
        prompt,
        system="You are a software engineering learning extractor. Be concise and actionable.",
    )

    if response is None:
        return None

    learnings: list[dict[str, object]] = []
    for parsed in _parse_json_lines(response):
        if "summary" not in parsed:
            continue
        learnings.append({
            "summary": str(parsed["summary"]),
            "detail": str(parsed.get("detail", "")),
            "tags": parsed.get("tags", ["auto-discovered", "llm"]),
            # Stored as str for YAML serialization consistency
            "impact": str(parsed.get("impact", "0.6")),
        })

    return learnings if learnings else None


def llm_summarize_learnings(
    learnings: list[dict[str, object]],
    patterns: list[dict[str, object]],
    llm: LLMClient,
    learning_cap: int,
    pattern_cap: int,
) -> str | None:
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

    items: list[str] = [
        f"- Learning: {entry.get('summary', '')} | Detail: {entry.get('detail', '')}"
        for entry in learnings[:learning_cap]
    ] + [
        f"- Pattern: {pat.get('name', '')} | {pat.get('description', '')}"
        for pat in patterns[:pattern_cap]
    ]

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
