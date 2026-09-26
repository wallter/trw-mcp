"""PRD-CORE-300-FR11 (S9): ``trw_status(feedback=...)`` posts what the deleted
the deleted standalone feedback tool posted. A network failure returns a named error,
never a success (never-raises contract, PRD-INFRA-132 NFR02).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.tools.submit_feedback import SubmitFeedbackResult


def _status() -> Any:
    return extract_tool_fn(make_test_server("orchestration"), "trw_status")


class _ConfiguredCfg:
    resolved_backend_url = "https://example.test"
    resolved_backend_api_key = "key"


def test_status_feedback_mode_posts_what_submit_feedback_posts() -> None:
    fake = SubmitFeedbackResult(success=True, submission_id="sub_1", status_code=200)
    with (
        patch("trw_mcp.models.config.get_config", return_value=_ConfiguredCfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=fake) as http,
    ):
        result = _status()(
            feedback={
                "category": "feedback",
                "subject": "a subject",
                "message": "a valid length message body",
            }
        )

    assert result["success"] is True
    assert result["submission_id"] == "sub_1"
    assert http.call_args.kwargs["payload"]["category"] == "feedback"


def test_status_feedback_mode_network_failure_is_never_a_success() -> None:
    with (
        patch("trw_mcp.models.config.get_config", return_value=_ConfiguredCfg()),
        patch("httpx.Client.post", side_effect=httpx.ConnectError("boom")),
    ):
        result = _status()(
            feedback={
                "category": "feedback",
                "subject": "a subject",
                "message": "a valid length message body",
            }
        )

    assert result["success"] is False
    assert "transport error" in result["error"]


def test_status_feedback_mode_rejects_malformed_json_string() -> None:
    with pytest.raises(ToolError):
        _status()(feedback="{not json")


def test_status_without_feedback_or_delivery_still_reports_run_status(tmp_path: Any) -> None:
    """Non-vacuity: the new parameter does not shadow the default status path.

    A fresh project has no run, so the default path takes the SAME no-run
    branch it always did (StateError) rather than short-circuiting into the
    feedback shape.
    """
    from tests._path_isolation import set_current_root
    from trw_mcp.exceptions import StateError

    set_current_root(tmp_path)
    with pytest.raises(StateError):
        _status()()
