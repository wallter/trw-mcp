"""PRD-CORE-354 FR03: golden strings for the pure one-line status render."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from trw_mcp.services.status_line import DEGRADED_LINE, FALLBACK_LINE, NO_RUN_LINE, render_status_line

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


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, "TRW ▸ implement · ckpt 12m · build ✓ · review – · deliver – · ✉2"),
        ({"run__state": "none"}, NO_RUN_LINE),
        ({"run__state": "none", "inbox__pending": 0}, NO_RUN_LINE),
        ({"degraded__state": "yes"}, DEGRADED_LINE),
        (
            {"checkpoint__state": "stale", "checkpoint__age_s": 47 * 60, "inbox__pending": 0},
            "TRW ▸ implement · ckpt 47m! · build ✓ · review – · deliver –",
        ),
        ({"checkpoint__age_s": 30, "inbox__pending": 0}, "TRW ▸ implement · ckpt 30s · build ✓ · review – · deliver –"),
        (
            {"checkpoint__age_s": 7300, "inbox__pending": 0},
            "TRW ▸ implement · ckpt 2h · build ✓ · review – · deliver –",
        ),
        (
            {"checkpoint__state": "none", "checkpoint__age_s": None, "inbox__state": "none", "inbox__pending": None},
            "TRW ▸ implement · ckpt – · build ✓ · review – · deliver –",
        ),
        (
            {
                "run__state": "unknown",
                "checkpoint__state": "unknown",
                "evidence__build__state": "unknown",
                "evidence__review__state": "unknown",
                "evidence__deliver__state": "unknown",
                "inbox__state": "unknown",
                "inbox__pending": None,
            },
            "TRW ▸ ? · ckpt ? · build ? · review ? · deliver ? · ✉?",
        ),
        (
            {
                "evidence__build__state": "failed",
                "evidence__review__state": "block",
                "evidence__deliver__state": "called",
                "inbox__pending": 0,
            },
            "TRW ▸ implement · ckpt 12m · build ✗ · review ✗ · deliver ✓",
        ),
        (
            {"evidence__build__scope": "session", "evidence__review__state": "warn", "inbox__pending": 0},
            "TRW ▸ implement · ckpt 12m · build ✓ · review ! · deliver –",
        ),
    ],
)
def test_golden_lines(overrides: dict[str, Any], expected: str) -> None:
    assert render_status_line(_snap(**overrides)) == expected


def test_positive_state_without_run_or_session_scope_is_unknown() -> None:
    """FR02: even a hand-built snapshot cannot produce a tick from aggregate scope."""
    line = render_status_line(_snap(evidence__build__scope="project_aggregate", inbox__pending=0))
    assert "build ?" in line and "✓" not in line


@pytest.mark.parametrize("bad", [None, {}, {"schema_version": 2}, "text", 7])
def test_unusable_input_renders_the_fallback(bad: Any) -> None:
    assert render_status_line(bad) == FALLBACK_LINE


@pytest.mark.parametrize("width", [70, 50, 40, 20, 12, 3])
def test_width_budget(width: int) -> None:
    line = render_status_line(_snap(), width=width)
    assert len(line) <= width and "\n" not in line


def test_width_keeps_the_inbox_doorbell_longest() -> None:
    expected = "TRW ▸ implement · ckpt 12m · build ✓ · ✉2"
    assert render_status_line(_snap(), width=len(expected)) == expected
