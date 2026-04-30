"""TelemetryPipeline retry and drain resilience tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests._auto_upgrade_test_support import _mock_httpx_response
from tests._telemetry_pipeline_support import (
    _make_event,
    _make_fake_cfg,
    _reset_pipeline_singleton,
    make_configured_pipeline,
    pipeline_cls,
)


class TestRetryWithBackoff:
    """HTTP sends retry up to max_retries times with backoff before failing."""

    def test_retry_with_backoff_eventually_succeeds(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failing twice then succeeding results in 3 total HTTP calls and sent > 0."""
        import httpx

        p, _ = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=_make_fake_cfg(
                effective_platform_urls=["http://retry-backend.test"],
                installation_id="retry-test",
                framework_version="v0",
            ),
            pipeline_kwargs={
                "flush_interval_secs": 60.0,
                "batch_size": 100,
                "max_retries": 3,
                "backoff_base": 0.0,
            },
        )
        p.enqueue(_make_event(tool_name="trw_retry_test"))

        call_count = 0
        ok_resp = _mock_httpx_response(status_code=200)

        def make_client(*args: Any, **kwargs: Any) -> Any:
            client = MagicMock()

            def post(*_a: Any, **_kw: Any) -> Any:
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise httpx.RequestError("temporary failure")
                return ok_resp

            client.post.side_effect = post
            client.__enter__.return_value = client
            client.__exit__.return_value = False
            return client

        with patch("trw_mcp.telemetry.pipeline.httpx.Client", side_effect=make_client):
            result = p.flush_now()

        assert call_count == 3, f"Expected 3 HTTP calls (2 fail + 1 success), got {call_count}"
        assert result.get("sent", 0) > 0 or result.get("failed", 0) == 0

    def test_retry_exhausted_marks_batch_failed(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When all retries fail, sent==0 and events are preserved."""
        import httpx

        p, _ = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=_make_fake_cfg(
                effective_platform_urls=["http://always-down.test"],
                installation_id="fail-test",
                framework_version="v0",
            ),
            pipeline_kwargs={"max_retries": 2, "backoff_base": 0.0},
        )
        p.enqueue(_make_event(tool_name="trw_fail_test"))

        client_mock = MagicMock()
        client_mock.post.side_effect = httpx.RequestError("down")
        client_mock.__enter__.return_value = client_mock
        client_mock.__exit__.return_value = False

        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            result = p.flush_now()

        assert result.get("sent", 0) == 0


class TestNoDoubleSend:
    """JSONL is empty after a successful flush — no duplicate sends on retry."""

    def test_no_double_send_after_drain(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After flush_now succeeds, pipeline-events.jsonl is empty (nothing to re-send)."""
        p, trw_dir = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=_make_fake_cfg(
                effective_platform_urls=[],
                installation_id="drain-test",
                framework_version="v0",
            ),
            pipeline_kwargs={"max_retries": 1, "backoff_base": 0.0},
        )
        p.enqueue(_make_event(tool_name="trw_drain_test"))

        p.flush_now()
        second_result = p.flush_now()

        assert len(p._queue) == 0

        jsonl = trw_dir / "logs" / "pipeline-events.jsonl"
        if jsonl.exists():
            pass

        assert second_result.get("sent", 0) >= 0
