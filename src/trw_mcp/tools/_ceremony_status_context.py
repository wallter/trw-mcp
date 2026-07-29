"""Reactive nudge-context construction for live ceremony-status injection.

Every live MCP tool that decorates its response via ``append_ceremony_status``
must tell the nudge layer WHICH tool produced the response and what that tool
observed (build outcome, review verdict, P0 count). That ``NudgeContext`` is
the sole activation key for three otherwise-unreachable behaviours:

* the ``context`` nudge pool — ``_ceremony_status_pool.resolve_pool_content``
  returns ``None`` for ``pool == "context"`` when ``context`` is falsy, so every
  draw is wasted into ``record_pool_ignore`` -> cooldown;
* the build-failure / P0 pool bypass in ``_nudge_rules._select_nudge_pool``,
  which force-selects the ``context`` pool regardless of weights;
* the per-tool dispatch in ``_nudge_template_select._context_reactive_message``.

Commit ``093b1fd49e`` (2026-04-10) renamed the injector and dropped the
``NudgeContext`` construction from every call site, so ``context`` silently
defaulted to ``None`` everywhere for months. This module is the ONE place that
builds it, so a future rename cannot re-drop it from eight scattered sites —
and ``tests/test_nudge_context_wiring.py`` asserts each production call site
still routes through here.

Ownership boundary: ``NudgeContext`` belongs to the ceremony-status layer
(``_ceremony_status.py`` / ``_ceremony_status_pool.py`` already import it).
Tool modules stay free of nudge-model imports and pass a tool label instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trw_mcp.state._ceremony_progress_state import NudgeContext, ToolName

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "KNOWN_TOOL_NAMES",
    "append_ceremony_status_for_tool",
    "build_nudge_context",
    "resolve_tool_name",
]

# Canonical ``ToolName`` values, used by callers and by the wiring regression
# test to prove a resolved label is a real tool name and not a typo.
KNOWN_TOOL_NAMES: frozenset[str] = frozenset(
    value for name, value in vars(ToolName).items() if not name.startswith("_") and isinstance(value, str)
)


def resolve_tool_name(tool_name: str) -> str:
    """Map a caller-supplied label onto its canonical ``ToolName`` value.

    Accepts both the constant NAME (``"INIT"``, as the orchestration facade
    passes) and the constant VALUE (``"init"``). Unknown labels pass through
    unchanged: downstream dispatch returns ``None`` for them and falls back to
    static messages, so an unrecognised tool degrades rather than raising.
    """
    resolved = getattr(ToolName, tool_name.upper(), None)
    return resolved if isinstance(resolved, str) else tool_name


def build_nudge_context(
    tool_name: str,
    *,
    tool_success: bool = True,
    build_passed: bool | None = None,
    review_verdict: str | None = None,
    review_p0_count: int = 0,
    is_subagent: bool = False,
) -> NudgeContext:
    """Build the reactive nudge context describing a completed tool call."""
    return NudgeContext(
        tool_name=resolve_tool_name(tool_name),
        tool_success=tool_success,
        build_passed=build_passed,
        review_verdict=review_verdict,
        review_p0_count=review_p0_count,
        is_subagent=is_subagent,
    )


def append_ceremony_status_for_tool(
    response: dict[str, object],
    trw_dir: Path | None = None,
    *,
    tool_name: str,
    tool_success: bool = True,
    build_passed: bool | None = None,
    review_verdict: str | None = None,
    review_p0_count: int = 0,
    is_subagent: bool = False,
) -> dict[str, object]:
    """Attach ceremony status to ``response`` with reactive context wired in.

    Thin, deliberate seam over :func:`append_ceremony_status`: same fail-open
    contract, but the ``context`` argument is never omitted.
    """
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    return append_ceremony_status(
        response,
        trw_dir,
        build_nudge_context(
            tool_name,
            tool_success=tool_success,
            build_passed=build_passed,
            review_verdict=review_verdict,
            review_p0_count=review_p0_count,
            is_subagent=is_subagent,
        ),
    )
