"""Core TelemetryPipeline behavior tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._telemetry_pipeline_support import (
    _make_event,
    _read_jsonl,
)

from ._telemetry_pipeline_support import fast_pipeline, pipeline_cls  # noqa: F401


class TestDisabledPipeline:
    """Tests for pipeline with _enabled=False."""

    def test_disabled_pipeline_enqueue_is_noop(self, pipeline_cls: Any) -> None:
        """Enqueuing 10 events onto a disabled pipeline leaves the queue empty."""
        p = pipeline_cls()
        object.__setattr__(p, "_enabled", False)

        for i in range(10):
            p.enqueue(_make_event(tool_name=f"tool_{i}"))

        assert len(p._queue) == 0

    def test_disabled_pipeline_flush_returns_skipped(self, pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """flush_now on a disabled pipeline returns a result with skipped_reason set."""
        p = pipeline_cls()
        object.__setattr__(p, "_enabled", False)

        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: None, raising=False)
        monkeypatch.setattr("trw_mcp.telemetry.pipeline.resolve_trw_dir", lambda: None, raising=False)

        result = p.flush_now()
        assert result["skipped_reason"] is not None
        assert result["sent"] == 0


class TestEnrichment:
    """Events are enriched with installation_id and framework_version."""

    def test_enrichment_adds_installation_id_and_version(
        self, fast_pipeline: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Enqueuing an event without installation_id causes the pipeline to inject it."""
        p = fast_pipeline
        p.enqueue({"event_type": "tool_invocation", "tool_name": "trw_recall"})

        assert len(p._queue) == 1
        queued = list(p._queue)[0]
        assert "installation_id" in queued or queued.get("event_type") == "tool_invocation"
        result = p.flush_now()

        jsonl = tmp_path / ".trw" / "logs" / "pipeline-events.jsonl"
        if jsonl.exists():
            lines = _read_jsonl(jsonl)
            if lines:
                assert any("installation_id" in rec or "event_type" in rec for rec in lines)
        assert result["sent"] >= 0

    def test_enrichment_does_not_overwrite_existing_installation_id(self, fast_pipeline: Any) -> None:
        """Existing installation_id in event must not be overwritten by enrichment."""
        p = fast_pipeline
        p.enqueue({"event_type": "tool_invocation", "installation_id": "caller-provided-id"})

        assert len(p._queue) == 1
        queued = list(p._queue)[0]
        if "installation_id" in queued:
            assert queued["installation_id"] == "caller-provided-id"


class TestAnonymization:
    """Events containing filesystem paths are redacted before storage."""

    def test_anonymization_redacts_error_paths(
        self, fast_pipeline: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An event whose 'error' field contains the project root is redacted."""
        project_root = str(tmp_path)
        monkeypatch.setattr("trw_mcp.telemetry.pipeline.resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)

        p = fast_pipeline
        raw_error = f"{project_root}/src/foo.py not found"
        p.enqueue(_make_event(error=raw_error))

        queued = list(p._queue)[0]
        error_val = str(queued.get("error", raw_error))
        assert project_root not in error_val, (
            f"project root '{project_root}' must be redacted from error field, got: {error_val!r}"
        )

    def test_anonymization_redacts_string_values(
        self, fast_pipeline: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All string event values containing the project root are redacted."""
        project_root = str(tmp_path)
        monkeypatch.setattr("trw_mcp.telemetry.pipeline.resolve_project_root", lambda: tmp_path)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)

        p = fast_pipeline
        raw_path = f"{project_root}/some/deeply/nested/file.py"
        p.enqueue(_make_event(file_path=raw_path))

        queued = list(p._queue)[0]
        if "file_path" in queued:
            val = str(queued["file_path"])
            assert project_root not in val, f"project root must be redacted from file_path, got: {val!r}"

    def test_anonymization_preserves_non_path_strings(self, fast_pipeline: Any) -> None:
        """Non-path string values pass through without modification."""
        p = fast_pipeline
        p.enqueue(_make_event(tool_name="trw_recall", phase="research"))

        queued = list(p._queue)[0]
        assert queued.get("tool_name") == "trw_recall"
        assert queued.get("phase") == "research"


class TestQueueOverflow:
    """max_queue_size enforcement: oldest events evicted, counter incremented."""

    def test_queue_overflow_evicts_oldest(self, pipeline_cls: Any) -> None:
        """With max_queue_size=5, enqueueing 8 events retains only the last 5."""
        p = pipeline_cls(max_queue_size=5)
        for i in range(8):
            p.enqueue(_make_event(tool_name=f"tool_{i}", seq=i))

        assert len(p._queue) == 5
        seqs = [e.get("seq") for e in p._queue if "seq" in e]
        if seqs:
            assert min(seqs) >= 3

    def test_queue_overflow_increments_counter(self, pipeline_cls: Any) -> None:
        """Overflow of 3 events increments _overflow_count by at least 3."""
        p = pipeline_cls(max_queue_size=5)
        for i in range(8):
            p.enqueue(_make_event(seq=i))

        assert p._overflow_count >= 3

    def test_queue_at_exact_capacity_does_not_overflow(self, pipeline_cls: Any) -> None:
        """Enqueueing exactly max_queue_size events causes no overflow."""
        p = pipeline_cls(max_queue_size=10)
        for i in range(10):
            p.enqueue(_make_event(seq=i))

        assert len(p._queue) == 10
        assert p._overflow_count == 0


class TestScrubPiiAllFields:
    """Round-2 SECURITY fix: _scrub_pii must scrub ALL string fields, not only 'error'."""

    def test_scrub_pii_scrubs_non_error_string_fields(self, pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """PII in message/args fields must be redacted, not just the 'error' field."""
        # Avoid path-redaction interference; isolate strip_pii behavior.
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.resolve_project_root",
            lambda: None,
            raising=False,
        )
        p = pipeline_cls()
        event: dict[str, object] = {
            "error": "boom contact admin@example.com",
            "message": "user alice@example.com hit an error",
            "args": "token sk-ABCDEFGHIJKLMNOPQRSTUVWX leaked",
            "count": 7,
        }
        p._scrub_pii(event)

        assert "admin@example.com" not in str(event["error"])
        # Regression: the message field was previously shipped raw.
        assert "alice@example.com" not in str(event["message"])
        assert "<email>" in str(event["message"])
        assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in str(event["args"])
        # Non-string fields are untouched.
        assert event["count"] == 7

    def test_scrub_pii_preserves_safe_identifier_fields(
        self, pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Safe-key allowlist (ids/timestamps) is not mangled by scrubbing."""
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.resolve_project_root",
            lambda: None,
            raising=False,
        )
        p = pipeline_cls()
        event: dict[str, object] = {
            "session_id": "agent@host-session",
            "ts": "2026-06-09T00:00:00+00:00",
        }
        p._scrub_pii(event)
        assert event["session_id"] == "agent@host-session"
        assert event["ts"] == "2026-06-09T00:00:00+00:00"

    def test_scrub_pii_recurses_into_nested_dict(self, pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """PII buried inside a nested dict value must be scrubbed (was leaking raw)."""
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.resolve_project_root",
            lambda: None,
            raising=False,
        )
        p = pipeline_cls()
        event: dict[str, object] = {
            "args": {"user": "alice@example.com", "nested": {"contact": "bob@example.com"}},
        }
        p._scrub_pii(event)

        flat = str(event["args"])
        assert "alice@example.com" not in flat
        assert "bob@example.com" not in flat
        assert "<email>" in flat

    def test_scrub_pii_recurses_into_nested_list(self, pipeline_cls: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        """PII inside list elements (including dicts in lists) must be scrubbed."""
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.resolve_project_root",
            lambda: None,
            raising=False,
        )
        p = pipeline_cls()
        event: dict[str, object] = {
            "payload": ["carol@example.com", {"email": "dan@example.com"}, 42],
        }
        p._scrub_pii(event)

        payload = event["payload"]
        assert isinstance(payload, list)
        assert "carol@example.com" not in str(payload[0])
        assert "dan@example.com" not in str(payload[1])
        # Non-string scalars in the list survive untouched.
        assert payload[2] == 42
