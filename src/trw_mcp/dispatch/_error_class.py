"""The remedy class and last error message of a dispatch that produced no usable answer (PRD-CORE-304-FR01).

``classify_silence`` (``_normalize``) names WHY a run is unusable; this names WHAT a caller does about it,
so a capacity blip, a credential race, a login problem and a content stop stop looking alike.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from typing import Literal

import structlog

from trw_mcp.dispatch._credentials import run_guarded
from trw_mcp.dispatch._normalize import _AUTH_MARKERS, _status_text
from trw_mcp.dispatch._types import DispatchAttempt, DispatchRequest, DispatchResult

__all__ = ["ErrorClass", "error_class", "last_error", "run_dispatch", "with_one_retry"]

logger = structlog.get_logger(__name__)

ErrorClass = Literal[
    "provider_capacity", "quota", "credential_refresh_conflict", "auth", "content_stop", "timeout", "unknown"
]
_ERROR_CLASS: dict[str, ErrorClass] = {
    "timed_out": "timeout",
    "provider_capacity": "provider_capacity",
    "quota_exhausted": "quota",
    "credential_refresh_conflict": "credential_refresh_conflict",
}
_LAST_ERROR_CHARS = 500
#: A value the client labels as a credential, in any of ``key: v``, ``key=v``, ``"key": "v"`` (sol r3), and any
#: opaque run long enough to be a token: pattern redaction alone misses an unprefixed one.
_LABELLED_SECRET = re.compile(
    r"""((?:token|secret|password|passwd|credential|api[_-]?key|auth)\w*["']?\s*[:=]\s*["']?)[^\s"',;}]+""",
    re.IGNORECASE,
)
_OPAQUE_RUN = re.compile(r"[A-Za-z0-9_\-./+=]{24,}")


def error_class(silence_reason: str | None, structured: dict[str, object] | None, raw_stderr: str) -> ErrorClass | None:
    """The remedy class of a run that produced no usable answer (PRD-CORE-304-FR01), or ``None``."""
    if silence_reason is None:
        return None
    if silence_reason == "auth_or_content_stop":
        haystack = f"{_status_text(structured)}\n{raw_stderr.lower()}"
        return "auth" if any(marker in haystack for marker in _AUTH_MARKERS) else "content_stop"
    return _ERROR_CLASS.get(silence_reason, "unknown")


def last_error(structured: dict[str, object] | None, raw_stderr: str) -> str | None:
    """The client's own last error message: its structured error event, else its last stderr line."""
    from trw_mcp.telemetry.anonymizer import redact_secrets  # the telemetry package imports config, which imports this

    detail = (structured or {}).get("error")
    if isinstance(detail, dict):
        detail = detail.get("message") or detail
    message = str(detail) if detail else next((line for line in reversed(raw_stderr.splitlines()) if line.strip()), "")
    message = _LABELLED_SECRET.sub(r"\1[redacted]", redact_secrets(message.strip()))
    return _OPAQUE_RUN.sub("[redacted]", message)[:_LAST_ERROR_CHARS] or None  # a token echoed in an error never leaves


#: Classes the SAME client is worth one more try for (PRD-CORE-304-FR03): the provider had no room, or a
#: concurrent process spent the rotating refresh token (the retry refreshes from the token it rotated to).
#: A quota refusal is not one of them: another client answers instead (``_fallback``).
_RETRYABLE: frozenset[ErrorClass] = frozenset({"provider_capacity", "credential_refresh_conflict"})
RETRY_DELAY_SECONDS = 5.0
#: A retry needs this much of the dispatch's budget left after the delay, or the first answer stands.
_MIN_RETRY_SECONDS = 30.0


def with_one_retry(run: Callable[[], DispatchResult], deadline: float) -> DispatchResult:
    """*run*, and once more if it failed with a retryable class and *deadline* leaves room; the first try is kept."""
    first = run()
    if first.error_class not in _RETRYABLE or deadline - time.monotonic() - RETRY_DELAY_SECONDS < _MIN_RETRY_SECONDS:
        return first
    logger.warning("dispatch_retry", client=first.client, error_class=first.error_class)  # never the client's text
    time.sleep(RETRY_DELAY_SECONDS)
    second = run()
    earlier = DispatchAttempt(client=first.client, reason=first.silence_reason)
    return second.model_copy(update={"attempts": [earlier, *second.attempts]})


def run_dispatch(
    req: DispatchRequest,
    run_once: Callable[[DispatchRequest], DispatchResult],
    refuse: Callable[[str], DispatchResult],
) -> DispatchResult:
    """*req* within ONE budget, ``req.timeout_s``, for the credential-lock wait and every attempt.

    Each attempt runs with the time left, so a retry never stretches a dispatch past what a background
    job's watchdog allows (it kills a job at 1.5x its timeout; sol review of PRD-CORE-304).
    """
    deadline = time.monotonic() + req.timeout_s

    def attempt() -> DispatchResult:
        return run_once(req.model_copy(update={"timeout_s": max(1, math.ceil(deadline - time.monotonic()))}))

    return run_guarded(req.client, req.timeout_s, lambda: with_one_retry(attempt, deadline), refuse)
