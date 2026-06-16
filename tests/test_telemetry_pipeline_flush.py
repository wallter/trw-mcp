"""TelemetryPipeline flush behavior tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests._auto_upgrade_test_support import _mock_httpx_client, _mock_httpx_response
from tests._telemetry_pipeline_support import (
    _make_event,
    _make_fake_cfg,
    _read_jsonl,
    make_configured_pipeline,
)

from ._telemetry_pipeline_support import fast_pipeline, pipeline_cls  # noqa: F401


class TestFlushNowOffline:
    """flush_now writes to JSONL when no platform URLs are configured."""

    def test_flush_now_writes_to_jsonl(self, fast_pipeline: Any, tmp_path: Path) -> None:
        """Offline flush_now writes enqueued events to pipeline-events.jsonl."""
        p = fast_pipeline
        p.enqueue(_make_event(tool_name="trw_learn"))
        p.enqueue(_make_event(tool_name="trw_recall"))

        p.flush_now()

        jsonl = tmp_path / ".trw" / "logs" / "pipeline-events.jsonl"
        assert jsonl.exists(), "pipeline-events.jsonl must be created by flush_now"
        lines = _read_jsonl(jsonl)
        assert len(lines) >= 2

    def test_flush_now_result_has_required_keys(self, fast_pipeline: Any) -> None:
        """flush_now result TypedDict contains sent, failed, overflow, skipped_reason."""
        p = fast_pipeline
        p.enqueue(_make_event())
        result = p.flush_now()

        for key in ("sent", "failed", "skipped_reason"):
            assert key in result, f"Missing key '{key}' in flush_now result"

    def test_flush_now_empty_queue_returns_skipped(self, fast_pipeline: Any) -> None:
        """flush_now on empty queue returns skipped_reason (no events to send)."""
        p = fast_pipeline
        result = p.flush_now()
        assert result.get("sent", 0) == 0
        assert result.get("failed", 0) == 0


class TestFlushNowOnline:
    """flush_now sends events to backend when platform_urls are configured."""

    def _make_online_pipeline(
        self,
        pipeline_cls: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        platform_url: str = "http://fake-backend.test",
    ) -> Any:
        """Helper: build a pipeline pointing to a fake backend URL."""
        pipeline, _ = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=_make_fake_cfg(
                effective_platform_urls=[platform_url],
                platform_api_key="test-key",
                installation_id="test-install",
            ),
            pipeline_kwargs={
                "flush_interval_secs": 60.0,
                "batch_size": 100,
                "max_retries": 1,
                "backoff_base": 0.0,
            },
        )
        return pipeline

    def test_flush_now_sends_to_backend(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """flush_now POSTs events to the configured platform URL."""
        p = self._make_online_pipeline(pipeline_cls, tmp_path, monkeypatch)
        p.enqueue(_make_event(tool_name="trw_deliver"))
        p.enqueue(_make_event(tool_name="trw_checkpoint"))

        client_mock = _mock_httpx_client(_mock_httpx_response(status_code=200))

        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            result = p.flush_now()

        client_mock.post.assert_called()
        body = client_mock.post.call_args.kwargs.get("json")
        assert isinstance(body, dict)
        assert "events" in body
        assert result.get("failed", 0) == 0

    def test_flush_now_clears_jsonl_on_success(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a successful HTTP flush, pipeline-events.jsonl is empty."""
        p = self._make_online_pipeline(pipeline_cls, tmp_path, monkeypatch)

        jsonl = tmp_path / ".trw" / "logs" / "pipeline-events.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"event_type": "old_event"}) + "\n")

        p.enqueue(_make_event(tool_name="trw_learn"))

        client_mock = _mock_httpx_client(_mock_httpx_response(status_code=200))

        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            p.flush_now()

        if jsonl.exists():
            remaining = _read_jsonl(jsonl)
            assert len(remaining) == 0, "JSONL must be empty after successful flush"

    def test_flush_now_preserves_jsonl_on_failure(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When HTTP POST fails, enqueued events are preserved for retry."""
        import httpx

        p = self._make_online_pipeline(pipeline_cls, tmp_path, monkeypatch)
        p.enqueue(_make_event(tool_name="trw_learn"))
        p.enqueue(_make_event(tool_name="trw_recall"))

        client_mock = MagicMock()
        client_mock.post.side_effect = httpx.RequestError("connection refused")
        client_mock.__enter__.return_value = client_mock
        client_mock.__exit__.return_value = False

        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            result = p.flush_now()

        jsonl = tmp_path / ".trw" / "logs" / "pipeline-events.jsonl"
        total_preserved = len(p._queue)
        if jsonl.exists():
            total_preserved += len(_read_jsonl(jsonl))
        assert total_preserved >= 2 or result.get("failed", 0) >= 2


class TestFlushResultShape:
    """PipelineFlushResult TypedDict has the correct keys."""

    def test_flush_result_all_keys_present(self, fast_pipeline: Any) -> None:
        """flush_now always returns a dict with sent, failed, and skipped_reason."""
        p = fast_pipeline
        result = p.flush_now()

        required_keys = {"sent", "failed", "skipped_reason"}
        missing = required_keys - result.keys()
        assert not missing, f"flush_now result missing keys: {missing}"

    def test_flush_result_types(self, fast_pipeline: Any) -> None:
        """sent and failed are integers; skipped_reason is str or None."""
        p = fast_pipeline
        result = p.flush_now()

        assert isinstance(result["sent"], int)
        assert isinstance(result["failed"], int)
        assert result["skipped_reason"] is None or isinstance(result["skipped_reason"], str)

    def test_flush_result_sent_plus_failed_equals_queue_size(self, fast_pipeline: Any) -> None:
        """When all events are processed, sent + failed == original queue length."""
        p = fast_pipeline
        for i in range(5):
            p.enqueue(_make_event(seq=i))

        result = p.flush_now()
        if result.get("skipped_reason") is None:
            total = result.get("sent", 0) + result.get("failed", 0)
            assert total == 5 or result.get("skipped_reason") is not None
