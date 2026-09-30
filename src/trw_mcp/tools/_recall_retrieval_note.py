"""The degraded-retrieval advisory for a recall (INC-119 c)."""

from __future__ import annotations

from pathlib import Path


def retrieval_note(trw_dir: Path, query: str) -> str | None:
    """Say so when a query ran without semantic ranking because the store cannot encode text.

    A keyword-only ranking is not a failure, but it is a different ranking than the caller expects, and its order can
    vary between runs; silence made two recalls of one query look inconsistent. A listing (empty or ``*``) ranks nothing,
    and an embedder status that cannot be read adds nothing (an advisory must not fail a recall).
    """
    if query.strip() in ("", "*"):
        return None
    try:
        from trw_mcp.state._store_selection import selected_store

        store, namespace = selected_store(trw_dir)
        status = store.embedder_status(namespace)
    except Exception:  # justified: advisory only, an unreadable status adds no note  # trw-fail-silent-allow: advisory
        return None
    if status.get("available"):
        return None
    fix = f" Fix: {status['fix']}" if status.get("fix") else ""
    return (
        f"Semantic ranking is unavailable ({status.get('reason') or 'embedder unavailable'}): results are ranked by "
        f"keyword match and their order may vary between runs.{fix}"
    )
