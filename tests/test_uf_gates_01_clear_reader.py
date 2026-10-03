"""UF-GATES-01: the CLEAR scorer reads the rows the emitters persist, and says so when it cannot.

``HPOTelemetryEvent`` is ``strict=True``. Strict validation of a Python dict rejects the ISO string a JSONL row
stores for ``ts``, so ``cls.model_validate(json.loads(line))`` refused every persisted row (0 of 2204 in the audit,
0 of 1036 in a live r8 run) and an ``except: continue`` hid it: every delivered run silently got no CLEAR score.
Strict JSON-mode validation accepts the ISO string, which is what the reader now uses. A row that still fails is
counted and logged at warning, and the deliver step's own failure is logged at warning, not debug.
"""

from __future__ import annotations

from pathlib import Path

from structlog.testing import capture_logs


def _write_events(run_dir: Path, lines: list[str]) -> None:
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "events-2026-10-01.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _emitted_rows() -> list[str]:
    """Rows exactly as the emitters write them (``event.model_dump_json()``)."""
    from trw_mcp.telemetry.event_base import HPOSessionStartEvent, ToolCallEvent

    return [
        HPOSessionStartEvent(session_id="s1").model_dump_json(),
        ToolCallEvent(session_id="s1", payload={"tool": "trw_recall", "duration_ms": 12, "ok": True}).model_dump_json(),
    ]


def test_persisted_rows_are_scored(tmp_path: Path) -> None:
    from trw_mcp.scoring.clear import load_and_score_run

    _write_events(tmp_path, _emitted_rows())
    assert load_and_score_run("s1", tmp_path) is not None


def test_a_rejected_row_is_counted_and_logged_at_warning(tmp_path: Path) -> None:
    from trw_mcp.scoring.clear import load_and_score_run

    _write_events(tmp_path, [*_emitted_rows(), '{"event_type": "tool_call", "session_id": "s1", "bogus": 1}'])
    with capture_logs() as logs:
        score = load_and_score_run("s1", tmp_path)
    assert score is not None  # the good rows still score
    rejected = [e for e in logs if e["event"] == "clear_rows_rejected"]
    assert rejected and rejected[0]["log_level"] == "warning"
    assert rejected[0]["rejected"] == 1 and rejected[0]["read"] == 3


def test_the_deliver_step_logs_its_own_failure_at_warning(tmp_path: Path, monkeypatch: object) -> None:
    import pytest

    from trw_mcp.tools import _ceremony_deliver_steps as steps

    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("scorer broke")

    mp = pytest.MonkeyPatch()
    mp.setattr("trw_mcp.scoring.clear.load_and_score_run", _boom)
    try:
        with capture_logs() as logs:
            steps.step_clear_score(tmp_path, {})  # type: ignore[arg-type]
    finally:
        mp.undo()
    failed = [e for e in logs if e["event"] == "clear_score_step_failed"]
    assert failed and failed[0]["log_level"] == "warning"
