"""PRD-CORE-249-FR03 — the ``trw_session_start`` open-handoff readback.

Ages are derived from ``first_seen`` at read time; ``total`` is the untruncated
count; an oversized managed block reports ``not_measured`` and is NEVER reported
as zero open items; and the key survives the compact (default) payload.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _project_handoff as ph
from trw_mcp.tools import _project_handoff_readback as rb


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    cfg = TRWConfig()
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
    (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_block(project: Path, rows: list[ph.HandoffRow]) -> Path:
    target = project / ".trw" / "HANDOFF.md"
    target.write_text(ph.HANDOFF_FILE_HEADER + "\n" + ph.render_block(rows) + "\n", encoding="utf-8")
    return target


def _today() -> date:
    """UTC calendar date — the same clock the readback derives ages against."""
    return datetime.now(timezone.utc).date()


def _row(gate_id: str, days_ago: int, run_id: str = "RUN-1") -> ph.HandoffRow:
    return ph.HandoffRow(
        first_seen=(_today() - timedelta(days=days_ago)).isoformat(),
        gate_id=gate_id,
        blocking_class="human-only",
        owner="ops@example",
        run_id=run_id,
        reason="waiting on a human",
    )


def test_age_count_bound_and_compact_survival(project: Path) -> None:
    """FR03: ages, untruncated total, oldest-first cap, and compact survival."""
    _write_block(project, [_row("X-1", 0), _row("X-2", 5), _row("X-3", 40)])
    result = rb.read_open_handoff()
    assert result["status"] == "measured"
    assert result["total"] == 3
    assert [item["age_days"] for item in result["items"]] == [40, 5, 0], "items must be oldest-first"
    assert {item["gate_id"] for item in result["items"]} == {"X-1", "X-2", "X-3"}

    # More rows than the cap: items truncate oldest-first, total stays honest.
    many = [_row(f"X-{i}", i, run_id=f"RUN-{i}") for i in range(1, rb.HANDOFF_READBACK_MAX_ITEMS + 6)]
    _write_block(project, many)
    capped = rb.read_open_handoff()
    assert capped["total"] == len(many)
    assert len(capped["items"]) == rb.HANDOFF_READBACK_MAX_ITEMS
    assert capped["items"][0]["age_days"] == max(r.age_days(_today()) for r in many)

    # Compact (default) trimming keeps the key — the FR03 regression assertion.
    from trw_mcp.tools._session_start_trim import trim_session_start_payload

    payload = {"success": True, "open_handoff": dict(capped), "learnings": []}
    trimmed = trim_session_start_payload(payload, verbose=False)  # type: ignore[arg-type]
    assert trimmed.get("compact") is True
    assert "open_handoff" in trimmed
    assert trimmed["open_handoff"]["total"] == len(many)


def test_oversized_block_is_not_measured_never_zero(project: Path) -> None:
    """FR03: a block over the parse cap reports not_measured, never zero items."""
    target = project / ".trw" / "HANDOFF.md"
    filler = "x" * (rb.HANDOFF_BLOCK_MAX_BYTES + 1024)
    target.write_text(
        f"{ph.HANDOFF_START_MARKER}\n{filler}\n{ph.HANDOFF_END_MARKER}\n",
        encoding="utf-8",
    )
    result = rb.read_open_handoff()
    assert result["status"] == "not_measured"
    assert result["reason"] == "block_exceeds_cap"
    assert "total" not in result, "not_measured must never be reported as zero open items"


def test_absent_file_and_absent_block_are_distinct_from_zero(project: Path) -> None:
    """FR03: no file / no managed block is 'absent', which is not 'measured 0'."""
    assert rb.read_open_handoff()["status"] == "absent"
    (project / ".trw" / "HANDOFF.md").write_text("# just prose\n", encoding="utf-8")
    assert rb.read_open_handoff()["status"] == "absent"
    _write_block(project, [])
    empty = rb.read_open_handoff()
    assert empty["status"] == "measured"
    assert empty["total"] == 0


@pytest.mark.integration
def test_step_is_registered_and_failure_lands_in_degradations(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03 wiring + NFR02: the step is in the real table and fails open."""
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_step_table import SESSION_START_STEPS, SessionStartContext, run_steps

    steps = [s for s in SESSION_START_STEPS if s.key == "handoff_readback"]
    assert steps, "handoff_readback is not registered in SESSION_START_STEPS"
    step = steps[0]
    assert step.critical is False, "the readback must never take down session start"
    # It must run AFTER run_resolve so the project root is settled.
    keys = [s.key for s in SESSION_START_STEPS]
    assert keys.index("handoff_readback") > keys.index("run_resolve")

    _write_block(project, [_row("X-9", 3)])
    sctx = SessionStartContext(query="", config=TRWConfig(), ctx=None, is_focused=False, results={}, errors=[])
    run_steps([step], sctx, _ceremony)
    assert sctx.results["open_handoff"]["total"] == 1

    def _boom() -> None:
        raise RuntimeError("readback exploded")

    monkeypatch.setattr("trw_mcp.tools._project_handoff_readback.step_handoff_readback", _boom)
    failing = SessionStartContext(query="", config=TRWConfig(), ctx=None, is_focused=False, results={}, errors=[])
    run_steps([step], failing, _ceremony)
    assert failing.errors == [], "a readback failure must not become a session-start error"
    assert any(d.get("step") == "handoff_readback" for d in failing.results.get("degradations", []))
