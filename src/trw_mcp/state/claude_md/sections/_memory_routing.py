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
_FALLBACK_MEMORY_ROUTING = """# TRW Memory Routing

**NEVER** store technical knowledge in native auto-memory. Use `trw_learn()` exclusively for engineering insights.

| | `trw_learn()` (Use for Engineering) | Native auto-memory (Use for Personal) |
|---|---|---|
| Search | `trw_recall(query)` — semantic + keyword | Filename scan only |
| Visibility | All agents, subagents, teammates | Primary session only |
| Lifecycle | Impact-scored, recalled at session start | Static until manually edited |

Gotcha or error pattern → `trw_learn()`. Build trick that saves time → `trw_learn()`. Communication preference → native memory.

Use `trw_learn_update(memory_id, ...)` to correct or amend an existing entry — avoid storing a duplicate when the intent is to fix stale or inaccurate knowledge.
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


def _format_learning_session_claim() -> str:
    """Render a truthful analytics-backed learning/session claim."""
    sessions_tracked, total_learnings = _load_analytics_counts()
    session_label = "session" if sessions_tracked == 1 else "sessions"
    learning_label = "learning" if total_learnings == 1 else "learnings"
    return f"{total_learnings} {learning_label} from {sessions_tracked} prior {session_label}"


def render_memory_harmonization() -> str:
    """Render memory-system routing guidance for Claude Code CLAUDE.md."""
    sessions_tracked, total_learnings = _load_analytics_counts()
    scale_claim = f"{total_learnings} learnings across {sessions_tracked} sessions"
    return (
        "### Memory Routing\n"
        "\n"
        "Default to `trw_learn()` for knowledge. "
        "Use native auto-memory only for personal preferences.\n"
        "\n"
        "| | `trw_learn()` | Native auto-memory |\n"
        "|---|---|---|\n"
        "| Search | `trw_recall(query)` — semantic + keyword | Filename scan only |\n"
        "| Visibility | All sessions and configured helpers | Primary session only |\n"
        "| Lifecycle | Impact-scored, recalled at session start | Static until manually edited |\n"
        f"| Scale | {scale_claim}, auto-pruned by staleness | 200-line index cap |\n"
        "\n"
        "Gotcha or error pattern → `trw_learn()`. "
        "User’s preferred commit style → native memory. "
        "Build trick that saves time → `trw_learn()`. "
        "Communication preference → native memory.\n"
        "\n"
        "`trw_learn(scope=...)` routes between the project tier (default, in `.trw/`) "
        "and an opt-in machine-local user tier (`~/.trw`, shared across every repo on the box). "
        '`scope="auto"` classifies portability; `"project"`/`"user"` force it. '
        '`trw_recall()` federates both tiers; `include_tiers=["project"]` restricts to project-only. '
        "To correct or amend an existing entry, use `trw_learn_update(memory_id, ...)` "
        "rather than storing a duplicate.\n"
        "\n"
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
