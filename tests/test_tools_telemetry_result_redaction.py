"""PRD-SEC-004 (telemetry-privacy-4): the detailed tool-telemetry record runs
result_summary (repr(result)[:100]) through strip_pii() before it is written to
the upload queue, so no raw PII (emails / API keys) can egress.

Behavior test: a tool result containing an email + api-key is scrubbed in the
written record, and the record is stamped with the consent state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _write_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: object) -> dict[str, Any]:
    """Invoke _write_telemetry_record with a config pointed at tmp_path; return
    the single written record."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools import telemetry as tel

    cfg = TRWConfig(platform_telemetry_enabled=True, logs_dir="logs", telemetry_file="tool-telemetry.jsonl")
    monkeypatch.setattr(tel, "get_config", lambda: cfg)
    monkeypatch.setattr(tel, "resolve_trw_dir", lambda: tmp_path)

    tel._write_telemetry_record(
        "trw_learn",
        args=(),
        kwargs={},
        duration_ms=1.0,
        result=result,
        success=True,
    )

    path = tmp_path / "logs" / "tool-telemetry.jsonl"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    return json.loads(lines[0])


def test_result_summary_email_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _write_record(tmp_path, monkeypatch, {"msg": "contact alice@example.com now"})
    summary = record["result_summary"]
    assert "alice@example.com" not in summary
    assert "<email>" in summary


def test_result_summary_api_key_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _write_record(tmp_path, monkeypatch, "token sk_abcdefghijklmnopqrstuvwxyz123456")
    summary = record["result_summary"]
    assert "sk_abcdefghijklmnopqrstuvwxyz123456" not in summary
    assert "<api_key>" in summary


def test_result_summary_clean_value_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record = _write_record(tmp_path, monkeypatch, {"ok": True, "count": 3})
    # Non-PII content survives (truncated to <=100 chars by the writer).
    assert "count" in record["result_summary"]


def test_detailed_record_is_consent_stamped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The detailed record carries the consent marker so the sender treats it as
    uploadable (it was written under platform consent)."""
    record = _write_record(tmp_path, monkeypatch, {"ok": True})
    assert record.get("_trw_consent") is True
