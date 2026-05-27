"""Tests for ``trw_submit_feedback`` MCP tool — PRD-CORE-182.

Covers:
- Validation: category enum, length bounds, header injection guards
  (subject/metadata CR-LF), metadata caps, contact_email shape
- Auto-metadata population (trw_mcp_version / python_version / os_platform)
- HTTP wiring: payload shape, success path, non-2xx path, transport-error path
- Backend-not-configured guard (no URL/key → clear error, no HTTP)
- MCP tool registration smoke test (tool appears on a FastMCP instance)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from trw_mcp.tools.submit_feedback import (
    MAX_MESSAGE_LEN,
    MAX_METADATA_KEY_LEN,
    MAX_METADATA_KEYS,
    MAX_METADATA_VALUE_LEN,
    MAX_SUBJECT_LEN,
    MIN_MESSAGE_LEN,
    SubmitFeedbackResult,
    _build_auto_metadata,
    _merge_metadata,
    _validate,
    register_submit_feedback_tools,
    submit_feedback,
    submit_feedback_via_http,
)

# ---------------------------------------------------------------------------
# Pure validation
# ---------------------------------------------------------------------------


def test_validate_rejects_unknown_category() -> None:
    err = _validate(
        category="system_takeover",
        subject="x",
        message="valid length message body",
        metadata=None,
        contact_email=None,
    )
    assert "invalid category" in err


def test_validate_rejects_empty_subject() -> None:
    err = _validate(
        category="feedback",
        subject="   ",
        message="valid length message body",
        metadata=None,
        contact_email=None,
    )
    assert "subject" in err


def test_validate_rejects_subject_over_cap() -> None:
    err = _validate(
        category="feedback",
        subject="x" * (MAX_SUBJECT_LEN + 1),
        message="valid length message body",
        metadata=None,
        contact_email=None,
    )
    assert "subject" in err


def test_validate_rejects_subject_with_newline() -> None:
    err = _validate(
        category="feedback",
        subject="ok\r\nBcc: a@b.com",
        message="valid length message body",
        metadata=None,
        contact_email=None,
    )
    assert "newline" in err


def test_validate_rejects_short_message() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="short",
        metadata=None,
        contact_email=None,
    )
    assert "at least" in err and str(MIN_MESSAGE_LEN) in err


def test_validate_rejects_message_over_cap() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="x" * (MAX_MESSAGE_LEN + 1),
        metadata=None,
        contact_email=None,
    )
    assert "at most" in err and str(MAX_MESSAGE_LEN) in err


def test_validate_rejects_metadata_with_too_many_keys() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="valid length message body",
        metadata={f"k{i}": "v" for i in range(MAX_METADATA_KEYS + 1)},
        contact_email=None,
    )
    assert str(MAX_METADATA_KEYS) in err


def test_validate_rejects_metadata_value_over_cap() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="valid length message body",
        metadata={"big": "x" * (MAX_METADATA_VALUE_LEN + 1)},
        contact_email=None,
    )
    assert str(MAX_METADATA_VALUE_LEN) in err


def test_validate_rejects_metadata_key_over_cap() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="valid length message body",
        metadata={"k" * (MAX_METADATA_KEY_LEN + 1): "ok"},
        contact_email=None,
    )
    assert str(MAX_METADATA_KEY_LEN) in err


def test_validate_rejects_metadata_with_newline() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="valid length message body",
        metadata={"trace": "line1\nline2"},
        contact_email=None,
    )
    assert "newline" in err


def test_validate_rejects_bad_contact_email() -> None:
    err = _validate(
        category="feedback",
        subject="x",
        message="valid length message body",
        metadata=None,
        contact_email="not-an-email",
    )
    assert "email" in err


def test_validate_accepts_minimal_valid_payload() -> None:
    err = _validate(
        category="bugfix",
        subject="x",
        message="ten chars long",
        metadata=None,
        contact_email=None,
    )
    assert err == ""


# ---------------------------------------------------------------------------
# Auto-metadata
# ---------------------------------------------------------------------------


def test_build_auto_metadata_has_expected_keys() -> None:
    meta = _build_auto_metadata()
    assert set(meta.keys()) == {"trw_mcp_version", "python_version", "os_platform"}
    # python_version is always set (we just read sys.version_info).
    assert meta["python_version"].count(".") == 2
    # os_platform is always non-empty.
    assert meta["os_platform"]


def test_merge_metadata_user_overrides_auto() -> None:
    auto = {"trw_mcp_version": "1.0.0", "os_platform": "linux"}
    user = {"trw_mcp_version": "override", "extra": "x"}
    merged = _merge_metadata(user, auto)
    assert merged["trw_mcp_version"] == "override"
    assert merged["extra"] == "x"
    assert merged["os_platform"] == "linux"


def test_merge_metadata_drops_empty_values() -> None:
    auto = {"a": "1", "b": ""}
    user = {"c": "", "d": "2"}
    merged = _merge_metadata(user, auto)
    assert merged == {"a": "1", "d": "2"}


def test_merge_metadata_handles_none_user() -> None:
    auto = {"a": "1"}
    merged = _merge_metadata(None, auto)
    assert merged == {"a": "1"}


# ---------------------------------------------------------------------------
# HTTP wiring
# ---------------------------------------------------------------------------


def _mock_httpx_response(status: int, body: dict[str, Any] | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    if body is not None:
        resp.json.return_value = body
        resp.text = str(body)
    else:
        resp.json.side_effect = ValueError("no json")
        resp.text = ""
    return resp


def test_submit_feedback_via_http_success() -> None:
    with patch("httpx.Client") as mock_client_cls:
        ctx_mgr = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_httpx_response(
            200, {"submission_id": "sub_abc123", "status": "accepted"}
        )
        ctx_mgr.__enter__.return_value = mock_client
        mock_client_cls.return_value = ctx_mgr

        result = submit_feedback_via_http(
            backend_url="https://api.trw.test",
            api_key="key-xyz",
            payload={
                "category": "bugfix",
                "subject": "x",
                "message": "valid length",
                "metadata": {"a": "1"},
            },
        )

    assert result.success is True
    assert result.submission_id == "sub_abc123"
    assert result.status_code == 200
    assert result.metadata_attached == {"a": "1"}

    # Verify URL + auth header + JSON payload sent correctly.
    call = mock_client.post.call_args
    assert call.args[0] == "https://api.trw.test/v1/submissions"
    assert call.kwargs["headers"]["Authorization"] == "Bearer key-xyz"
    assert call.kwargs["headers"]["Content-Type"] == "application/json"
    assert call.kwargs["json"]["category"] == "bugfix"


def test_submit_feedback_via_http_strips_trailing_slash() -> None:
    """Backend URL with trailing slash MUST NOT produce //v1/submissions."""
    with patch("httpx.Client") as mock_client_cls:
        ctx_mgr = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_httpx_response(
            200, {"submission_id": "sub_x"}
        )
        ctx_mgr.__enter__.return_value = mock_client
        mock_client_cls.return_value = ctx_mgr

        submit_feedback_via_http(
            backend_url="https://api.trw.test/",
            api_key="k",
            payload={"category": "feedback", "subject": "x", "message": "valid length"},
        )

    url = mock_client.post.call_args.args[0]
    assert url == "https://api.trw.test/v1/submissions"


def test_submit_feedback_via_http_non_2xx_surfaces_detail() -> None:
    with patch("httpx.Client") as mock_client_cls:
        ctx_mgr = MagicMock()
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_httpx_response(
            429,
            {"detail": {"error": "rate_limit_exceeded", "detail": "Please wait 60s."}},
        )
        ctx_mgr.__enter__.return_value = mock_client
        mock_client_cls.return_value = ctx_mgr

        result = submit_feedback_via_http(
            backend_url="https://api.trw.test",
            api_key="k",
            payload={"category": "feedback", "subject": "x", "message": "valid length"},
        )

    assert result.success is False
    assert result.status_code == 429
    assert "Please wait" in result.error


def test_submit_feedback_via_http_transport_error_is_returned_not_raised() -> None:
    with patch("httpx.Client") as mock_client_cls:
        mock_client_cls.side_effect = httpx.HTTPError("connection refused")
        result = submit_feedback_via_http(
            backend_url="https://api.trw.test",
            api_key="k",
            payload={"category": "feedback", "subject": "x", "message": "valid length"},
        )
    assert result.success is False
    assert "transport error" in result.error
    assert result.status_code == 0


# ---------------------------------------------------------------------------
# Top-level submit_feedback wiring
# ---------------------------------------------------------------------------


def test_submit_feedback_short_circuits_on_validation_error() -> None:
    with patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http") as http:
        result = submit_feedback(
            category="bogus",
            subject="x",
            message="valid length message",
        )
    http.assert_not_called()
    assert result.success is False
    assert "invalid category" in result.error


def test_submit_feedback_errors_when_backend_not_configured() -> None:
    """Missing URL/key returns a friendly error without an HTTP call."""

    class _StubCfg:
        resolved_backend_url = ""
        resolved_backend_api_key = ""

    with patch("trw_mcp.models.config.get_config", return_value=_StubCfg()), patch(
        "trw_mcp.tools.submit_feedback.submit_feedback_via_http"
    ) as http:
        result = submit_feedback(
            category="feedback",
            subject="x",
            message="valid length message",
        )

    http.assert_not_called()
    assert result.success is False
    assert "backend not configured" in result.error


def test_submit_feedback_auto_attaches_metadata() -> None:
    class _StubCfg:
        resolved_backend_url = "https://api.trw.test"
        resolved_backend_api_key = "key-xyz"

    fake = SubmitFeedbackResult(
        success=True, submission_id="sub_abc", status_code=200, metadata_attached={}
    )
    with patch("trw_mcp.models.config.get_config", return_value=_StubCfg()), patch(
        "trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=fake
    ) as http:
        result = submit_feedback(
            category="feedback",
            subject="hello",
            message="this is a valid length message body",
            metadata={"custom": "value"},
        )

    assert result.success is True
    call_kwargs = http.call_args.kwargs
    payload = call_kwargs["payload"]
    assert payload["category"] == "feedback"
    assert payload["subject"] == "hello"
    # User metadata + auto metadata merged.
    assert payload["metadata"]["custom"] == "value"
    assert "trw_mcp_version" in payload["metadata"]
    assert "python_version" in payload["metadata"]
    assert "os_platform" in payload["metadata"]


def test_submit_feedback_forwards_contact_email_only_when_set() -> None:
    class _StubCfg:
        resolved_backend_url = "https://api.trw.test"
        resolved_backend_api_key = "key"

    fake = SubmitFeedbackResult(success=True, submission_id="sub_x", status_code=200)

    with patch("trw_mcp.models.config.get_config", return_value=_StubCfg()), patch(
        "trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=fake
    ) as http:
        submit_feedback(
            category="question",
            subject="x",
            message="valid length message body",
            contact_email="alice@example.com",
        )
        assert "contact_email" in http.call_args.kwargs["payload"]

    with patch("trw_mcp.models.config.get_config", return_value=_StubCfg()), patch(
        "trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=fake
    ) as http:
        submit_feedback(
            category="question",
            subject="x",
            message="valid length message body",
        )
        assert "contact_email" not in http.call_args.kwargs["payload"]


# ---------------------------------------------------------------------------
# MCP tool registration smoke
# ---------------------------------------------------------------------------


def test_register_submit_feedback_tools_registers_tool_on_server() -> None:
    """The registration helper attaches a tool callable to the FastMCP server."""
    import asyncio

    from fastmcp import FastMCP

    server = FastMCP(name="test-server")
    register_submit_feedback_tools(server)
    tools = asyncio.run(server.list_tools())
    names = {getattr(t, "name", None) for t in tools}
    assert "trw_submit_feedback" in names, (
        f"expected trw_submit_feedback registered; found: {sorted(n for n in names if n)}"
    )


# ---------------------------------------------------------------------------
# Parametric corner cases on bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category", ["bugfix", "installation", "feedback", "feature_request", "question", "other"]
)
def test_validate_accepts_all_documented_categories(category: str) -> None:
    err = _validate(
        category=category,
        subject="x",
        message="valid length message body",
        metadata=None,
        contact_email=None,
    )
    assert err == ""
