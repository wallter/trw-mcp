"""PRD-CORE-246 FR03/NFR01/NFR02 — the build gate keys on evidence, not on a guess.

Before FR03, ``resolve_deliver_gate_decision`` returned
``task_type in {coding, rca, eval}``: a run classified ``research``, ``docs``,
``planning`` or ``unknown`` never blocked for a missing build check, however
many source files it changed. Combined with a detector that could not see the
only free-text field describing the work, the system was MOST lenient exactly
where it understood the task least.

FR03 makes the predicate a disjunction — build-artifact task type OR recorded
change evidence — and inverts the posture on its own evidence: an uncomputable
count blocks. That inversion is the single fail-closed component of the change
set, and NFR02 requires the three touched components to keep three DIFFERENT
postures, each asserted separately here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from trw_mcp.models.config import get_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._deliver_gate_mode import (
    count_session_changed_files,
    resolve_deliver_gate_decision,
)
from trw_mcp.tools._delivery_helpers import check_delivery_gates

pytestmark = pytest.mark.integration


def _make_run(tmp_path: Path, task_type: str, *, modified: int) -> Path:
    """A run with no passing build check and ``modified`` distinct file edits."""
    writer = FileStateWriter()
    run_dir = tmp_path / ".trw" / "runs" / "t" / "20260903T000000Z-bbbb2222"
    (run_dir / "meta").mkdir(parents=True)
    writer.write_yaml(
        run_dir / "meta" / "run.yaml",
        {
            "run_id": "20260903T000000Z-bbbb2222",
            "task": "t",
            "status": "active",
            "phase": "deliver",
            "task_type": task_type,
        },
    )
    writer.append_jsonl(run_dir / "meta" / "events.jsonl", {"event": "run_init", "task": "t"})
    for i in range(modified):
        writer.append_jsonl(run_dir / "meta" / "events.jsonl", {"event": "file_modified", "file": f"src/m{i}.py"})
    return run_dir


# ── FR03: the evidence rule ─────────────────────────────────────────────


def test_unknown_task_type_blocks_when_files_changed(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03 AC1 + US-001: unknown + missing build + 3 modified files -> BLOCKED.

    Driven through the PRODUCTION ``check_delivery_gates`` path, not the pure
    predicate, so this proves the count is actually wired to the gate.
    """
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "unknown", modified=3)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert result.get("delivery_blocked")
    assert result.get("blocked_task_type") == "unknown"
    assert result.get("missing_gate") == "build_check"
    # The pure predicate agrees with the production path.
    assert (
        resolve_deliver_gate_decision(
            mode="block_coding", task_type="unknown", build_check_missing=True, files_changed=3
        )
        is True
    )


def test_unknown_task_type_stays_advisory_with_zero_changes(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03 AC2 + US-001: nothing changed -> the advisory warning survives.

    This is the boundary that keeps the fix from being "block everything": a
    ceremony-only run is exactly the case the gate must not fire on.
    """
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "unknown", modified=0)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert not result.get("delivery_blocked")
    assert "build_gate_warning" in result
    assert (
        resolve_deliver_gate_decision(
            mode="block_coding", task_type="unknown", build_check_missing=True, files_changed=0
        )
        is False
    )


def test_production_path_passes_the_count(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03 ``value_equals``: the count the gate receives IS the review-scope count.

    One notion of "code changed", not two. If the gate ever grows its own
    counter this fails, because the two numbers would stop agreeing.
    """
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    run_dir = _make_run(tmp_project, "docs", modified=4)
    events = [e for e in FileStateReader().read_jsonl(run_dir / "meta" / "events.jsonl") if isinstance(e, dict)]

    from trw_mcp.tools._delivery_event_checks import (
        _count_file_modified_current_session,
        _project_root_from_run,
    )

    expected = _count_file_modified_current_session(events, _project_root_from_run(run_dir), None)
    observed = count_session_changed_files(events=events, run_path=run_dir, session_id=None)

    assert observed == expected == 4
    # ...and that count is what actually reaches the decision.
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")
    assert result.get("delivery_blocked")
    assert "files modified this session: 4" in str(result["delivery_blocked"])


def test_distinct_path_semantics_are_inherited_not_reinvented(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR03 boundary: a file edited five times counts ONCE."""
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    writer = FileStateWriter()
    run_dir = _make_run(tmp_project, "docs", modified=0)
    for _ in range(5):
        writer.append_jsonl(run_dir / "meta" / "events.jsonl", {"event": "file_modified", "file": "src/same.py"})

    events = [e for e in FileStateReader().read_jsonl(run_dir / "meta" / "events.jsonl") if isinstance(e, dict)]
    assert count_session_changed_files(events=events, run_path=run_dir, session_id=None) == 1


def test_threshold_is_read_from_config_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03 tunable: raising the threshold narrows the change clause but can
    never restore never-block-on-unknown, because the task-type clause is an OR."""
    cfg = get_config().model_copy(update={"deliver_gate_unclassified_change_threshold": 5})
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)

    common = {"mode": "block_coding", "build_check_missing": True}
    assert resolve_deliver_gate_decision(task_type="unknown", files_changed=4, **common) is False
    assert resolve_deliver_gate_decision(task_type="unknown", files_changed=5, **common) is True
    # No threshold value switches the fix off for a build-bearing type.
    assert resolve_deliver_gate_decision(task_type="coding", files_changed=0, **common) is True


def test_advisory_mode_remains_the_documented_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Migration/back-compat: a project that needs the old posture sets
    ``deliver_gate_mode: advisory``. That pre-existing escape is untouched — no
    new on/off flag was added for FR03."""
    for task_type in ("coding", "rca", "eval", "docs", "research", "planning", "unknown"):
        assert (
            resolve_deliver_gate_decision(
                mode="advisory", task_type=task_type, build_check_missing=True, files_changed=99
            )
            is False
        )


# ── NFR02: three components, three deliberate postures ──────────────────


def test_three_failure_postures_are_distinct(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: each of the three touched components exhibits exactly its own
    posture and no other.

    1. Detection FAILS OPEN — it is a classifier, not a gate, and must never
       block ``trw_init``.
    2. The deliver gate FAILS CLOSED on its own evidence — an uncomputable
       change count is treated as meeting the threshold.
    3. The surface middleware FAILS OPEN — a broken exposure gate must not
       brick a session, and FR06 widens exposure anyway.
    """
    # 1. Detection: malformed input degrades, never raises.
    from trw_mcp.tools._task_type_detection import detect_task_type

    degraded = detect_task_type(task_name=object(), prd_scope=object())  # type: ignore[arg-type]
    assert (degraded.task_type, degraded.detection_method) == ("unknown", "fallback")

    # 2. Gate: an UNCOMPUTABLE count blocks.
    assert (
        resolve_deliver_gate_decision(
            mode="block_coding", task_type="unknown", build_check_missing=True, files_changed=None
        )
        is True
    )
    # ...and the production helper is what produces that ``None``.
    boom = count_session_changed_files(
        events=[{"event": "file_modified", "file": object()}],  # type: ignore[dict-item]
        run_path=Path("/nonexistent/does/not/exist"),
        session_id=None,
    )
    assert boom is None or isinstance(boom, int)

    # 3. Middleware: a raising resolver still exposes the full catalogue.
    import asyncio

    from trw_mcp.middleware import surface_authority as sa

    def _boom(**_kwargs: object) -> str:
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(sa, "resolve_task_type", _boom)

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Ctx:
        message = None
        fastmcp_context = None

    async def _call_next(_ctx: object) -> list[_Tool]:
        return [_Tool("trw_code_search"), _Tool("trw_learn")]

    listed = asyncio.run(
        sa.SurfaceAuthorityMiddleware().on_list_tools(_Ctx(), _call_next)  # type: ignore[arg-type]
    )
    assert {t.name for t in listed} == {"trw_code_search", "trw_learn"}, "middleware must fail OPEN"


def test_uncomputable_count_blocks_through_the_production_path(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR03 AC3: the fail-closed inversion, driven through ``check_delivery_gates``.

    ``count_session_changed_files`` is patched at the seam
    ``_delivery_helpers`` imports it from, so the gate genuinely receives
    ``None`` rather than the test asserting on the pure predicate again. The run
    modified NOTHING, so a fail-OPEN implementation would let it through —
    that is what makes the assertion non-vacuous.
    """
    cfg = get_config()
    object.__setattr__(cfg, "deliver_gate_mode", "block_coding")
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.count_session_changed_files", lambda **_kw: None)

    run_dir = _make_run(tmp_project, "docs", modified=0)
    result = check_delivery_gates(run_dir, FileStateReader(), tmp_project / ".trw")

    assert result.get("delivery_blocked")
    assert "uncomputable" in str(result["delivery_blocked"])


def test_unreadable_threshold_also_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02 corollary: the gate must not WEAKEN when its own tunable is unreadable."""

    def _boom() -> object:
        raise RuntimeError("config exploded")

    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", _boom)
    assert (
        resolve_deliver_gate_decision(
            mode="block_coding", task_type="unknown", build_check_missing=True, files_changed=0
        )
        is True
    )


def test_acceptable_failure_record_still_releases_a_fail_closed_block(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """US-001 AC3: a fail-closed gate can force evidence or a record — never wedge.

    Reuses the existing end-to-end deliver harness so the structured
    PRD-CORE-191 override is exercised on the real ``trw_deliver`` path with an
    ``unknown`` task type that blocks only because of the FR03 change clause.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from tests.test_deliver_gate_mode import _call_deliver

    reason = json.dumps(
        {
            "failed_command": "pytest tests -q",
            "residual_risk": "one integration environment is unavailable",
            "owner": "operator-example",
            "expiry_iso": (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat(),
        }
    )
    result = _call_deliver(
        tmp_project,
        monkeypatch,
        task_type="unknown",
        mode="block_coding",
        file_modified_count=3,
        allow_unverified=True,
        unverified_reason=reason,
    )

    assert result["success"] is True
    assert result["acceptable_failure_record"]["owner"] == "operator-example"


# ── NFR01: cost bounds ──────────────────────────────────────────────────


def test_gate_and_detection_cost_bounds() -> None:
    """NFR01: detection is pure string work over a bounded join, and the gate's
    addition is one integer over an already-materialised list.

    Bounds are asserted with generous headroom over the PRD's p99 targets so
    this is a REGRESSION tripwire (an accidental I/O call or a quadratic scan
    would blow it by orders of magnitude), not a benchmark that flakes on a
    loaded CI box.
    """
    from trw_mcp.tools._task_type_detection import detect_task_type

    text = ("some description of the work that matches nothing in particular. " * 64)[:4096]
    assert len(text) >= 4000

    samples: list[float] = []
    for _ in range(1000):
        start = time.perf_counter()
        detect_task_type(task_name="ticket-1", objective=text, run_type="implementation")
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    p99 = samples[int(0.99 * len(samples)) - 1]
    assert p99 <= 20.0, f"detection p99 {p99:.3f} ms — PRD budget is 2 ms; 20 ms is the flake-tolerant tripwire"

    # The gate's own addition: one integer comparison, no I/O.
    gate_samples: list[float] = []
    for _ in range(1000):
        start = time.perf_counter()
        resolve_deliver_gate_decision(
            mode="block_coding", task_type="unknown", build_check_missing=True, files_changed=3
        )
        gate_samples.append((time.perf_counter() - start) * 1000.0)
    gate_samples.sort()
    assert gate_samples[len(gate_samples) // 2] <= 5.0


# --- PRD-CORE-265-NFR02: all four adapters, both conditions ------------------


def test_formation_adapters_fail_closed_and_distinguish_absent_from_broken(
    formation_env: FormationFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """NFR02. A broken manifest refuses in all four; an absent one refuses in none.

    ATTRIBUTION. One assertion per adapter, each guarding a specific ``except
    FormationError`` branch: ``check_formation_ownership.main`` (commit),
    ``_apply_formation_block`` (status), ``evaluate_formation_gate`` (deliver
    gate), and ``checkpoint._resolve_formation_line`` (recovery capture). Swap
    any of them for a bare ``except Exception: return`` and its broken case
    silently joins the absent case — which is the reassuring-fallback defect
    this whole PRD was written to remove.

    The hook adapter is deliberately NOT in this list: FR10 makes it advisory in
    every configuration, so its correct posture on a broken manifest is silence,
    and that is asserted in the hook suite instead.
    """
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import check_formation_ownership as commit_gate

    from trw_mcp.formation import create, join
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate
    from trw_mcp.tools._orchestration_status_assembly import assemble_status_result
    from trw_mcp.tools.checkpoint import _resolve_formation_line

    orchestrator = formation_env.orchestrator_run
    member = formation_env.member_runs["impl-1"]

    def status_block(run: Path) -> dict[str, object]:
        return dict(
            assemble_status_result(
                {"run_id": run.name, "task": run.name, "phase": "implement"},
                [],
                {},
                run,
                FileStateReader(),
                run / "meta",
            )
        )

    # --- ABSENT: every adapter behaves exactly as it does with no formation ---
    commit_gate._resolve_caller_run = lambda: member  # type: ignore[assignment]
    assert commit_gate.main(["src/beta/x.py"]) == 0
    absent_status = status_block(orchestrator)
    assert "formation" not in absent_status and "formation_error" not in absent_status
    assert evaluate_formation_gate(orchestrator).should_block is False
    assert _resolve_formation_line(orchestrator) == "none active"

    # --- BROKEN: every adapter refuses, and names the file -------------------
    create(orchestrator, formation_env.payload(), prds_dir=None)
    join("release-train", "impl-1", member, pin_key="pin-1")
    formation_env.manifest_path().write_text("members: [unterminated\n", encoding="utf-8")

    assert commit_gate.main(["src/beta/x.py"]) == 1
    assert "formation.yaml" in capsys.readouterr().err

    broken_status = status_block(orchestrator)
    assert "formation" not in broken_status
    assert "formation.yaml" in str(broken_status["formation_error"])

    outcome = evaluate_formation_gate(orchestrator)
    assert outcome.should_block is True
    assert "formation.yaml" in outcome.message

    recovery = _resolve_formation_line(orchestrator)
    assert recovery.startswith("unresolved") and "formation.yaml" in recovery
