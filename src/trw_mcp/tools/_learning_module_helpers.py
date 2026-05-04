"""Module-level helpers for ``tools.learning`` (PRD-DIST-243 Phase 1, cycle 22).

Extracted from ``tools/learning.py`` to keep that module under the
350-effective-LOC operator threshold. Holds:

- ``_SOLUTION_PATTERNS`` + ``_is_solution_summary`` (PRD-FIX-052 FR05)
- ``_build_call_ctx`` (PRD-CORE-141 FR03 — TRWCallContext builder)
- ``_read_injected_ids`` + ``_annotate_injected_learnings`` (PRD-CORE-095 FR15)
- ``_create_llm_client`` (LLMClient factory; routes usage log via config)

The module-level ``__getattr__`` shim in ``tools/learning.py`` STAYS
there — Python's module-level dunder lookup doesn't resolve through
re-imports, so the shim is load-bearing only at its original site.
"""

from __future__ import annotations

import re as _re
from pathlib import Path

from fastmcp import Context

from trw_mcp.clients.llm import LLMClient
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import TRWCallContext, resolve_pin_key, resolve_trw_dir

__all__ = [
    "_SOLUTION_PATTERNS",
    "_annotate_injected_learnings",
    "_build_call_ctx",
    "_create_llm_client",
    "_is_solution_summary",
    "_read_injected_ids",
]


# PRD-FIX-052-FR05: Solution-indicator patterns for auto-'pattern' tag suggestion.
_SOLUTION_PATTERNS = _re.compile(
    r"(?:use .+ instead|prefer |always |best practice|"
    r"recommended approach|the fix is|pattern:)",
    flags=_re.IGNORECASE | _re.VERBOSE,
)


def _is_solution_summary(summary: str) -> bool:
    """Return True if the summary matches solution-indicator patterns (FR05)."""
    return bool(_SOLUTION_PATTERNS.search(summary))


def _build_call_ctx(ctx: Context | None) -> TRWCallContext:
    """PRD-CORE-141 FR03: build a TRWCallContext from an incoming FastMCP ctx.

    Used by ctx-aware learning tools so they don't scan-hijack another
    session's on-disk active run via telemetry or PRD knowledge-ID prefetch.
    """
    pin_key = resolve_pin_key(ctx=ctx, explicit=None)
    try:
        raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
    except Exception:
        raw_session = None
    return TRWCallContext(
        session_id=pin_key,
        client_hint=None,
        explicit=False,
        fastmcp_session=raw_session if isinstance(raw_session, str) else None,
    )


def _read_injected_ids(trw_dir: Path) -> set[str]:
    """Read learning IDs already injected by the user-prompt-submit hook.

    PRD-CORE-095 FR15: Returns a set of IDs from
    ``.trw/context/injected_learning_ids.txt`` (one per line).
    Returns empty set if file missing or unreadable.
    """
    state_file = trw_dir / "context" / "injected_learning_ids.txt"
    try:
        return {
            line.strip()
            for line in state_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError:
        return set()


def _annotate_injected_learnings(
    result: dict[str, object],
    trw_dir: Path,
) -> None:
    """Annotate and deprioritize already-injected learnings in recall results.

    PRD-CORE-095 FR15: Reads injected IDs from state file and moves
    already-injected learnings to the end of the list with an annotation.
    Fresh results fill the primary slots.
    """
    injected_ids = _read_injected_ids(trw_dir)
    if not injected_ids:
        return
    learnings = result.get("learnings")
    if not learnings or not isinstance(learnings, list):
        return
    fresh: list[dict[str, object]] = []
    already: list[dict[str, object]] = []
    for entry in learnings:
        lid = str(entry.get("id", ""))
        if lid in injected_ids:
            entry["already_in_context"] = True
            already.append(entry)
        else:
            fresh.append(entry)
    result["learnings"] = fresh + already


def _create_llm_client() -> LLMClient:
    """Create an LLM client using current config."""
    config = get_config()
    llm_usage_path: Path | None = None
    if config.llm_usage_log_enabled:
        trw_dir = resolve_trw_dir()
        llm_usage_path = trw_dir / config.logs_dir / config.llm_usage_log_file
    return LLMClient(
        model=config.llm_default_model, usage_log_path=llm_usage_path
    )
