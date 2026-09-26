"""``trw_status(feedback=...)`` — PRD-CORE-300-FR11 (S9).

Thin adapter over :func:`trw_mcp.tools.submit_feedback.submit_feedback`, the
surviving helper behind the deleted standalone feedback MCP tool. Kept out
of ``tools/orchestration.py`` to keep that module's own edit minimal — parallel
slices touch it too.
"""

from __future__ import annotations

from typing import Any

from trw_mcp.tools.submit_feedback import submit_feedback


def status_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Post ``payload`` exactly as the deleted standalone feedback tool did.

    A network failure returns ``submit_feedback``'s ``success=False`` shape —
    never raises, never reports success on a transport error.
    """
    return submit_feedback(
        category=str(payload.get("category", "")),
        subject=str(payload.get("subject", "")),
        message=str(payload.get("message", "")),
        contact_email=payload.get("contact_email"),
        metadata=payload.get("metadata"),
    ).model_dump()


__all__ = ["status_feedback"]
