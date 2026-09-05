"""Offline marshalling for ``trw-mcp local feedback`` and ``trw-mcp local recall``.

PRD-CORE-247-FR03. Sibling of :mod:`trw_mcp.services.orchestration_service`,
which already carries the ``local init/checkpoint/status/learn/deliver``
marshalling and sits within a few lines of the module-size gate; these two go
here rather than growing it.

**These functions marshal and nothing else.** No redaction, validation, ranking,
or persistence logic is reimplemented: ``local feedback`` calls
:func:`trw_mcp.tools.submit_feedback.submit_feedback` — the same top-level
callable the MCP tool wrapper calls, which already redacts before it validates —
and ``local recall`` calls :func:`trw_mcp.tools._recall_impl.execute_recall`.
That is PRD-FIX-073-FR02's shared-service rule applied to the two surfaces it
did not reach.

Like ``orchestration_service``, this module has NO dependency on FastMCP, so it
is safe to import from a CLI entry point that runs with no server at all — which
is the entire point of the offline path.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state._constants import LOCAL_CLI_SOURCE_IDENTITY

_logger = structlog.get_logger(__name__)

#: Default number of learnings ``local recall`` prints. Deliberately smaller than
#: the MCP tool's configured default: the offline surface writes to a terminal an
#: operator is reading by eye, not into a model's context window.
LOCAL_RECALL_DEFAULT_MAX_RESULTS: int = 5


def submit_local_feedback(
    *,
    category: str,
    subject: str,
    message: str,
    contact_email: str | None = None,
) -> dict[str, object]:
    """Submit feedback through the shared ``submit_feedback`` callable.

    Returns the tool's own ``SubmitFeedbackResult`` shape as a plain dict, so the
    CLI branch formats one contract rather than branching on a model type.
    ``submit_feedback`` never raises — an unconfigured backend comes back as
    ``success: false`` with an ``error`` string — so this function adds no second
    error contract of its own.
    """
    from trw_mcp.tools.submit_feedback import submit_feedback

    result = submit_feedback(
        category=category,
        subject=subject,
        message=message,
        contact_email=contact_email,
        metadata={"source_identity": LOCAL_CLI_SOURCE_IDENTITY},
    )
    payload: dict[str, object] = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    _logger.info("local_feedback_submitted", category=category, success=bool(payload.get("success")))
    return payload


def run_local_recall(
    query: str,
    *,
    trw_dir: Path | None = None,
    tags: list[str] | None = None,
    max_results: int | None = None,
) -> dict[str, object]:
    """Recall learnings through the shared ``execute_recall`` implementation.

    Raises:
        StateError / OSError: propagated from the store when no memory backend
            can be opened, so the CLI can print an actionable message and exit
            non-zero rather than printing an empty, falsely-successful result.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    resolved = trw_dir or (Path.cwd() / ".trw")
    result = execute_recall(
        query,
        resolved,
        get_config(),
        tags=tags,
        max_results=max_results if max_results is not None else LOCAL_RECALL_DEFAULT_MAX_RESULTS,
    )
    _logger.info("local_recall_completed", query=query[:60], results=len(result.get("learnings", []) or []))
    return dict(result)


def format_local_recall(result: dict[str, object]) -> list[str]:
    """Render a recall result as terminal lines.

    Formatting only — the ranking that decided the order happened in
    ``execute_recall``. Kept beside the marshaller so the CLI dispatch branch
    stays a two-line call and the shape is testable without a subprocess.
    """
    learnings = result.get("learnings") or []
    if not isinstance(learnings, list) or not learnings:
        return ["No matching learnings."]
    lines: list[str] = []
    for entry in learnings:
        if not isinstance(entry, dict):
            continue
        lines.append(f"[{entry.get('id', 'unknown')}] {entry.get('summary', '')}")
    return lines or ["No matching learnings."]


__all__ = [
    "LOCAL_RECALL_DEFAULT_MAX_RESULTS",
    "format_local_recall",
    "run_local_recall",
    "submit_local_feedback",
]
