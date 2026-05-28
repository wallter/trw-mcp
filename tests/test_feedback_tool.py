"""PRD-INFRA-132 FR04 — unit tests for ``trw_submit_feedback_impl``.

Tests cover:
1. Happy path: 200 response yields submission_id.
2. HTTP error (503) is captured in ``error`` and does NOT raise.
3. Network failure (httpx.ConnectError) returns structured error.
4. Redaction runs BEFORE the POST — captured request body has no plaintext.

Backend creds + learning attachment side-effects are mocked so these tests
stay in the unit tier (no real I/O / network).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

import trw_mcp.tools.feedback as feedback_mod
from trw_mcp.tools.feedback import (
    FeedbackSubmissionResult,
    trw_submit_feedback_impl,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal httpx.Response stand-in covering the fields we read."""

    def __init__(
        self,
        status_code: int,
        json_payload: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_payload
        self.text = text

    def json(self) -> dict[str, Any]:
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """httpx.Client stand-in that captures POST args.

    Acts as both the constructor return value AND the context-manager target
    so ``with httpx.Client(...) as c: c.post(...)`` works.
    """

    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response
        self.captured_url: str | None = None
        self.captured_payload: dict[str, Any] | None = None
        self.captured_headers: dict[str, str] | None = None

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.captured_url = url
        self.captured_payload = json
        self.captured_headers = headers
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse | Exception,
) -> _FakeClient:
    """Patch httpx.Client used by feedback.py to return our fake."""
    fake = _FakeClient(response)

    def _factory(*_args: object, **_kwargs: object) -> _FakeClient:
        return fake

    monkeypatch.setattr(feedback_mod.httpx, "Client", _factory)
    return fake


def _stub_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        feedback_mod,
        "_load_backend_creds",
        lambda: ("https://backend.example.com", "test-key-abc"),
    )


def _stub_attachments_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_mod, "_fetch_recent_learnings_block", lambda: "")
    monkeypatch.setattr(feedback_mod, "_fetch_last_error_block", lambda: "")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_submission_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    fake = _install_fake_client(
        monkeypatch,
        _FakeResponse(200, json_payload={"submission_id": "sub_abc123", "status": "accepted"}),
    )

    result = trw_submit_feedback_impl(
        category="bug",
        summary="something broke",
        detail="here is what happened",
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert isinstance(result, FeedbackSubmissionResult)
    assert result.submission_id == "sub_abc123"
    assert result.status == "accepted"
    assert result.error is None

    # Captured request shape
    assert fake.captured_url == "https://backend.example.com/v1/submissions"
    assert fake.captured_payload is not None
    assert fake.captured_payload["category"] == "bug"
    assert fake.captured_payload["subject"] == "something broke"
    assert fake.captured_payload["body"] == "here is what happened"
    assert fake.captured_headers is not None
    assert fake.captured_headers["Authorization"] == "Bearer test-key-abc"


def test_subject_truncated_to_max(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    fake = _install_fake_client(
        monkeypatch,
        _FakeResponse(200, json_payload={"submission_id": "x"}),
    )

    long_summary = "a" * 500
    trw_submit_feedback_impl(
        category="bug",
        summary=long_summary,
        detail="ok",
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert fake.captured_payload is not None
    assert len(fake.captured_payload["subject"]) == 200


# ---------------------------------------------------------------------------
# HTTP error
# ---------------------------------------------------------------------------


def test_http_503_returns_error_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    _install_fake_client(
        monkeypatch,
        _FakeResponse(503, json_payload={"detail": "backend down"}),
    )

    result = trw_submit_feedback_impl(
        category="bug",
        summary="hi",
        detail="ok",
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert result.submission_id == ""
    assert result.status == "error"
    assert result.error is not None
    assert "http_503" in result.error


def test_http_400_with_text_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    _install_fake_client(
        monkeypatch,
        _FakeResponse(400, text="bad payload"),
    )

    result = trw_submit_feedback_impl(
        category="bug",
        summary="hi",
        detail="ok",
        include_recent_learnings=False,
        include_last_error=False,
    )
    assert result.status == "error"
    assert result.error is not None
    assert "http_400" in result.error


# ---------------------------------------------------------------------------
# Network failure
# ---------------------------------------------------------------------------


def test_network_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    _install_fake_client(
        monkeypatch,
        httpx.ConnectError("connection refused"),
    )

    result = trw_submit_feedback_impl(
        category="bug",
        summary="hi",
        detail="ok",
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert result.status == "error"
    assert result.error is not None
    assert result.error.startswith("network: ConnectError")


def test_timeout_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    _install_fake_client(
        monkeypatch,
        httpx.ConnectTimeout("timeout"),
    )

    result = trw_submit_feedback_impl(
        category="bug",
        summary="hi",
        detail="ok",
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert result.status == "error"
    assert result.error is not None
    assert "network:" in result.error


# ---------------------------------------------------------------------------
# Redaction happens BEFORE the POST
# ---------------------------------------------------------------------------


def test_tool_redacts_before_send(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_creds(monkeypatch)
    _stub_attachments_empty(monkeypatch)
    fake = _install_fake_client(
        monkeypatch,
        _FakeResponse(200, json_payload={"submission_id": "ok"}),
    )

    secret_body = "I have a key: trw_lic_abc and that's the bug"
    trw_submit_feedback_impl(
        category="bug",
        summary="leak demo",
        detail=secret_body,
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert fake.captured_payload is not None
    sent_body = fake.captured_payload["body"]
    assert "trw_lic_abc" not in sent_body
    assert "<REDACTED:license_key>" in sent_body


# ---------------------------------------------------------------------------
# Auth-missing fast path
# ---------------------------------------------------------------------------


def test_auth_missing_returns_error_without_post(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback_mod, "_load_backend_creds", lambda: ("", ""))
    # If httpx.Client were used, it would crash the test — we don't install one.

    result = trw_submit_feedback_impl(
        category="bug",
        summary="hi",
        detail="ok",
        include_recent_learnings=False,
        include_last_error=False,
    )

    assert result.status == "error"
    assert result.error is not None
    assert "auth_missing" in result.error
