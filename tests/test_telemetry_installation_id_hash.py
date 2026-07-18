"""PRD-SEC-004-FR08: the library's telemetry payload builders egress a HASHED
installation_id, never a raw project-directory name.

Behavior tests: assert the egressed value is the non-reversible anonymized form
(double SHA-256 prefix) and that a raw dir-name-looking value never appears.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._telemetry_pipeline_support import (  # noqa: F401
    _make_event,
    fast_pipeline,
    make_configured_pipeline,
    pipeline_cls,
)
from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
from trw_mcp.telemetry.anonymizer import anonymize_installation_id


class TestPipelineInstallationIdHash:
    def test_resolved_installation_id_is_hashed_on_egress(
        self, pipeline_cls: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An event missing installation_id is enriched with the HASHED resolved id."""
        raw_id = "my-secret-project-dir"
        pipeline, _ = make_configured_pipeline(pipeline_cls, tmp_path, monkeypatch)
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_installation_id",
            lambda: raw_id,
        )

        event = _make_event(tool_name="trw_learn")
        event.pop("installation_id", None)
        pipeline.enqueue(event)

        queued = pipeline._queue[-1]
        assert queued["installation_id"] == anonymize_installation_id(raw_id)
        assert queued["installation_id"] != raw_id

    def test_caller_supplied_installation_id_preserved(self, fast_pipeline: Any) -> None:
        """No-overwrite contract: a caller-supplied id is left untouched."""
        fast_pipeline.enqueue({"event_type": "tool_invocation", "installation_id": "caller-provided-id"})
        queued = fast_pipeline._queue[-1]
        assert queued["installation_id"] == "caller-provided-id"


class TestLegacyClientInstallationIdHash:
    """PRD-SEC-004-FR08 residual: the legacy TelemetryClient session-event path
    historically wrote the RAW installation_id to the upload queue while the new
    pipeline path hashed it. The legacy path must now hash it too."""

    def test_legacy_session_event_id_is_hashed_on_flush(self, tmp_path: Path) -> None:
        """A flushed legacy event egresses the hashed installation_id, not the raw one."""
        import json

        from trw_mcp.telemetry.client import TelemetryClient
        from trw_mcp.telemetry.models import SessionStartEvent

        raw_id = "raw-project-dir-name"
        out = tmp_path / "tool-telemetry.jsonl"
        client = TelemetryClient(enabled=True, output_path=out, platform_telemetry_enabled=True)
        client.record_event(SessionStartEvent(installation_id=raw_id, framework_version="v26", learnings_loaded=3))
        written = client.flush()
        assert written == 1

        record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert record["installation_id"] == anonymize_installation_id(raw_id)
        assert record["installation_id"] != raw_id

    def test_legacy_hash_is_idempotent_for_already_hashed_id(self, tmp_path: Path) -> None:
        """An already-hashed (16-hex) id is left untouched (no double-hashing)."""
        import json

        from trw_mcp.telemetry.client import TelemetryClient
        from trw_mcp.telemetry.models import SessionEndEvent

        already = anonymize_installation_id("some-install")
        out = tmp_path / "tool-telemetry.jsonl"
        client = TelemetryClient(enabled=True, output_path=out, platform_telemetry_enabled=True)
        client.record_event(SessionEndEvent(installation_id=already, framework_version="v26", tools_invoked=2))
        client.flush()

        record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert record["installation_id"] == already


class TestPublisherSourceProjectHash:
    def test_source_project_is_hashed(self, tmp_path: Path) -> None:
        """publish_learnings egresses source_project as the hashed installation id."""
        cfg = _make_config(learning_sharing_enabled=True)
        # _make_config builds a real TRWConfig; set a raw-looking installation_id.
        import trw_mcp.models.config as config_mod  # noqa: F401

        object.__setattr__(cfg, "installation_id", "raw-dir-name-2026")

        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))

        captured: dict[str, object] = {}

        def _capture(url: str, payload: dict[str, object], api_key: str = "") -> bool:
            captured.update(payload)
            return True

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", side_effect=_capture),
        ):
            from trw_mcp.telemetry.publisher import publish_learnings

            result = publish_learnings()

        assert result["published"] == 1
        assert captured["source_project"] == anonymize_installation_id("raw-dir-name-2026")
        assert captured["source_project"] != "raw-dir-name-2026"
