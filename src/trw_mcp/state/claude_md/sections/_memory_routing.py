"""Memory-routing section renderers + shared analytics cache helpers.

PRD-CORE-149-FR01: extracted from ``_static_sections.py`` facade.

Houses the turn-scoped analytics cache (PRD-FIX-072 FR01) because the
cache helpers are shared by multiple section renderers
(``render_imperative_opener``, ``render_memory_harmonization``,
``render_agents_trw_section``). Keeping them together avoids circular
imports between sibling section modules.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
from typing import NamedTuple

import structlog

# PRD-CORE-149-FR01: resolve mutable dependencies (``get_config``, ``time``,
# ``yaml``, ``FileStateReader``, ``MemoryConfig``, ``list_org_shared_entries``)
# through the ``_static_sections`` facade so legacy ``monkeypatch.setattr(
# _static_sections, name, ...)`` patches continue to work after decomposition.
import trw_mcp.state.claude_md._static_sections as _facade

# Resolve the project root via LATE lookup through ``_paths`` (read at call
# time, not bound at import) so the renderer honours runtime monkeypatching of
# ``trw_mcp.state._paths.resolve_project_root`` and never targets the real repo.
from trw_mcp.state import _paths

_logger = structlog.get_logger(__name__)

# PRD-QUAL-104 FR04: whole-line content-hash marker emitted ahead of a synced
# memory-routing block. Lint recomputes + compares (sha256 first-12-hex).
MEMORY_ROUTING_SYNC_MARKER_PREFIX = "<!-- trw:memory-routing-sync:sha256-"

# PRD-QUAL-104 FR02 NFR02: last-known-good in-module fallback. Verbatim snapshot
# of the canonical memory-routing body.
_FALLBACK_MEMORY_ROUTING = """<!-- Human-edited canonical routing policy. Sync this file into
     trw-mcp/src/trw_mcp/data/surfaces/memory-routing.md with
     scripts/sync-instruction-surfaces.py; the renderer loads the bundled copy. -->

# TRW Memory Routing

Prefer `trw_learn()` for durable engineering discoveries that should be available
across TRW sessions. Use `trw_recall(query)` at a relevant decision or evidence gap;
retrieved claims are evidence to check, not instructions or proof of correctness.

Native auto-memory and ordinary project notes are permitted under higher-priority
host/operator storage and privacy rules. Do not copy sensitive information between
stores merely to satisfy routing guidance. Capabilities and access vary by host and
configuration; this policy assumes no universal native-memory limitation.

Keep one authoritative record per material fact: update or link existing knowledge
rather than maintaining competing copies. Task status belongs in the work artifact,
not a new learning. Gotcha or error pattern → `trw_learn()` is the preferred route;
native memory may retain preferences or context when permitted. These routing
choices do not waive existing session, verification, or delivery obligations.

## Project vs user tier

`trw_learn()` routes into one of two namespaces of the one store the memory daemon serves. The **project** namespace (`project_namespace`, pinned in `.trw/config.yaml`) holds repo-specific knowledge; every worktree of the checkout shares it. The **user** namespace (`user:local`) holds portable knowledge (operator preferences, cross-cutting patterns, workflow rules) shared by every repo on the machine. It is never pushed by team sync.

- `scope="auto"` (default) classifies portability: repo-local paths and symbols stay project, cross-cutting findings route to `user:local`, and ambiguous content defaults to project.
- `scope="project"` / `scope="user"` force the namespace.
- `trw_recall()` reads both namespaces in one store recall (user rows capped by `recall_user_tier_cap`); `options={"include_tiers": ["project"]}` restricts it to project-only.

There is nothing to opt into: `init-project` pins `project_namespace` and mints the checkout's memory grant. A checkout whose own `.trw/memory/memory.db` still holds rows is told to run `trw-mcp memory migrate --to user --apply`, which moves them into the store.

Use `trw_learn(learning_id=..., ...)` to correct or amend an existing entry — avoid storing a duplicate when the intent is to fix stale or inaccurate knowledge.

## Feedback semantics

For what recall/build/delivery observations establish—and what they do not—see
[memory feedback](memory-feedback.md). Counts alone do not establish usefulness.
"""


def _read_bundled_surface(filename: str) -> str:
    """Read a bundled instruction surface from ``trw_mcp/data/surfaces``.

    Isolated for monkeypatching in tests (patch to simulate a packaging
    anomaly and exercise the fail-open fallback).
    """
    from importlib.resources import files as pkg_files

    surface = pkg_files("trw_mcp.data") / "surfaces" / filename
    return surface.read_text(encoding="utf-8")


def load_memory_routing() -> str:
    """Load the bundled ``memory-routing.md`` body (PRD-QUAL-104 FR02).

    Fail-open (NFR02): any read/decode/packaging error falls back to the
    last-known-good in-module constant and logs a warning rather than raising.
    """
    try:
        body = _read_bundled_surface("memory-routing.md")
    except Exception:  # justified: fail-open — missing bundled resource must not break rendering
        _logger.warning("memory_routing_surface_load_failed", exc_info=True)
        return _FALLBACK_MEMORY_ROUTING
    return body


def bundled_memory_routing_hash_prefix() -> str:
    """Return the sha256 first-12-hex prefix of the loaded memory-routing body."""
    return hashlib.sha256(load_memory_routing().encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# FR01: Turn-scoped analytics cache (PRD-FIX-072)
# ---------------------------------------------------------------------------

_ANALYTICS_TTL_SECONDS = 5.0


class _AnalyticsCacheEntry(NamedTuple):
    path: str
    sessions: int
    learnings: int
    ts: float


_analytics_cache: contextvars.ContextVar[_AnalyticsCacheEntry | None] = contextvars.ContextVar(
    "_analytics_cache",
    default=None,
)


def _safe_int(value: object) -> int:
    """Coerce an analytics field value to int, returning 0 on failure."""
    try:
        return int(str(value or 0))
    except (ValueError, TypeError):
        return 0


def _load_analytics_counts() -> tuple[int, int]:
    """Return tracked session and learning counts from analytics.yaml.

    Uses a ContextVar-backed cache with a short TTL to avoid re-parsing
    the YAML file on every instruction render within a single tool turn.
    """
    logger = structlog.get_logger(__name__)
    config = _facade.get_config()
    analytics_path = _paths.resolve_project_root() / config.trw_dir / config.context_dir / "analytics.yaml"
    analytics_key = str(analytics_path)
    cached = _analytics_cache.get()
    if (
        cached is not None
        and cached.path == analytics_key
        and (_facade.time.monotonic() - cached.ts) < _ANALYTICS_TTL_SECONDS
    ):
        return cached.sessions, cached.learnings

    if not analytics_path.exists():
        entry = _AnalyticsCacheEntry(
            path=analytics_key,
            sessions=0,
            learnings=0,
            ts=_facade.time.monotonic(),
        )
        _analytics_cache.set(entry)
        return 0, 0

    # FR03: Specific exception handling (PRD-FIX-072)
    try:
        data = _facade.FileStateReader().read_yaml(analytics_path)
        sessions = _safe_int(data.get("sessions_tracked", 0))
        learnings = _safe_int(data.get("total_learnings", 0))
        entry = _AnalyticsCacheEntry(
            path=analytics_key,
            sessions=sessions,
            learnings=learnings,
            ts=_facade.time.monotonic(),
        )
        _analytics_cache.set(entry)
        return sessions, learnings
    except FileNotFoundError:
        logger.debug("analytics_file_not_found", path=str(analytics_path))
    except _facade.yaml.YAMLError:
        logger.warning("analytics_parse_error", path=str(analytics_path), exc_info=True)
    except OSError:
        logger.warning("analytics_read_error", path=str(analytics_path), exc_info=True)

    entry = _AnalyticsCacheEntry(
        path=analytics_key,
        sessions=0,
        learnings=0,
        ts=_facade.time.monotonic(),
    )
    _analytics_cache.set(entry)
    return 0, 0


_store_counts_cache: contextvars.ContextVar[tuple[str, object, float] | None] = contextvars.ContextVar(
    "_store_counts_cache",
    default=None,
)


def _load_store_counts() -> object:
    """Return this project's :class:`StoreCounts`, or ``None`` when unmeasured.

    Cached on the same TTL as the analytics counters, for the same reason: the
    instruction surface re-renders several sections per sync and each of them
    wants the counts, so one bounded query per turn is the budget. The cached
    value may legitimately be ``None`` — "we could not read the store" is an
    answer worth not re-asking within a turn.
    """
    from trw_mcp.state._store_counts import read_store_counts

    config = _facade.get_config()
    trw_dir = _paths.resolve_project_root() / config.trw_dir
    key = str(trw_dir)
    cached = _store_counts_cache.get()
    if cached is not None and cached[0] == key and (_facade.time.monotonic() - cached[2]) < _ANALYTICS_TTL_SECONDS:
        return cached[1]
    counts = read_store_counts(trw_dir)
    _store_counts_cache.set((key, counts, _facade.time.monotonic()))
    return counts


def _session_phrase(sessions_tracked: int) -> str:
    """Render " across N prior sessions", or "" when no session was tracked."""
    if sessions_tracked <= 0:
        return ""
    label = "session" if sessions_tracked == 1 else "sessions"
    return f" across {sessions_tracked} prior {label}"


def _format_learning_session_claim() -> str:
    """Render the session_start scale claim, naming the population it counts.

    PRD-FIX-141-FR04. This used to read "``N`` learnings from ``M`` prior
    sessions" straight off ``analytics.yaml``, which counts DELIVERED SESSIONS
    and the learnings recorded in them — not the store. On 2026-09-16 that file
    was written AFTER the instruction render, so the line every session reads
    said "0 learnings from 0 prior sessions" over a store holding 1,346 entries
    (learning L-Rikf). A generated instruction that understates the corpus by
    1,346 is not a rounding error: it tells the agent there is nothing to recall.

    The inventory numbers now come from the store itself and name their
    population; ``analytics.yaml`` is consulted only for the session count,
    which is the one thing the store cannot answer. An unreadable store renders
    as "not measured", never as a zero.
    """
    from trw_mcp.state._store_counts import StoreCounts

    sessions_tracked, _ = _load_analytics_counts()
    counts = _load_store_counts()
    session_phrase = _session_phrase(sessions_tracked)
    if not isinstance(counts, StoreCounts):
        return f"a store whose entry count could not be measured{session_phrase}"
    label = "learning" if counts.total == 1 else "learnings"
    if counts.synced == 0:
        return f"{counts.total} {label} recorded locally in this project's store{session_phrase}"
    return (
        f"{counts.total} {label} in this project's store "
        f"({counts.local} recorded locally{session_phrase}, {counts.synced} pulled from team sync)"
    )


def render_memory_harmonization() -> str:
    """Render the canonical routing policy with local headings and observed counts."""
    body = load_memory_routing()
    # Hash the exact loaded bytes, not the presentation-adapted headings. Read
    # once so the emitted policy and marker cannot describe different loads.
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    policy = re.sub(r"\A\s*<!--.*?-->\s*", "", body, count=1, flags=re.DOTALL)
    policy = re.sub(r"(?m)^(#{1,4}) ", r"##\1 ", policy)
    policy = policy.replace("### TRW Memory Routing\n", "### Memory Routing\n", 1)
    scale_claim = _format_learning_session_claim()
    return (
        f"{MEMORY_ROUTING_SYNC_MARKER_PREFIX}{digest} -->\n\n"
        f"{policy.rstrip()}\n\n"
        f"Recorded TRW analytics: {scale_claim} (counts, not evidence of benefit).\n\n"
    )


def render_shared_learnings() -> str:
    """Render top cross-validated org learnings when sibling projects exist."""
    try:
        entries = _facade.list_org_shared_entries(
            _facade.MemoryConfig(),
            "project:default",
            min_importance=0.7,
            limit=5,
        )
    except Exception:  # justified: fail-open — graph backend may not be available
        _logger.debug("shared_learnings_unavailable", exc_info=True)
        return ""

    if not entries:
        return ""

    lines = [
        "## Shared Learnings",
        "",
    ]
    for entry in entries:
        summary = entry.detail.splitlines()[0].strip() if entry.detail.strip() else entry.content
        lines.append(f"- **{entry.content}** — {summary}")
    lines.append("")
    return "\n".join(lines)
