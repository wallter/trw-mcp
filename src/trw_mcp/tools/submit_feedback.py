"""trw_submit_feedback — thin MCP client for the backend submission portal.

Implements the client side of PRD-CORE-182. Wraps ``POST /v1/submissions`` so
TRW framework users can submit memos (bug reports, installation issues,
feedback, feature requests, questions) directly from their IDE without
re-implementing the HTTP contract.

The tool:
- Reads the backend URL + API key from :class:`TRWConfig`.
- Auto-populates client metadata (``trw_mcp_version``, ``python_version``,
  ``os_platform``) so the maintainer can triage submissions without guessing
  the environment.
- Returns a stable ``{success, submission_id?, error?}`` shape.
- Never raises on transport / server errors — failures surface in the
  ``error`` field so the calling agent can react gracefully.
"""

from __future__ import annotations

import os
import platform
import re
import sys
from typing import Any

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger(__name__)


# Accepted categories. Kept in sync with backend FR06 enum. Validated client-side
# so we fail fast before paying for an HTTP round-trip.
_ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {
        "bugfix",
        "installation",
        "feedback",
        "feature_request",
        "question",
        "other",
    }
)


# ---------------------------------------------------------------------------
# PRD-INFRA-132 FR04a — PII redaction
# ---------------------------------------------------------------------------
# Single chokepoint NFR01 mandates for secret hygiene. Pure function (no I/O,
# idempotent) so it is trivially unit-testable. Patterns compiled at import
# so redaction stays O(n) over the message body per call.
_LICENSE_KEY_RE = re.compile(r"trw_lic_\S+")
_API_KEY_RE = re.compile(r"(?:sk_(?:live|test)_\S+|pk_(?:live|test)_\S+|AKIA[0-9A-Z]{16})")
_ENV_RE = re.compile(
    r"\b(?:PASSWORD|SECRET|TOKEN|API[_-]?KEY|AWS_(?:ACCESS|SECRET)_KEY)\s*=\s*\S+",
    re.IGNORECASE,
)


def _redact_pii(text: str) -> str:
    """Strip license keys, API keys, env-var values, and $HOME paths.

    PRD-INFRA-132 FR04a — applied to the submission ``message`` before the
    network call so secrets never leave the box in clear form. ``HOME`` is
    resolved at call time (not import time) so tests can override it via
    ``monkeypatch.setenv``.
    """
    if not text:
        return text
    redacted = _LICENSE_KEY_RE.sub("<REDACTED:license_key>", text)
    redacted = _API_KEY_RE.sub("<REDACTED:api_key>", redacted)
    redacted = _ENV_RE.sub("<REDACTED:env>", redacted)
    home = os.path.expanduser("~")
    if home and home != "~":
        home_norm = home.rstrip("/")
        if home_norm:
            redacted = redacted.replace(home_norm, "$HOME")
    return redacted


# Validation caps mirrored from PRD-CORE-182 FR01/FR06. If the server tightens
# them this file is the single place we have to update on the client side.
MAX_SUBJECT_LEN = 200
MAX_MESSAGE_LEN = 10_000
MIN_MESSAGE_LEN = 10
MAX_METADATA_KEYS = 16
MAX_METADATA_KEY_LEN = 64
MAX_METADATA_VALUE_LEN = 200

# HTTP timeout for the submission round-trip. Submissions are tiny; this is
# generous enough to ride out a cold-start without leaving the agent hanging.
_HTTP_TIMEOUT_SECONDS = 10.0


class SubmitFeedbackResult(BaseModel):
    """Stable result shape returned by ``trw_submit_feedback``."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    success: bool
    submission_id: str = ""
    error: str = ""
    status_code: int = 0
    metadata_attached: dict[str, str] = Field(default_factory=dict)


def _trw_mcp_version() -> str:
    """Best-effort version lookup; tolerates missing package metadata."""
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover — stdlib always present on 3.8+
        return ""
    try:
        return version("trw-mcp")
    except PackageNotFoundError:
        return ""


def _build_auto_metadata() -> dict[str, str]:
    """Construct the auto-attached environment metadata dict."""
    return {
        "trw_mcp_version": _trw_mcp_version(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "os_platform": platform.platform(terse=True),
    }


def _merge_metadata(
    user_metadata: dict[str, str] | None,
    auto_metadata: dict[str, str],
) -> dict[str, str]:
    """Merge user-supplied metadata over auto-attached metadata.

    User wins on key collision, but only if the user value is non-empty.
    Empty values from either side are dropped so the email stays readable.
    """
    merged: dict[str, str] = {k: v for k, v in auto_metadata.items() if v}
    if user_metadata:
        merged.update({k: v for k, v in user_metadata.items() if v})
    return merged


def _validate(
    *,
    category: str,
    subject: str,
    message: str,
    metadata: dict[str, str] | None,
    contact_email: str | None,
) -> str:
    """Client-side validation. Returns an error string, or "" if OK."""
    if category not in _ALLOWED_CATEGORIES:
        return f"invalid category {category!r}; expected one of {sorted(_ALLOWED_CATEGORIES)}"
    if not isinstance(subject, str) or not subject.strip():
        return "subject must be a non-empty string"
    if len(subject) > MAX_SUBJECT_LEN:
        return f"subject must be at most {MAX_SUBJECT_LEN} chars"
    if "\r" in subject or "\n" in subject:
        return "subject must not contain newline characters"
    if not isinstance(message, str) or not message.strip():
        return "message must be a non-empty string"
    if len(message) < MIN_MESSAGE_LEN:
        return f"message must be at least {MIN_MESSAGE_LEN} chars"
    if len(message) > MAX_MESSAGE_LEN:
        return f"message must be at most {MAX_MESSAGE_LEN} chars"
    if metadata is not None:
        if not isinstance(metadata, dict):
            return "metadata must be a dict[str,str]"
        if len(metadata) > MAX_METADATA_KEYS:
            return f"metadata may contain at most {MAX_METADATA_KEYS} keys"
        for k, v in metadata.items():
            if not isinstance(k, str) or not isinstance(v, str):
                return "metadata keys and values must be strings"
            if len(k) > MAX_METADATA_KEY_LEN:
                return f"metadata key exceeds {MAX_METADATA_KEY_LEN} chars"
            if len(v) > MAX_METADATA_VALUE_LEN:
                return f"metadata value for key {k!r} exceeds {MAX_METADATA_VALUE_LEN} chars"
            if "\r" in k or "\n" in k or "\r" in v or "\n" in v:
                return "metadata must not contain newline characters"
    if contact_email is not None and (not isinstance(contact_email, str) or "@" not in contact_email):
        return "contact_email must be a valid email address"
    return ""


def submit_feedback_via_http(
    *,
    backend_url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> SubmitFeedbackResult:
    """POST the validated payload and translate the response.

    Isolated from the MCP tool wrapper so it is easy to unit-test by patching
    ``httpx.Client.post``.
    """
    import httpx

    url = f"{backend_url.rstrip('/')}/v1/submissions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("submit_feedback_transport_error", error=str(exc))
        return SubmitFeedbackResult(
            success=False,
            error=f"transport error: {exc}",
            status_code=0,
            metadata_attached=payload.get("metadata", {}),
        )

    status = response.status_code
    if status == 200:
        try:
            data = response.json()
            sub_id = str(data.get("submission_id", ""))
        except (ValueError, TypeError):
            sub_id = ""
        return SubmitFeedbackResult(
            success=True,
            submission_id=sub_id,
            status_code=status,
            metadata_attached=payload.get("metadata", {}),
        )

    # Surface the upstream error message when present.
    err_msg = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail")
            if isinstance(detail, dict):
                err_msg = str(detail.get("detail") or detail.get("error") or "")
            elif isinstance(detail, str):
                err_msg = detail
    except (ValueError, TypeError):
        err_msg = response.text[:200] if response.text else ""

    logger.info("submit_feedback_non_2xx", status=status, error=err_msg)
    return SubmitFeedbackResult(
        success=False,
        error=err_msg or f"HTTP {status}",
        status_code=status,
        metadata_attached=payload.get("metadata", {}),
    )


def submit_feedback(
    *,
    category: str,
    subject: str,
    message: str,
    contact_email: str | None = None,
    metadata: dict[str, str] | None = None,
) -> SubmitFeedbackResult:
    """Top-level callable used by both the MCP tool wrapper and tests.

    Reads the backend URL + API key from :class:`TRWConfig`. If neither is
    configured, returns ``success=False`` with a clear error so the operator
    knows to set ``TRW_BACKEND_URL`` / ``TRW_BACKEND_API_KEY``.

    PRD-INFRA-132 FR04a: the ``message`` body is run through ``_redact_pii``
    BEFORE validation so length / content checks see the redacted form and
    the network call never carries secrets in clear text.
    """
    message = _redact_pii(message)
    error = _validate(
        category=category,
        subject=subject,
        message=message,
        metadata=metadata,
        contact_email=contact_email,
    )
    if error:
        logger.info("submit_feedback_validation_failed", error=error)
        return SubmitFeedbackResult(success=False, error=error, status_code=0)

    # Lazy import so importing this module does not pull in heavy config.
    from trw_mcp.models.config import get_config

    cfg = get_config()
    backend_url = (cfg.resolved_backend_url or "").strip()
    api_key = (cfg.resolved_backend_api_key or "").strip()

    if not backend_url or not api_key:
        return SubmitFeedbackResult(
            success=False,
            error=(
                "backend not configured — set TRW_BACKEND_URL and TRW_BACKEND_API_KEY (or run install-trw to provision)"
            ),
            status_code=0,
        )

    final_metadata = _merge_metadata(metadata, _build_auto_metadata())
    payload: dict[str, Any] = {
        "category": category,
        "subject": subject.strip(),
        "message": message,
        "metadata": final_metadata,
    }
    if contact_email is not None:
        payload["contact_email"] = contact_email

    return submit_feedback_via_http(
        backend_url=backend_url,
        api_key=api_key,
        payload=payload,
    )


def register_submit_feedback_tools(server: FastMCP) -> None:
    """Register the ``trw_submit_feedback`` MCP tool on the given server."""

    @server.tool()
    def trw_submit_feedback(
        category: str,
        subject: str,
        message: str,
        contact_email: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Submit a memo to the TRW maintainer (PRD-CORE-182).

        Use when:
        - You found a bug, installation problem, or rough edge worth flagging.
        - You want to send a feature request or piece of feedback that
          deserves a real reply instead of disappearing into a personal log.
        - You want the maintainer to see exactly which trw-mcp / Python / OS
          you are on without retyping it — environment metadata is attached
          automatically.

        Input:
        - category: one of ``bugfix``, ``installation``, ``feedback``,
          ``feature_request``, ``question``, ``other``.
        - subject: short headline (1-200 chars, no newlines).
        - message: full memo body (10-10000 chars).
        - contact_email: optional reply-to address; defaults to no reply-to.
        - metadata: optional extra key/value pairs (16 keys max, 200 char
          values max). Merged on top of the auto-attached environment dict.

        Output: dict with ``success``, ``submission_id`` (when 200),
        ``error`` (when non-200), ``status_code`` (HTTP status or 0 on
        validation/transport error), and ``metadata_attached`` (the dict
        actually sent so you can audit it locally).

        Never raises — transport and validation failures are reported in the
        ``error`` field.
        """
        return submit_feedback(
            category=category,
            subject=subject,
            message=message,
            contact_email=contact_email,
            metadata=metadata,
        ).model_dump()


__all__ = [
    "MAX_MESSAGE_LEN",
    "MAX_METADATA_KEYS",
    "MAX_METADATA_KEY_LEN",
    "MAX_METADATA_VALUE_LEN",
    "MAX_SUBJECT_LEN",
    "MIN_MESSAGE_LEN",
    "SubmitFeedbackResult",
    "register_submit_feedback_tools",
    "submit_feedback",
    "submit_feedback_via_http",
]
