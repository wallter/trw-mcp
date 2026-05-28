"""PRD-INFRA-132 FR04 + FR04a — User-feedback MCP tool with PII redaction.

This module implements the FR04 (``trw_submit_feedback``) and FR04a
(``_redact_pii``) functional requirements of PRD-INFRA-132.

Design notes:
- ``_redact_pii`` is a pure function (no I/O) so it is trivially unit-testable
  and is the single chokepoint NFR01 mandates for secret hygiene.
- ``trw_submit_feedback`` NEVER raises (NFR05) — transport / HTTP errors are
  reported via the structured ``FeedbackSubmissionResult.error`` field.
- All structlog calls AVOID the reserved ``event=`` kwarg.
"""

from __future__ import annotations

import os
import re
from typing import Literal

import httpx
import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# FR04a — PII redaction
# ---------------------------------------------------------------------------

# Compiled at module import so redaction is O(n) over the body text per call
# regardless of how many redactor invocations happen in a session.
_LICENSE_KEY_RE = re.compile(r"trw_lic_\S+")
_API_KEY_RE = re.compile(r"(?:sk_(?:live|test)_\S+|pk_(?:live|test)_\S+|AKIA[0-9A-Z]{16})")
# Case-insensitive ``KEY=value`` shape for known sensitive env-var names. The
# ``\b`` guards against accidentally swallowing the tail of unrelated tokens
# (e.g. ``MY_PASSWORD_HINT=foo`` is intentionally matched; ``XPASSWORD=y`` is
# not — KEY must start on a word boundary).
_ENV_RE = re.compile(
    r"\b(?:PASSWORD|SECRET|TOKEN|API[_-]?KEY|AWS_(?:ACCESS|SECRET)_KEY)\s*=\s*\S+",
    re.IGNORECASE,
)


def _redact_pii(text: str) -> str:
    """Strip license keys, API keys, env-var values, and $HOME paths.

    Pure function — no I/O, idempotent (double-application yields the same
    output). The four redaction classes are documented in PRD-INFRA-132 FR04a.

    The ``$HOME`` substitution is applied last so it does not race the other
    patterns; ``os.path.expanduser('~')`` is resolved at call time (NOT at
    import time) so the function honors per-process ``HOME`` overrides set in
    tests via ``monkeypatch.setenv``.
    """
    if not text:
        return text

    redacted = _LICENSE_KEY_RE.sub("<REDACTED:license_key>", text)
    redacted = _API_KEY_RE.sub("<REDACTED:api_key>", redacted)
    redacted = _ENV_RE.sub("<REDACTED:env>", redacted)

    home = os.path.expanduser("~")
    # Skip if HOME is empty / unresolved / literal "~" — replacing on those
    # would either be a no-op or accidentally damage unrelated paths.
    if home and home != "~":
        # Normalize trailing slash so ``/home/op`` and ``/home/op/`` both match
        # uniformly; we substitute the prefix only and keep the remainder.
        home_norm = home.rstrip("/")
        if home_norm:
            redacted = redacted.replace(home_norm, "$HOME")

    return redacted


# ---------------------------------------------------------------------------
# FR04 — trw_submit_feedback MCP tool
# ---------------------------------------------------------------------------


_CategoryLiteral = Literal[
    "bug", "install_issue", "feedback", "feature_request", "question"
]

# Backend contract — kept here so a server-side change is a one-line client
# update without chasing magic strings through the file.
_MAX_SUBJECT_LEN = 200
_MAX_BODY_LEN = 50_000
_HTTP_TIMEOUT_SECONDS = 10.0
_RECENT_LEARNING_COUNT = 5
_RECENT_LEARNING_MIN_IMPACT = 0.7


class FeedbackSubmissionResult(BaseModel):
    """Structured result returned by :func:`trw_submit_feedback`.

    ``status`` is ``"accepted"`` on a 200, ``"error"`` otherwise. ``error``
    carries a short machine-readable tag (``http_503: ...``, ``network: ...``)
    that lets the calling agent decide whether to retry or surface to the user.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    submission_id: str
    status: str
    error: str | None = Field(default=None)


def _truncate(text: str, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, preserving the head."""
    if len(text) <= limit:
        return text
    return text[:limit]


def _fetch_recent_learnings_block() -> str:
    """Render up to N recent high-impact learnings as a Markdown block.

    Returns the empty string on any import or runtime failure so a recall
    outage never blocks a feedback submission (NFR05 spirit).
    """
    try:
        from trw_mcp.tools._recall_impl import execute_recall
    except ImportError:
        logger.debug("feedback_recall_import_failed")
        return ""

    try:
        # ``execute_recall`` honors the same shape as the trw_recall MCP tool;
        # we ask for high-impact entries only so the operator's feedback body
        # is loaded with signal, not noise.
        result = execute_recall(
            query="",
            tags=None,
            min_impact=_RECENT_LEARNING_MIN_IMPACT,
            status="active",
            shard_id=None,
            max_results=_RECENT_LEARNING_COUNT,
            compact=True,
            ultra_compact=False,
            topic=None,
            call_ctx=None,
        )
    except Exception:  # justified: best-effort attachment; never fail submission
        logger.debug("feedback_recall_failed", exc_info=True)
        return ""

    learnings_obj = result.get("learnings") if isinstance(result, dict) else None
    if not isinstance(learnings_obj, list) or not learnings_obj:
        return ""

    lines: list[str] = ["", "## Recent Learnings", ""]
    for entry in learnings_obj[:_RECENT_LEARNING_COUNT]:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id", ""))
        summary = str(entry.get("summary", ""))
        if summary:
            lines.append(f"- [{entry_id}] {summary}" if entry_id else f"- {summary}")

    if len(lines) <= 3:
        return ""
    return "\n".join(lines)


def _fetch_last_error_block() -> str:
    """Render the most recent unhandled exception (if available).

    Reads from the security anomaly stats sink. Returns "" if the sink is
    missing or empty so the absence of a recent error gracefully omits the
    section rather than emitting an empty heading.
    """
    try:
        from trw_mcp.security import anomaly_stats
    except ImportError:
        return ""

    last_error_fn = getattr(anomaly_stats, "get_last_unhandled_error", None)
    if not callable(last_error_fn):
        return ""

    try:
        last = last_error_fn()
    except Exception:  # justified: best-effort attachment; never fail submission
        logger.debug("feedback_last_error_lookup_failed", exc_info=True)
        return ""

    if not last:
        return ""

    text = str(last).strip()
    if not text:
        return ""

    return "\n".join(["", "## Last Error", "", "```", text, "```"])


def _load_backend_creds() -> tuple[str, str]:
    """Return ``(backend_url, api_key)`` from TRWConfig, or ``("", "")``.

    Backend URL + key live on the resolved sync-target properties of
    :class:`TRWConfig`. We tolerate either being empty and let the caller
    short-circuit with a structured error so the operator gets a clear
    "auth-missing" remediation hint.
    """
    try:
        from trw_mcp.models.config import get_config
    except ImportError:
        return "", ""

    try:
        cfg = get_config()
    except Exception:  # justified: config load must never crash the tool
        logger.debug("feedback_config_load_failed", exc_info=True)
        return "", ""

    backend_url = (getattr(cfg, "resolved_backend_url", "") or "").strip()
    api_key = (getattr(cfg, "resolved_backend_api_key", "") or "").strip()
    return backend_url, api_key


def _truncate_error_message(text: str, limit: int = 200) -> str:
    """Cap upstream error bodies before they land in our result.error field."""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def trw_submit_feedback_impl(
    category: _CategoryLiteral,
    summary: str,
    detail: str,
    include_recent_learnings: bool = True,
    include_last_error: bool = True,
) -> FeedbackSubmissionResult:
    """Implementation behind the ``trw_submit_feedback`` MCP tool.

    Split out from the registration closure so tests can call it directly
    without going through FastMCP's tool wrapper.
    """
    logger.info(
        "feedback_submission_attempted",
        category=category,
        summary_length=len(summary),
        detail_length=len(detail),
        include_recent_learnings=include_recent_learnings,
        include_last_error=include_last_error,
    )

    backend_url, api_key = _load_backend_creds()
    if not backend_url or not api_key:
        return FeedbackSubmissionResult(
            submission_id="",
            status="error",
            error=(
                "auth_missing: set TRW_BACKEND_URL and TRW_BACKEND_API_KEY "
                "(or re-run install-trw)"
            ),
        )

    subject = _truncate(summary.strip(), _MAX_SUBJECT_LEN)
    body_parts: list[str] = [detail]

    if include_recent_learnings:
        block = _fetch_recent_learnings_block()
        if block:
            body_parts.append(block)

    if include_last_error:
        block = _fetch_last_error_block()
        if block:
            body_parts.append(block)

    body = "\n".join(body_parts)
    body = _truncate(body, _MAX_BODY_LEN)
    # Redact AFTER assembly so attached learnings + error trace are also
    # filtered — they may incidentally contain $HOME paths or env values.
    body = _redact_pii(body)

    url = f"{backend_url.rstrip('/')}/v1/submissions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, str] = {
        "category": category,
        "subject": subject,
        "body": body,
    }

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        logger.info("feedback_submission_network_failure", reason="timeout")
        return FeedbackSubmissionResult(
            submission_id="",
            status="error",
            error=f"network: {type(exc).__name__}",
        )
    except httpx.RequestError as exc:
        logger.info("feedback_submission_network_failure", reason="request_error")
        return FeedbackSubmissionResult(
            submission_id="",
            status="error",
            error=f"network: {type(exc).__name__}",
        )

    status_code = response.status_code
    if status_code == 200:
        submission_id = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                raw_id = data.get("submission_id", "")
                submission_id = str(raw_id) if raw_id is not None else ""
        except (ValueError, TypeError):
            submission_id = ""

        logger.info(
            "feedback_submission_completed",
            status_code=status_code,
            has_submission_id=bool(submission_id),
        )
        return FeedbackSubmissionResult(
            submission_id=submission_id,
            status="accepted",
        )

    # Non-200. Extract a short error message; never echo the full body.
    err_msg = ""
    try:
        err_body = response.json()
        if isinstance(err_body, dict):
            detail_field = err_body.get("detail")
            if isinstance(detail_field, str):
                err_msg = detail_field
            elif isinstance(detail_field, dict):
                inner = detail_field.get("detail") or detail_field.get("error")
                if isinstance(inner, str):
                    err_msg = inner
    except (ValueError, TypeError):
        err_msg = response.text or ""

    logger.info(
        "feedback_submission_completed",
        status_code=status_code,
        outcome="error",
    )
    return FeedbackSubmissionResult(
        submission_id="",
        status="error",
        error=f"http_{status_code}: {_truncate_error_message(err_msg)}",
    )


def register_feedback_tools(server: FastMCP) -> None:
    """Register PRD-INFRA-132 FR04 tool on the given FastMCP server.

    NOTE: PRD-CORE-182 shipped a prior ``trw_submit_feedback`` tool in
    :mod:`trw_mcp.tools.submit_feedback`. This module's tool name is
    suffixed (``trw_submit_feedback_v2``) so both can coexist while the
    operator-facing migration to the PRD-INFRA-132 contract lands.
    """

    @server.tool()
    def trw_submit_feedback_v2(
        category: _CategoryLiteral,
        summary: str,
        detail: str,
        include_recent_learnings: bool = True,
        include_last_error: bool = True,
    ) -> FeedbackSubmissionResult:
        """Submit feedback to the TRW maintainer (PRD-INFRA-132 FR04).

        Wraps ``POST /v1/submissions`` with PII redaction (license keys, API
        keys, ``$HOME`` paths, sensitive env-var KEY=value pairs) applied to
        the assembled body BEFORE the network call (FR04a).

        Args:
            category: one of ``bug``, ``install_issue``, ``feedback``,
                ``feature_request``, ``question``.
            summary: 1-200 chars; becomes the email subject.
            detail: full memo body; up to 50_000 chars.
            include_recent_learnings: attach up to 5 high-impact learnings.
            include_last_error: attach the most recent unhandled tool trace.

        Returns:
            FeedbackSubmissionResult with ``submission_id`` on success or
            structured ``error`` on HTTP / network failure. Never raises.
        """
        return trw_submit_feedback_impl(
            category=category,
            summary=summary,
            detail=detail,
            include_recent_learnings=include_recent_learnings,
            include_last_error=include_last_error,
        )


__all__ = [
    "FeedbackSubmissionResult",
    "_redact_pii",
    "register_feedback_tools",
    "trw_submit_feedback_impl",
]
