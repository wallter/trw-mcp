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

import platform
import sys
from typing import Any

import structlog
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypedDict

# Secret/PII redaction: the single trw-mcp redactor lives in ``telemetry.anonymizer`` (R2-014).
from trw_mcp.telemetry.anonymizer import redact_metadata, redact_secrets

__all__ = ["submit_feedback", "submit_feedback_via_http"]


logger = structlog.get_logger(__name__)


class SubmissionPayload(TypedDict, total=False):
    """Wire shape POSTed to ``/v1/submissions``.

    ``contact_email`` is the only optional key (``total=False`` allows it to be
    omitted); the other four are always present once a payload is built.
    """

    category: str
    subject: str
    message: str
    metadata: dict[str, str]
    contact_email: str


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


# Validation caps mirrored from PRD-CORE-182 FR01/FR06. If the server tightens
# them this file is the single place we have to update on the client side.
MAX_SUBJECT_LEN = 200
MAX_MESSAGE_LEN = 10_000
MIN_MESSAGE_LEN = 10
MAX_METADATA_KEYS = 16
MAX_METADATA_KEY_LEN = 64
MAX_METADATA_VALUE_LEN = 200
# ``contact_email`` was the one user-controlled field with neither a length cap
# nor a newline check, despite NFR01 claiming a single chokepoint over "every
# user-controlled field": a 10k string of secrets containing ``@`` was accepted
# verbatim. RFC 5321 caps a path at 254 octets; anything longer is not an
# address. Redaction is deliberately NOT applied — an address is the field's
# whole purpose and mangling it would defeat the reply path — so the defence is
# a hard bound plus rejection of the shapes that smuggle a payload.
MAX_CONTACT_EMAIL_LEN = 254

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
    if contact_email is not None:
        if not isinstance(contact_email, str) or "@" not in contact_email:
            return "contact_email must be a valid email address"
        if len(contact_email) > MAX_CONTACT_EMAIL_LEN:
            return f"contact_email must be at most {MAX_CONTACT_EMAIL_LEN} chars"
        if any(ch in contact_email for ch in "\r\n") or contact_email.strip() != contact_email:
            return "contact_email must not contain newline or surrounding whitespace"
    return ""


def _extract_submission_id(response: Any) -> str:
    """Best-effort extraction of ``submission_id`` from a 200 response.

    Tolerant of a non-JSON body or a JSON value that is not a dict — both
    collapse to ``""`` rather than raising, preserving the never-raises
    contract on the success path.
    """
    try:
        data = response.json()
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("submission_id", ""))


def _extract_error_message(response: Any) -> str:
    """Pull a human-readable error out of a non-2xx response body.

    Handles FastAPI's ``{"detail": {...}}`` / ``{"detail": "..."}`` shapes and
    falls back to the raw text. Never raises.
    """
    try:
        body = response.json()
    except (ValueError, TypeError):
        try:
            text = response.text
        except Exception:  # body access must not break error reporting
            return ""
        return text[:200] if text else ""
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("detail") or detail.get("error") or "")
        if isinstance(detail, str):
            return detail
    return ""


def submit_feedback_via_http(
    *,
    backend_url: str,
    api_key: str,
    payload: SubmissionPayload,
    timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> SubmitFeedbackResult:
    """POST the validated payload and translate the response.

    Isolated from the MCP tool wrapper so it is easy to unit-test by patching
    ``httpx.Client.post``. Never raises — transport, decode, and HTTP errors
    all surface in the returned :class:`SubmitFeedbackResult`.
    """
    import httpx

    attached: dict[str, str] = payload.get("metadata", {})
    url = f"{backend_url.rstrip('/')}/v1/submissions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("submit_feedback_transport_error", error=str(exc), outcome="failure")
        return SubmitFeedbackResult(
            success=False,
            error=f"transport error: {exc}",
            status_code=0,
            metadata_attached=attached,
        )
    except Exception as exc:
        # Broad catch is intentional — the never-raises contract is the point.
        # Not every httpx failure derives from httpx.HTTPError: e.g.
        # httpx.InvalidURL (malformed backend_url) subclasses Exception
        # directly. The documented "never raises" guarantee must hold for
        # direct callers of this helper, so we catch the residual case too.
        # The error string deliberately reports only the exception TYPE — never
        # str(exc) — because a malformed URL / value could echo back a secret
        # that was interpolated into it.
        logger.warning(
            "submit_feedback_transport_error",
            error_type=type(exc).__name__,
            outcome="failure",
        )
        return SubmitFeedbackResult(
            success=False,
            error=f"transport error: {type(exc).__name__}",
            status_code=0,
            metadata_attached=attached,
        )

    status = response.status_code
    if status == 200:
        sub_id = _extract_submission_id(response)
        logger.info("submit_feedback_dispatched", status=status, outcome="success")
        return SubmitFeedbackResult(
            success=True,
            submission_id=sub_id,
            status_code=status,
            metadata_attached=attached,
        )

    err_msg = _extract_error_message(response)
    logger.info("submit_feedback_non_2xx", status=status, error=err_msg, outcome="failure")
    return SubmitFeedbackResult(
        success=False,
        error=err_msg or f"HTTP {status}",
        status_code=status,
        metadata_attached=attached,
    )


def _submit_feedback_impl(
    *,
    category: str,
    subject: str,
    message: str,
    contact_email: str | None,
    metadata: dict[str, str] | None,
) -> SubmitFeedbackResult:
    """Core submission flow. May raise — wrapped by :func:`submit_feedback`."""
    # NFR01 chokepoint: redact every user-controlled field before it can leave
    # the box — message body, subject headline, AND each user-supplied metadata
    # value. Redaction runs BEFORE validation so the length/content checks see
    # the redacted form and the network call never carries secrets in clear.
    message = redact_secrets(message)
    subject = redact_secrets(subject)
    metadata = redact_metadata(metadata)
    error = _validate(
        category=category,
        subject=subject,
        message=message,
        metadata=metadata,
        contact_email=contact_email,
    )
    if error:
        logger.info("submit_feedback_validation_failed", error=error, outcome="validation_rejected")
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
    payload: SubmissionPayload = {
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

    PRD-INFRA-132 FR04a: the ``message`` body is run through ``redact_secrets``
    BEFORE validation so length / content checks see the redacted form and
    the network call never carries secrets in clear text.

    Never raises (PRD-INFRA-132 NFR02 + PRD-CORE-182-NFR02): any unexpected
    error — config load failure, environment lookup, etc. — is caught and
    surfaced in the ``error`` field so the calling agent always gets the
    stable result shape back. The error string is deliberately generic to
    avoid echoing a redacted secret or config detail back to the caller.
    """
    try:
        return _submit_feedback_impl(
            category=category,
            subject=subject,
            message=message,
            contact_email=contact_email,
            metadata=metadata,
        )
    except Exception as exc:  # never-raises contract is the whole point
        logger.warning(
            "submit_feedback_unexpected_error",
            error_type=type(exc).__name__,
            outcome="failure",
        )
        return SubmitFeedbackResult(
            success=False,
            error=f"unexpected error: {type(exc).__name__}",
            status_code=0,
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
        """Submit a memo to the TRW maintainer; environment metadata is
        auto-attached. Use when reporting a bug, feedback, or feature
        request. Never raises. category is one of {bugfix, installation,
        feedback, feature_request, question, other}.
        """
        return submit_feedback(
            category=category,
            subject=subject,
            message=message,
            contact_email=contact_email,
            metadata=metadata,
        ).model_dump()


__all__ = [
    "MAX_CONTACT_EMAIL_LEN",
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
