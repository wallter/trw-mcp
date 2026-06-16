"""PRD-SEC-004-FR01: single choke point — every off-machine telemetry send
path honors platform_telemetry_enabled. When the flag is False, zero httpx POST
is issued and the local JSONL durable buffer is preserved.

Behavior tests (not existence tests): assert zero POST via mocked transport and
the skipped_reason="platform_telemetry_disabled" contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tests._auto_upgrade_test_support import _mock_httpx_client, _mock_httpx_response
from tests._telemetry_pipeline_support import (  # noqa: F401
    _make_event,
    _make_fake_cfg,
    _read_jsonl,
    fast_pipeline,
    make_configured_pipeline,
    pipeline_cls,
)


class TestPipelineGate:
    """TelemetryPipeline.flush_now respects platform_telemetry_enabled."""

    def test_pipeline_respects_telemetry_disabled(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disabled flag + non-empty platform_urls => zero POST, disabled reason."""
        cfg = _make_fake_cfg(
            platform_telemetry_enabled=False,
            effective_platform_urls=["http://fake-backend.test"],
            platform_api_key="test-key",
        )
        pipeline, trw_dir = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=cfg,
            pipeline_kwargs={"flush_interval_secs": 60.0, "max_retries": 1, "backoff_base": 0.0},
        )
        pipeline.enqueue(_make_event(tool_name="trw_deliver"))

        client_mock = _mock_httpx_client(_mock_httpx_response(status_code=200))
        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            result = pipeline.flush_now()

        client_mock.post.assert_not_called()
        assert result["sent"] == 0
        assert result["skipped_reason"] == "platform_telemetry_disabled"

    def test_pipeline_disabled_preserves_local_jsonl(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The local JSONL durable write happens even when egress is suppressed."""
        cfg = _make_fake_cfg(
            platform_telemetry_enabled=False,
            effective_platform_urls=["http://fake-backend.test"],
        )
        pipeline, trw_dir = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=cfg,
            pipeline_kwargs={"flush_interval_secs": 60.0, "max_retries": 1, "backoff_base": 0.0},
        )
        pipeline.enqueue(_make_event(tool_name="trw_learn"))

        client_mock = _mock_httpx_client(_mock_httpx_response(status_code=200))
        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            pipeline.flush_now()

        jsonl = trw_dir / "logs" / "pipeline-events.jsonl"
        assert jsonl.exists()
        assert len(_read_jsonl(jsonl)) >= 1

    def test_pipeline_enabled_still_sends(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity: flag True + URLs => POST occurs (no false-positive gating)."""
        cfg = _make_fake_cfg(
            platform_telemetry_enabled=True,
            effective_platform_urls=["http://fake-backend.test"],
            platform_api_key="test-key",
        )
        pipeline, _ = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=cfg,
            pipeline_kwargs={"flush_interval_secs": 60.0, "max_retries": 1, "backoff_base": 0.0},
        )
        pipeline.enqueue(_make_event(tool_name="trw_deliver"))

        client_mock = _mock_httpx_client(_mock_httpx_response(status_code=200))
        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            result = pipeline.flush_now()

        client_mock.post.assert_called()
        assert result["sent"] == 1


def _write_events(input_path: Path, events: list[dict[str, object]], *, consented: bool = True) -> None:
    # PRD-SEC-004-FR01: stamp the consent state recorded at write time; the
    # sender uploads only consented rows (default consented for these tests).
    import json

    from trw_mcp.telemetry.sender import stamp_consent

    input_path.parent.mkdir(parents=True, exist_ok=True)
    with input_path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(stamp_consent(dict(ev), consented=consented)) + "\n")


class TestSenderGate:
    """BatchSender.send respects platform_telemetry_enabled."""

    def _make_sender(self, tmp_path: Path, *, enabled: bool) -> Any:
        from trw_mcp.telemetry.sender import BatchSender

        input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
        return BatchSender(
            platform_urls=["https://api.example.com"],
            input_path=input_path,
            batch_size=100,
            max_retries=1,
            backoff_base=0.0,
            platform_telemetry_enabled=enabled,
        ), input_path

    def test_send_respects_telemetry_disabled(self, tmp_path: Path) -> None:
        """Disabled flag + queued events => zero POST, local queue untouched."""
        sender, input_path = self._make_sender(tmp_path, enabled=False)
        _write_events(input_path, [{"event_type": "test"}, {"event_type": "test2"}])

        with patch.object(sender, "_http_post", return_value=True) as post_mock:
            result = sender.send()

        post_mock.assert_not_called()
        assert result["sent"] == 0
        assert result["skipped_reason"] == "platform_telemetry_disabled"
        # Local record preserved (queue not rewritten/truncated)
        assert input_path.exists()
        assert len(_read_jsonl(input_path)) == 2

    def test_send_enabled_still_posts(self, tmp_path: Path) -> None:
        """Sanity: flag True + queued events => POST occurs."""
        sender, input_path = self._make_sender(tmp_path, enabled=True)
        _write_events(input_path, [{"event_type": "test"}])

        with patch.object(sender, "_http_post", return_value=True) as post_mock:
            result = sender.send()

        post_mock.assert_called()
        assert result["sent"] == 1


class TestDefaultConfigZeroEgress:
    """The load-bearing invariant: a fresh default TRWConfig produces zero egress."""

    def test_default_config_pipeline_zero_post(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(platform_urls=["http://fake-backend.test"])
        # Default config: platform_telemetry_enabled is False by default.
        assert cfg.platform_telemetry_enabled is False

        pipeline, _ = make_configured_pipeline(
            pipeline_cls,
            tmp_path,
            monkeypatch,
            cfg=cfg,
            pipeline_kwargs={"flush_interval_secs": 60.0, "max_retries": 1, "backoff_base": 0.0},
        )
        pipeline.enqueue(_make_event(tool_name="trw_deliver"))

        client_mock = MagicMock()
        with patch("trw_mcp.telemetry.pipeline.httpx.Client", return_value=client_mock):
            result = pipeline.flush_now()

        client_mock.post.assert_not_called()
        assert result["skipped_reason"] == "platform_telemetry_disabled"

    def test_default_config_sender_zero_post(self, tmp_path: Path) -> None:
        from trw_mcp.telemetry.sender import BatchSender

        input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
        _write_events(input_path, [{"event_type": "test"}])
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(platform_urls=["https://api.example.com"])

        with (
            patch("trw_mcp.telemetry.sender.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.sender.resolve_trw_dir", return_value=tmp_path),
        ):
            sender = BatchSender.from_config()
            sender._input_path = input_path
            with patch.object(sender, "_http_post", return_value=True) as post_mock:
                result = sender.send()

        post_mock.assert_not_called()
        assert result["skipped_reason"] == "platform_telemetry_disabled"
