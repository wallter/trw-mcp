"""PRD-FIX-127 FR05: a census gate that observes real durable I/O.

The gate this replaces read the delivery journal's OWN step rows and asserted they
equalled the hand-written list the wiring wrote them from
(``test_delivery_wiring.py``, PRD-CORE-208 FR03). That assertion could only fail if
somebody DELETED a ``step()`` call. The negative-control arm below adds ONE
unjournaled durable write to the live delivery path — the exact defect the gate
advertises — and the old gate stayed green for it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server
from tests._delivery_support import make_uuid7, strong_capability
from trw_mcp.tools._delivery_io_tracer import DurableWriteTrace, trace_durable_writes


def _seed_run(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    for sub in ("learnings/entries", "reflections", "context"):
        (trw_dir / sub).mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n", encoding="utf-8"
    )
    (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return run_dir


def _traced_deliver(tools: dict, tmp_path: Path, run_dir: Path) -> DurableWriteTrace:
    """Run one real trw_deliver (critical path + deferred batch) inside the tracer."""
    trw_dir = tmp_path / ".trw"
    with trace_durable_writes() as trace:
        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        ):
            tools["trw_deliver"].fn(
                allow_unverified=True,
                unverified_reason="test fixture: no build_check recorded for this synthetic run",
                delivery_id=make_uuid7(),
                capability_token=strong_capability(),
            )
        from trw_mcp.tools import _deferred_state as _ds

        thread = _ds._deferred_thread
        if thread is not None:
            thread.join(timeout=60)
    return trace


@pytest.mark.integration
def test_unmodified_deliver_has_no_unexplained_durable_write(tmp_path, monkeypatch) -> None:
    """FR05: every observed durable write is inside a boundary or explicitly exempt."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    trace = _traced_deliver(tools, tmp_path, _seed_run(tmp_path))

    unexplained = trace.unexplained()
    assert unexplained == (), "unjournaled durable writes: " + "; ".join(o.describe() for o in unexplained)

    # Non-vacuity: the tracer really observed the delivery path. If this set were
    # empty the assertion above would pass for the wrong reason.
    credited = trace.by_boundary()
    assert len(trace.observations) > 10
    assert {"S20", "D23"} <= credited, f"tracer credited only {sorted(credited)}"


def _inject_unjournaled_write(trw_dir: Path, results: dict) -> None:
    """Stand-in for a NEW durable delivery mutation somebody forgot to journal."""
    from trw_mcp.state.persistence import FileStateWriter

    FileStateWriter().write_text(trw_dir / "context" / "synthetic-delivery-write.json", "{}\n")


@pytest.mark.integration
def test_unattributed_durable_write_fails_the_census(tmp_path, monkeypatch) -> None:
    """FR05 negative control: one injected unjournaled write turns the gate red.

    This is the arm the old journal-reading gate could not have: it never observed
    I/O, so an ADDED mutation was invisible to it by construction.
    """
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    run_dir = _seed_run(tmp_path)
    with patch(
        "trw_mcp.tools._ceremony_deliver_tool._attach_deliver_ceremony_status",
        _inject_unjournaled_write,
    ):
        trace = _traced_deliver(tools, tmp_path, run_dir)

    unexplained = trace.unexplained()
    assert unexplained, "the census gate stayed green on an injected unjournaled write"
    described = " ".join(obs.describe() for obs in unexplained)
    assert "synthetic-delivery-write.json" in described  # the gate NAMES the write
    assert (tmp_path / ".trw" / "context" / "synthetic-delivery-write.json").exists()


def test_tracer_uninstalls_every_seam_on_exit(tmp_path) -> None:
    """Risk control: a leaked patch would follow the writer classes into later tests."""
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter

    before = {
        "write_yaml": FileStateWriter.write_yaml,
        "append_jsonl": FileStateWriter.append_jsonl,
        "write_text": FileStateWriter.write_text,
        "ensure_dir": FileStateWriter.ensure_dir,
        "log_event": FileEventLogger.log_event,
        "connect": sqlite3.connect,
    }
    with trace_durable_writes() as trace:
        FileStateWriter().write_text(tmp_path / "probe.txt", "x")
        assert any(obs.seam == "write_text" for obs in trace.observations)
        assert FileStateWriter.write_text is not before["write_text"]

    assert FileStateWriter.write_yaml is before["write_yaml"]
    assert FileStateWriter.append_jsonl is before["append_jsonl"]
    assert FileStateWriter.write_text is before["write_text"]
    assert FileStateWriter.ensure_dir is before["ensure_dir"]
    assert FileEventLogger.log_event is before["log_event"]
    assert sqlite3.connect is before["connect"]


def test_read_only_sqlite_commits_are_not_recorded_as_writes(tmp_path) -> None:
    """A commit that changed no row is not a durable write; recording it buries the real ones."""
    db = tmp_path / "probe.sqlite3"
    with trace_durable_writes() as trace:
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.commit()
        mutating = len([o for o in trace.observations if o.seam == "sqlite_commit"])
        conn.execute("SELECT * FROM t").fetchall()
        conn.commit()  # read-only: total_changes did not advance
        assert len([o for o in trace.observations if o.seam == "sqlite_commit"]) == mutating
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        assert len([o for o in trace.observations if o.seam == "sqlite_commit"]) == mutating + 1
        conn.close()


def test_journal_projection_no_longer_claims_to_be_a_tracer() -> None:
    """FR05(a)/FR07: the demoted reader is named and documented for what it is."""
    from trw_mcp.tools import _delivery_tracer

    source = Path(str(_delivery_tracer.__file__)).read_text(encoding="utf-8")
    assert not hasattr(_delivery_tracer, "trace_journaled_effects")
    assert hasattr(_delivery_tracer, "read_journaled_step_ids")
    assert "cannot lie about what the dispatcher touched" not in source
    assert "zero-instrumentation tracer" not in _delivery_tracer.read_journaled_step_ids.__doc__
