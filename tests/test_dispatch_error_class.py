"""PRD-CORE-304-FR01: every failed dispatch names its remedy class and the client's own last error."""

from __future__ import annotations

import json

import pytest

from trw_mcp.dispatch._error_class import error_class, last_error
from trw_mcp.dispatch._normalize import classify_silence, normalize_output
from trw_mcp.dispatch._types import DispatchResult


def _codex_failure(message: str) -> str:
    """A codex ``exec --json`` stream whose turn failed with *message* (the shape rc11 C12 received)."""
    events = [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "error", "message": message},
        {"type": "turn.failed", "error": {"message": message}},
    ]
    return "\n".join(json.dumps(event) for event in events)


def _classify(raw_stdout: str, raw_stderr: str = "", *, exit_code: int = 1, timed_out: bool = False) -> DispatchResult:
    text, structured = normalize_output("codex", raw_stdout)
    reason = classify_silence(
        text=text, raw_stderr=raw_stderr, structured=structured, exit_code=exit_code, timed_out=timed_out
    )
    return DispatchResult.model_construct(
        silence_reason=reason, structured=structured, raw_stderr=raw_stderr, text=text, exit_code=exit_code
    )


@pytest.mark.parametrize(
    ("stdout", "stderr", "silence_reason", "klass"),
    [
        # rc11 C12: reported as auth_or_content_stop before this PRD.
        (
            _codex_failure("Selected model is at capacity. Please try a different model."),
            "",
            "provider_capacity",
            "provider_capacity",
        ),
        (
            "",
            "Error: refresh token has already been used to generate a new access token",
            "credential_refresh_conflict",
            "credential_refresh_conflict",
        ),
        (
            _codex_failure("invalid_grant: the refresh token was rotated"),
            "",
            "credential_refresh_conflict",
            "credential_refresh_conflict",
        ),
        (_codex_failure("You've hit your usage limit."), "", "quota_exhausted", "quota"),
        ("", "Error: not logged in. Run `codex login`.", "auth_or_content_stop", "auth"),
        (_codex_failure("blocked by the content filter"), "", "auth_or_content_stop", "content_stop"),
        ("", "", "nonzero_exit", "unknown"),
    ],
)
def test_a_failed_run_names_its_remedy_class(stdout: str, stderr: str, silence_reason: str, klass: str) -> None:
    result = _classify(stdout, stderr)
    assert (result.silence_reason, result.error_class) == (silence_reason, klass)
    assert bool(result.last_error) is bool(stdout or stderr)  # the client's own words, when it said any


def test_a_timeout_is_its_own_class() -> None:
    assert _classify("", "", timed_out=True).error_class == "timeout"


def test_last_error_is_the_clients_own_message_capped() -> None:
    result = _classify(_codex_failure("Selected model is at capacity. " + "please try again later " * 100))
    assert result.last_error is not None
    assert result.last_error.startswith("Selected model is at capacity.")
    assert len(result.last_error) == 500
    assert last_error(None, "first line\nsecond line\n\n") == "second line"


def test_a_usable_answer_has_no_error_class_or_last_error() -> None:
    result = DispatchResult.model_construct(
        silence_reason=None, structured=None, raw_stderr="warning: noise", text="ok"
    )
    assert (result.error_class, result.last_error) == (None, None)
    assert error_class(None, None, "") is None


def test_a_review_about_capacity_that_completed_is_not_a_failure() -> None:
    """The stream markers apply only when no usable answer came back, as for the auth markers."""
    reason = classify_silence(
        text="Verdict: the server is overloaded path is handled.",
        raw_stderr="at capacity",
        structured=None,
        exit_code=0,
        timed_out=False,
    )
    assert reason is None


def test_last_error_never_carries_a_token_the_client_echoed() -> None:
    """sol plan review: a credential echoed in an error message is redacted before it reaches any surface."""
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYW5hcnkifQ.c2lnbmF0dXJlLWNhbmFyeQ"
    message = last_error(None, f"401: refresh failed for Bearer {jwt} (key sk-proj-CANARYCANARYCANARY1234)")
    assert message is not None
    assert jwt not in message and "CANARYCANARY" not in message


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: refresh failed: refresh_token: 'opaque-canary-value'",
        'Error: {"access_token": "opaque-canary-value", "status": 401}',
        "auth failed (api_key=opaque-canary-value)",
        "Error: token rotation failed for opaquecanaryvalue0123456789abcdef",
    ],
)
def test_last_error_masks_labelled_and_opaque_credentials(stderr: str) -> None:
    """sol r3: an unprefixed credential in any common error shape never reaches last_error."""
    message = last_error(None, stderr)
    assert message is not None and "redacted" in message.lower()
    assert "opaque-canary-value" not in message and "opaquecanaryvalue0123456789abcdef" not in message
