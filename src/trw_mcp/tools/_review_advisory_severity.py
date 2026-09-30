"""Advisory review-finding severity (XC-01): one batched ``trw_assess`` screen per review, logged only.

Belongs to the ``_review_helpers`` facade and is re-exported there. A reviewer self-assigns each
finding's severity and ``_compute_verdict`` derives pass/warn/block from the worst one. This asks
the calibrated judge the same question about every finding in one batched call and logs its answer
beside the reported label, so the disagreement rate can be measured before anything consumes it.
The verdict never reads it, it is written to no artifact or response, and it runs only when
``trw_assess`` is surfaced (``assess_enabled`` / ``TRW_JEV_ENABLED``), on a daemon thread so a
review never waits on the judge.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping, Sequence
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Per-request timeout for the judge. It bounds each HTTP request, not the whole screen: a large
#: review is sent in several chunks. The screen runs on a daemon thread, so it never holds the review
#: or process exit either way.
_TIMEOUT_SECONDS = 20.0

#: One choice question, asked of every finding in one batched call (the item rides in the question).
SEVERITY_QUESTION: dict[str, dict[str, Any]] = {
    "severity": {
        "type": "choice",
        "instructions": "How severe is this code-review finding for the change under review?",
        "criteria": {
            "critical": "Blocks the change: data loss, a security hole, a crash, or a broken contract on a default path.",
            "warning": "Should be fixed but need not block: a real defect with limited reach, or a risky pattern.",
            "info": "A note, nit or suggestion: nothing is wrong on any reachable path.",
        },
    }
}


def advisory_severity(findings: Sequence[Mapping[str, Any]], *, kit: Any) -> dict[str, dict[str, Any]]:
    """Per finding index: the reported severity beside the judge's choice, probabilities and margin.

    Only the finding's description and category are sent (the toolkit redacts them); the result holds
    labels and numbers, never finding text, so it is safe to log.
    """
    items = {
        str(i): {"description": str(f.get("description", "")), "category": str(f.get("category", ""))}
        for i, f in enumerate(findings)
    }
    batch = kit.batch_items(items, SEVERITY_QUESTION)
    rows: dict[str, dict[str, Any]] = {}
    for key, result in batch.per_item.items():
        answer = result.to_wire().get("severity", {})
        rows[key] = {
            "reported": str(findings[int(key)].get("severity", "")),
            "advisory": answer.get("choice"),
            "probabilities": answer.get("probabilities"),
            "margin": answer.get("margin"),
            "failure": answer.get("failure"),
        }
    return rows


def _screen_and_log(findings: list[dict[str, Any]]) -> None:
    from trw_memory.decisions import toolkit_from_env

    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.telemetry.anonymizer import redact_secrets

    try:
        project_root = resolve_project_root()
        kit = toolkit_from_env(
            dict(os.environ),
            redactor=redact_secrets,
            dotenv_path=project_root / ".env",
            project_root=project_root,
            timeout_s=_TIMEOUT_SECONDS,
        )
        rows = advisory_severity(findings, kit=kit)
    except Exception as exc:  # trw-fail-silent-allow: advisory only; the verdict never reads it, the failure is logged
        logger.info("review_finding_severity_advisory_failed", error_class=type(exc).__name__, findings=len(findings))
        return
    answered = [r for r in rows.values() if r["advisory"] is not None]
    logger.info(
        "review_finding_severity_advisory",
        findings=len(findings),
        answered=len(answered),
        disagreements=sum(1 for r in answered if r["advisory"] != r["reported"]),
        rows=rows,
    )


def log_advisory_severity(findings: Sequence[Mapping[str, Any]], *, background: bool = True) -> threading.Thread | None:
    """Start the advisory screen for *findings* when ``trw_assess`` is surfaced; return its thread, if any.

    A no-op (``None``) with no findings or with the judge off. ``background=False`` runs it inline (tests).
    """
    if not findings:
        return None
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.tools._assess_enablement import assess_surfaced

        if not assess_surfaced(get_config()):
            return None
        snapshot = [dict(f) for f in findings]  # the caller may mutate its list after returning
        if not background:
            _screen_and_log(snapshot)
            return None
        thread = threading.Thread(
            target=_screen_and_log, args=(snapshot,), name="review-advisory-severity", daemon=True
        )
        thread.start()
        return thread
    except Exception as exc:  # trw-fail-silent-allow: an advisory must never abort the review; the failure is logged
        logger.info("review_finding_severity_advisory_failed", error_class=type(exc).__name__, findings=len(findings))
        return None
