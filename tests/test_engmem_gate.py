"""PRD-CORE-292 FR06: the engmem gate goes red on leaks and on vacuous results.

Mutation controls over synthetic harness output: a forbidden row, an all-empty
entry point, a missing positive control and a per-query regression each fail it;
a clean run passes. The gate reads files, so none of this runs a model.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

_GATE_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "engmem_gate.py"
_spec = importlib.util.spec_from_file_location("engmem_gate", _GATE_PATH)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["engmem_gate"] = gate
_spec.loader.exec_module(gate)


def _row(
    qid: str, *, hit: bool = True, top10: list[str] | None = None, forbidden: list[str] | None = None
) -> dict[str, Any]:
    top = top10 if top10 is not None else (["G-" + qid] if hit else [])
    forb = forbidden or []
    return {
        "qid": qid,
        "top10": top,
        "gold": ["G-" + qid],
        "forbidden": forb,
        "hit@5": float(hit),
        "complete@5": float(hit),
        "hit@10": float(hit),
        "complete@10": float(hit),
        "forbidden@10": float(any(r in forb for r in top)),
    }


def _run(
    mcp: list[dict[str, Any]], library: list[dict[str, Any]] | None = None, arm: str = "trw-mcp-recall"
) -> dict[str, Any]:
    lib = library if library is not None else [_row(r["qid"]) for r in mcp]
    return {"_per_query": {"trw-hybrid": lib, arm: mcp}}


BASELINE = _run([_row("q1"), _row("q2", hit=False)])


def test_a_clean_run_passes() -> None:
    assert gate.check(BASELINE, _run([_row("q1"), _row("q2")]), "recall") == []


def test_a_forbidden_row_fails() -> None:
    leaked = _row("q1", top10=["G-q1", "OLD"], forbidden=["OLD"])
    reasons = gate.check(BASELINE, _run([leaked, _row("q2")]), "recall")
    assert any("forbidden" in r and "OLD" in r for r in reasons), reasons


def test_an_all_empty_entry_point_fails() -> None:
    empty = [_row("q1", hit=False), _row("q2", hit=False)]
    reasons = gate.check(BASELINE, _run(empty, library=[_row("q1"), _row("q2")]), "recall")
    assert any("returned nothing" in r for r in reasons), reasons


def test_no_positive_control_fails() -> None:
    misses = [_row("q1", hit=False), _row("q2", hit=False)]
    reasons = gate.check(BASELINE, _run([_row("q1"), _row("q2")], library=misses), "recall")
    assert any("positive control" in r for r in reasons), reasons


@pytest.mark.parametrize("regressed", ["baseline", "library"])
def test_a_per_query_regression_fails(regressed: str) -> None:
    candidate = _run([_row("q1", hit=False), _row("q2")])
    reasons = gate.check(BASELINE, candidate, "recall")
    needle = "frozen baseline" if regressed == "baseline" else "against the library"
    assert any(needle in r for r in reasons), reasons


def test_missing_and_skipped_queries_fail(tmp_path: Path) -> None:
    assert gate.check(BASELINE, _run([]), "recall") == ["trw-mcp-recall: no executed queries"]
    reasons = gate.check(BASELINE, _run([_row("q1")]), "recall")
    assert any("not executed" in r and "q2" in r for r in reasons), reasons


def test_a_missing_file_fails_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "base").mkdir()
    (tmp_path / "cand").mkdir()
    (tmp_path / "base" / "base-recall-1000.json").write_text(json.dumps(BASELINE))
    argv = [
        "gate",
        "--baseline",
        str(tmp_path / "base"),
        "--candidate",
        str(tmp_path / "cand"),
        "--sizes",
        "1000",
        "--entries",
        "recall",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert gate.main() == 1
    assert "missing (candidate)" in capsys.readouterr().out


def _ranked(qid: str, gold_rank: int, shown: int) -> dict[str, Any]:
    """The library's order with the gold row at *gold_rank*, cut to *shown* rows."""
    top = [f"D{index}" for index in range(1, 11)]
    top[gold_rank - 1] = "G-" + qid
    row = _row(qid, top10=top[:shown])
    for depth in (5, 10):  # recorded as the harness records them, from what was shown
        row[f"hit@{depth}"] = row[f"complete@{depth}"] = float("G-" + qid in top[: min(depth, shown)])
    return row


def _session(mcp: list[dict[str, Any]], library: list[dict[str, Any]]) -> dict[str, Any]:
    return _run(mcp, library=library, arm="trw-mcp-session-start")


def test_session_start_is_judged_at_the_stubs_it_shows() -> None:
    """PRD-CORE-294 FR02 caps session start; a gold row ranked below the cap is not a regression."""
    from trw_mcp.tools._recall_presenter import SESSION_MAX_STUBS

    below_cap = SESSION_MAX_STUBS + 1
    library = [_ranked("q1", below_cap, 10)]
    mcp = [_ranked("q1", below_cap, SESSION_MAX_STUBS)]

    assert gate.check(_session(mcp, library), _session(mcp, library), "session-start") == []


def test_session_start_losing_a_row_it_should_show_fails() -> None:
    from trw_mcp.tools._recall_presenter import SESSION_MAX_STUBS

    library = [_ranked("q1", SESSION_MAX_STUBS, 10)]
    short = [_ranked("q1", SESSION_MAX_STUBS, SESSION_MAX_STUBS - 1)]

    reasons = gate.check(_session(library, library), _session(short, library), "session-start")

    assert any("against the library" in r for r in reasons), reasons
    assert any("frozen baseline" in r for r in reasons), reasons
