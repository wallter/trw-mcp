"""PRD-CORE-354 FR03: golden strings for the pure one-line status render."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.services.status_line import DEGRADED_LINE, FALLBACK_LINE, NO_RUN_LINE, render_status_line

pytestmark = pytest.mark.unit

_GOLDEN_TS = (
    Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "claude-mods" / "trw-ui" / "label-golden.ts"
)
_AS_OF = "2026-10-04T12:00:00+00:00"


def _snap(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": _AS_OF,
        "session_id": "s",
        "client": "claude-code",
        "run": {
            "state": "ok",
            "run_id": "r",
            "task": "t",
            "phase": "implement",
            "status": "active",
            "run_path": "/x",
            "as_of": _AS_OF,
        },
        "checkpoint": {"state": "ok", "count": 3, "last_ts": _AS_OF, "age_s": 720, "scope": "run", "as_of": _AS_OF},
        "evidence": {
            "build": {"state": "passed", "scope": "run", "ts": None, "test_count": 10, "build_scope": "full"},
            "review": {"state": "none", "scope": "run", "ts": None},
            "deliver": {"state": "none", "scope": "run", "ts": None},
            "as_of": _AS_OF,
        },
        "gate_preview": {"state": "ready", "summary": "READY", "preview": True, "as_of": _AS_OF},
        "project_aggregate": {
            "build_check_result": None,
            "review_verdict": None,
            "deliver_called": None,
            "scope": "project_aggregate",
        },
        "inbox": {"state": "ok", "pending": 2, "formation_id": "f", "as_of": _AS_OF},
        "degraded": {"state": "no"},
        "unknown": [],
    }
    snap = copy.deepcopy(base)
    for dotted, value in overrides.items():
        target = snap
        *parents, leaf = dotted.split("__")
        for key in parents:
            target = target[key]
        target[leaf] = value
    return snap


_FAIL = {"evidence__build__state": "failed", "evidence__review__state": "block"}
_NOBOX = {"inbox__pending": 0}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "TRW ▸ implement · t · ✉2"),
        ({"run__task": ""}, "TRW ▸ implement · ✉2"),
        ({"run__state": "none"}, NO_RUN_LINE),
        ({"run__state": "none", "inbox__pending": 0}, "TRW"),
        ({"degraded__state": "yes"}, DEGRADED_LINE),
        ({"run__state": "unknown"}, "TRW ?"),
        ({"run__state": "unknown", "inbox__pending": 5}, "TRW ?"),
        # No checkpoint age or positive/none ticks, whatever the checkpoint says.
        ({"checkpoint__state": "stale", "checkpoint__age_s": 47 * 60, **_NOBOX}, "TRW ▸ implement · t"),
        ({"checkpoint__state": "none", "checkpoint__age_s": None, **_NOBOX}, "TRW ▸ implement · t"),
        ({"evidence__review__state": "warn", **_NOBOX}, "TRW ▸ implement · t"),
        ({**_FAIL, **_NOBOX}, "TRW ▸ implement · t · build ✗ · review ✗"),
        ({"evidence__build__state": "failed"}, "TRW ▸ implement · t · build ✗ · ✉2"),
        ({"evidence__deliver__state": "called", **_NOBOX}, "TRW ✓ t"),
        ({"evidence__deliver__state": "called", **_FAIL}, "TRW ✓ t · build ✗ · review ✗ · ✉2"),
        ({"run__task": "x" * 40, **_NOBOX}, "TRW ▸ implement · " + "x" * 23 + "…"),
        ({"run__task": "  two   words ", **_NOBOX}, "TRW ▸ implement · two words"),
        ({"run__phase": None, **_NOBOX}, "TRW ▸ ? · t"),
    ],
)
def test_golden_lines(overrides: dict[str, Any], expected: str) -> None:
    assert render_status_line(_snap(**overrides)) == expected


def test_unscoped_evidence_never_surfaces() -> None:
    """FR02: a failure scoped to the project aggregate is not this run's failure."""
    line = render_status_line(_snap(**_FAIL, evidence__build__scope="project_aggregate", evidence__review__scope="x"))
    assert "✗" not in line


def test_deliver_unscoped_is_not_delivered() -> None:
    line = render_status_line(_snap(evidence__deliver__state="called", evidence__deliver__scope="project_aggregate"))
    assert "✓" not in line


@pytest.mark.parametrize("bad", [None, {}, {"schema_version": 2}, "text", 7])
def test_unusable_input_renders_the_fallback(bad: Any) -> None:
    assert render_status_line(bad) == FALLBACK_LINE


@pytest.mark.parametrize("width", [70, 50, 30, 20, 12, 3])
def test_width_budget(width: int) -> None:
    line = render_status_line(_snap(**_FAIL), width=width)
    assert len(line) <= width and "\n" not in line


def test_width_drops_the_task_first_and_keeps_exceptions() -> None:
    snap = _snap(run__task="a long task name here", **_FAIL)
    full = "TRW ▸ implement · a long task name here · build ✗ · review ✗ · ✉2"
    assert render_status_line(snap) == full
    assert render_status_line(snap, width=len(full)) == full
    assert render_status_line(snap, width=len(full) - 1) == "TRW ▸ implement · build ✗ · review ✗ · ✉2"
    assert render_status_line(snap, width=20).endswith("…")


def _golden_cases() -> list[dict[str, Any]]:
    text = _GOLDEN_TS.read_text(encoding="utf-8").split("export const GOLDEN = ", 1)[1].strip().rstrip(";")
    cases: list[dict[str, Any]] = json.loads(text)["cases"]
    return cases


@pytest.mark.parametrize("case", _golden_cases(), ids=lambda c: str(c["name"]))
def test_shared_golden_table_parity(case: dict[str, Any]) -> None:
    """The Python renderer and the trw-ui mod must produce identical strings."""
    assert render_status_line(case["snapshot"], case.get("width")) == case["expect"]
