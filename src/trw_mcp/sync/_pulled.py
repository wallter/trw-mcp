"""Which rows team or company sync PULLED, as opposed to rows this host wrote. Belongs to :mod:`trw_mcp.sync.pull`.

A pulled row is another author's learning: sync never uploads it (see ``_client_runtime.get_dirty_entries``). The row's own
provenance decides. The ``team-sync-`` id prefix is only a fallback for a row that records NO provenance at all and never
overrides an explicit own source: a learning this host wrote under such an id is still its own and is uploaded.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "BASELINE_KEY",
    "PULLED_SOURCES",
    "content_fingerprint",
    "finish_pulled_merge",
    "is_edited_here",
    "is_pulled",
    "keep_own_provenance",
]

#: Metadata key holding the fingerprint of the content a pulled row had when it arrived: the baseline an edit is judged by.
BASELINE_KEY = "team_sync_content_fp"

PULLED_SOURCES = frozenset({"team_sync", "company_sync"})
_PULLED_ID_PREFIX = "team-sync-"


def _recorded(entry: Any, field: str) -> str:
    return str(getattr(entry, field, "") or "").strip()


def is_pulled(entry: Any) -> bool:
    """Whether *entry* arrived through team or company sync."""
    source = _recorded(entry, "source")
    if source:  # an explicit source is final, pulled or the author's own
        return source in PULLED_SOURCES
    others = {_recorded(entry, field) for field in ("source_identity", "client_profile")} - {""}
    if others:
        return bool(others & PULLED_SOURCES)
    return str(getattr(entry, "id", "")).startswith(_PULLED_ID_PREFIX)


def keep_own_provenance(merged: Any, existing: Any) -> Any:
    """*merged* with the provenance of the local row it was merged into, when that row is the author's own.

    The platform echoes a host's own push back in the feed. Merging that echo must not turn the row into a "pulled" one,
    or its later edits would never be uploaded.
    """
    if is_pulled(existing):
        return merged
    return merged.model_copy(update={f: getattr(existing, f) for f in ("source", "source_identity", "client_profile")})


def content_fingerprint(entry: Any) -> str:
    """A hash of what a person wrote in *entry* (text, detail, tags, importance), blind to counters and metadata.

    ``sync_hash`` cannot play this part: storage recomputes it from the current content on every write, so an edited row
    and an untouched one both have a ``sync_hash`` equal to the hash of their content.
    """
    body = {
        "content": str(getattr(entry, "content", "")),
        "detail": str(getattr(entry, "detail", "") or ""),
        "tags": sorted(str(tag) for tag in (getattr(entry, "tags", None) or [])),
        "importance": round(float(getattr(entry, "importance", 0.0) or 0.0), 6),
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:32]


def is_edited_here(entry: Any) -> bool:
    """Whether a pulled *entry*'s content differs from what it was pulled with. No recorded baseline (a row pulled by an
    earlier release) answers no: such a row is dirty only through the old counter bug, and is acknowledged clean."""
    baseline = (getattr(entry, "metadata", None) or {}).get(BASELINE_KEY)
    return bool(baseline) and baseline != content_fingerprint(entry)


def finish_pulled_merge(resolved: Any, existing: Any, remote_won: bool) -> Any:
    """The merged row as the pull stores it: the author's own provenance kept, or the baseline of what was pulled recorded.

    A merge that kept local content (``remote_won`` false) keeps the baseline of the earlier pull, so the row still counts
    as edited here and its edit is protected from the teammate's next revision.
    """
    if existing is not None and not is_pulled(existing):
        return keep_own_provenance(resolved, existing)
    if remote_won:
        baseline = content_fingerprint(resolved)
    else:
        baseline = (getattr(existing, "metadata", None) or {}).get(BASELINE_KEY) or content_fingerprint(resolved)
    return resolved.model_copy(update={"metadata": {**(resolved.metadata or {}), BASELINE_KEY: baseline}})
