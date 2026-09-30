"""CODEX-P0-A S3: ``trw-mcp doctor`` says whether codex's own run record is readable on this machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _rollout(sessions: Path, name: str, turn: dict[str, object] | None) -> None:
    day = sessions / "2026" / "09" / "29"
    day.mkdir(parents=True, exist_ok=True)
    lines: list[dict[str, object]] = [{"type": "session_meta", "payload": {"cli_version": "0.99.1"}}]
    if turn is not None:
        lines.append({"type": "turn_context", "payload": turn})
    (day / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_no_sessions_directory_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "absent"))
    status, message = codex_observation_row()
    assert status == "SKIP"
    assert "unknown" in message


def test_a_readable_rollout_passes_with_the_cli_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(tmp_path / "sessions", "rollout-2026-09-29T01-00-00-a.jsonl", {"model": "m", "effort": "low"})
    status, message = codex_observation_row()
    assert status == "PASS"
    assert "0.99.1" in message


def test_the_newest_rollout_without_the_keys_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _rollout(tmp_path / "sessions", "rollout-2026-09-29T01-00-00-a.jsonl", {"model": "m", "effort": "low"})
    _rollout(tmp_path / "sessions", "rollout-2026-09-29T02-00-00-b.jsonl", {"cwd": "/x"})  # newer, format drift
    status, message = codex_observation_row()
    assert status == "WARN"
    assert "effort" in message or "model" in message


def test_the_row_is_registered_last_in_the_doctor_catalogue() -> None:
    from trw_mcp.server._doctor_checks_registry import CHECKS

    assert CHECKS[-1] == ("codex_observation", "_check_codex_observation")
