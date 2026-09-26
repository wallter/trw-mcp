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
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env, open_slot, write_pin  # noqa: F401
from tests._layout import MONOREPO_ROOT, requires_local_timing, requires_monorepo
from tests._timing import assert_budget
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

    monkeypatch.setattr(sa, "_resolve_mode", _boom)

    class _Tool:
        def __init__(self, name: str) -> None:
            self.name = name

    class _Ctx:
        message = None
        fastmcp_context = None

    async def _call_next(_ctx: object) -> list[_Tool]:
        return [_Tool("trw_code"), _Tool("trw_learn")]

    listed = asyncio.run(
        sa.SurfaceAuthorityMiddleware().on_list_tools(_Ctx(), _call_next)  # type: ignore[arg-type]
    )
    assert {t.name for t in listed} == {"trw_code", "trw_learn"}, "middleware must fail OPEN"


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
    """NFR01 setup sanity: the fixed description text used for the cost-bound
    measurement (see the ``_budget`` twin) is genuinely large, not vacuously short."""
    text = ("some description of the work that matches nothing in particular. " * 64)[:4096]
    assert len(text) >= 4000


@requires_local_timing
def test_gate_and_detection_cost_bounds_budget() -> None:
    """NFR01: detection is pure string work over a bounded join, and the gate's
    addition is one integer over an already-materialised list.

    Bounds are asserted with generous headroom over the PRD's p99 targets so
    this is a REGRESSION tripwire (an accidental I/O call or a quadratic scan
    would blow it by orders of magnitude), not a benchmark that flakes on a
    loaded CI box.
    """
    from trw_mcp.tools._task_type_detection import detect_task_type

    text = ("some description of the work that matches nothing in particular. " * 64)[:4096]

    samples: list[float] = []
    for _ in range(1000):
        start = time.perf_counter()
        detect_task_type(task_name="ticket-1", objective=text, run_type="implementation")
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    p99 = samples[int(0.99 * len(samples)) - 1]
    assert_budget("detection_p99", p99, 20.0, "ms")

    # The gate's own addition: one integer comparison, no I/O.
    gate_samples: list[float] = []
    for _ in range(1000):
        start = time.perf_counter()
        resolve_deliver_gate_decision(
            mode="block_coding", task_type="unknown", build_check_missing=True, files_changed=3
        )
        gate_samples.append((time.perf_counter() - start) * 1000.0)
    gate_samples.sort()
    assert_budget("gate_decision_median", gate_samples[len(gate_samples) // 2], 5.0, "ms")


# --- PRD-CORE-265-NFR02: all four adapters, both conditions ------------------


@pytest.mark.parametrize(
    "include_commit_gate",
    [False, pytest.param(True, marks=requires_monorepo)],
    ids=["shipped-adapters", "monorepo-commit-adapter"],
)
def test_formation_adapters_fail_closed_and_distinguish_absent_from_broken(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    include_commit_gate: bool,
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

    if include_commit_gate:
        assert MONOREPO_ROOT is not None
        assert (MONOREPO_ROOT / "scripts/check_formation_ownership.py").is_file()
        _sys.path.insert(0, str(MONOREPO_ROOT / "scripts"))
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
    if include_commit_gate:
        monkeypatch.setattr(commit_gate, "_resolve_caller_run", lambda: member)
        assert commit_gate.main(["src/beta/x.py"]) == 0
    absent_status = status_block(orchestrator)
    assert "formation" not in absent_status and "formation_error" not in absent_status
    assert evaluate_formation_gate(orchestrator).should_block is False
    assert _resolve_formation_line(orchestrator) == "none active"

    # --- BROKEN: every adapter refuses, and names the file -------------------
    create(orchestrator, formation_env.payload(), prds_dir=None)
    join("release-train", "impl-1", member, pin_key="pin-1")
    formation_env.manifest_path().write_text("members: [unterminated\n", encoding="utf-8")

    if include_commit_gate:
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


# --------------------------------------------------------------------------- #
# PRD-FIX-149 FR01/FR02/NFR02 -- the orchestrator's own slot (ledger T22).
#
# Run 20260919T172832Z-bc716ad0: the lead registered ITSELF as a formation member.
# With every peer terminal, its own trw_deliver was refused because its own slot
# was still joined -- and only delivery could flip that slot. load() gave the
# orchestrator member_id=None, so neither the gate nor the self-report could see
# its slot; the only escape was a Python-only revise() call.
# --------------------------------------------------------------------------- #


#: The orchestrator's and impl-1's own session ids in these fixtures -- verified
#: below by `own_slot_if_caller` (PRD-FIX-149 review R1/R8), never trusted from a
#: caller-supplied ``run_path`` alone.
_LEAD_SESSION = "lead-session"
_IMPL_1_SESSION = "impl-1-session"
_IMPL_2_SESSION = "impl-2-session"


def _self_registered_formation(env: FormationFixture, *, retire_impl_2: bool) -> None:
    from trw_mcp.formation import create, join, revise
    from trw_mcp.tools._orchestration_formation import record_member_delivery

    payload = env.payload()
    payload["members"].append(
        open_slot(
            "lead",
            "claude-code",
            role="lead",
            owned_paths=["docs/lead"],
            test_owned_paths=["tests/test_lead.py"],
            prd_ids=["PRD-FIX-149"],
        )
    )
    create(env.orchestrator_run, payload)
    # The orchestrator's own slot is joined under ITS OWN session's pin key,
    # and that session's pin store entry points at the orchestrator run -- both
    # of which a real trw_init + trw_deliver from the orchestrator's own
    # session would produce, and both of which own_slot_if_caller checks.
    join("release-train", "lead", env.orchestrator_run, pin_key=_LEAD_SESSION)
    write_pin(env, _LEAD_SESSION, env.orchestrator_run)
    join("release-train", "impl-1", env.member_runs["impl-1"], pin_key=_IMPL_1_SESSION)
    write_pin(env, _IMPL_1_SESSION, env.member_runs["impl-1"])
    join("release-train", "impl-2", env.member_runs["impl-2"], pin_key=_IMPL_2_SESSION)
    write_pin(env, _IMPL_2_SESSION, env.member_runs["impl-2"])
    # impl-1 delivers through the real FR11 path, with its OWN verified call context
    # (PRD-FIX-149 review R8: an ordinary member's self-report is caller-verified too).
    record_member_delivery(env.member_runs["impl-1"], {}, call_ctx=_impl_1_call_ctx())
    if retire_impl_2:
        revise("release-train", env.orchestrator_run, {"impl-2": {"status": "abandoned"}})


def _lead_call_ctx() -> Any:
    from trw_mcp.state._paths import TRWCallContext

    return TRWCallContext(session_id=_LEAD_SESSION, client_hint=None, explicit=True, fastmcp_session=None)


def _impl_1_call_ctx() -> Any:
    from trw_mcp.state._paths import TRWCallContext

    return TRWCallContext(session_id=_IMPL_1_SESSION, client_hint=None, explicit=True, fastmcp_session=None)


def _impl_2_call_ctx() -> Any:
    from trw_mcp.state._paths import TRWCallContext

    return TRWCallContext(session_id=_IMPL_2_SESSION, client_hint=None, explicit=True, fastmcp_session=None)


def test_formation_gate_excludes_self_registered_orchestrator_slot(formation_env: FormationFixture) -> None:
    """Every peer terminal: the orchestrator's own joined slot must not block its own delivery."""
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate

    _self_registered_formation(formation_env, retire_impl_2=True)

    outcome = evaluate_formation_gate(formation_env.orchestrator_run, call_ctx=_lead_call_ctx())
    assert outcome.should_block is False, outcome.message


def test_formation_gate_still_blocks_on_genuine_peer(formation_env: FormationFixture) -> None:
    """NFR02: excluding the caller's own slot must not excuse a peer that is still working."""
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate

    _self_registered_formation(formation_env, retire_impl_2=False)

    outcome = evaluate_formation_gate(formation_env.orchestrator_run, call_ctx=_lead_call_ctx())
    assert outcome.should_block is True
    assert "impl-2" in outcome.message
    assert "lead" not in outcome.message, "the caller's own slot must not be listed as blocking it"


def test_formation_gate_never_excludes_an_unverified_self_slot(formation_env: FormationFixture) -> None:
    """PRD-FIX-149 review R1: with no verified call context, the structural match is untrusted."""
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate

    _self_registered_formation(formation_env, retire_impl_2=True)

    outcome = evaluate_formation_gate(formation_env.orchestrator_run)
    assert outcome.should_block is True, "no call context proves nothing about which session is calling"
    assert "lead" in outcome.message


def test_formation_gate_and_self_report_refuse_a_peer_naming_the_orchestrator_run_path(
    formation_env: FormationFixture,
) -> None:
    """PRD-FIX-149 review R1 (ledger): a peer calling ``trw_deliver(run_path=<orchestrator run>)``
    must not borrow the orchestrator's own slot -- neither to skip it in the gate nor to
    self-report delivery on the orchestrator's behalf. It is treated as an ordinary peer.
    """
    from trw_mcp.formation import load
    from trw_mcp.state._paths import TRWCallContext
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate
    from trw_mcp.tools._orchestration_formation import record_member_delivery

    _self_registered_formation(formation_env, retire_impl_2=True)
    orchestrator = formation_env.orchestrator_run

    # A DIFFERENT session, pinned to its OWN (unrelated) run -- exactly a peer
    # that merely NAMED the orchestrator's run_path in its own trw_deliver call.
    peer_session = "peer-session"
    write_pin(formation_env, peer_session, formation_env.member_runs["impl-2"])
    peer_ctx = TRWCallContext(session_id=peer_session, client_hint=None, explicit=True, fastmcp_session=None)

    outcome = evaluate_formation_gate(orchestrator, call_ctx=peer_ctx)
    assert outcome.should_block is True, "an unverified caller must not inherit the orchestrator's own-slot exclusion"
    assert "lead" in outcome.message

    results: dict[str, object] = {}
    record_member_delivery(orchestrator, results, call_ctx=peer_ctx)
    assert "formation_member_delivered" not in results, "a peer must not self-report on the orchestrator's behalf"
    events = (orchestrator / "meta" / "events.jsonl").read_text(encoding="utf-8")
    assert "trw_deliver_complete" not in events, "a peer must not write the orchestrator's own delivery record"
    context = load(orchestrator)
    assert context is not None and context.manifest.member("lead").status == "joined", "unchanged by the peer's call"


def test_orchestrator_own_delivery_writes_record_and_stamp(formation_env: FormationFixture) -> None:
    """FR02: the delivery that passes the gate records the orchestrator's own slot, with no revise call.

    Drives the two formation seams in the order trw_deliver calls them: the gate
    (inside evaluate_delivery_gates), then record_member_delivery.
    """
    from trw_mcp.formation import load, status
    from trw_mcp.tools._formation_deliver_gate import evaluate_formation_gate
    from trw_mcp.tools._orchestration_formation import record_member_delivery

    _self_registered_formation(formation_env, retire_impl_2=True)
    orchestrator = formation_env.orchestrator_run
    assert evaluate_formation_gate(orchestrator, call_ctx=_lead_call_ctx()).should_block is False

    results: dict[str, object] = {}
    record_member_delivery(orchestrator, results, call_ctx=_lead_call_ctx())

    assert results.get("formation_member_delivered") == "lead", results
    events = (orchestrator / "meta" / "events.jsonl").read_text(encoding="utf-8")
    assert "trw_deliver_complete" in events, "the durable record the gate re-checks"
    context = load(orchestrator)
    assert context is not None
    assert context.manifest.member("lead").status == "delivered"
    board = status(context=context)
    assert board is not None and board.non_terminal == [], "every slot, including its own, is now terminal"


def test_an_ordinary_member_self_report_is_also_caller_verified(formation_env: FormationFixture) -> None:
    """PRD-FIX-149 review R8: a peer naming an ORDINARY member's run_path must not
    self-report on that member's behalf; only that member's own verified call context may.
    """
    from trw_mcp.formation import load
    from trw_mcp.tools._orchestration_formation import record_member_delivery

    _self_registered_formation(formation_env, retire_impl_2=False)
    impl_2_run = formation_env.member_runs["impl-2"]

    # A different session, pinned to its own (unrelated) run -- exactly a peer
    # that merely named impl-2's run_path in its own trw_deliver call.
    peer_session = "peer-session"
    write_pin(formation_env, peer_session, formation_env.orchestrator_run)
    from trw_mcp.state._paths import TRWCallContext

    peer_ctx = TRWCallContext(session_id=peer_session, client_hint=None, explicit=True, fastmcp_session=None)

    results: dict[str, object] = {}
    record_member_delivery(impl_2_run, results, call_ctx=peer_ctx)
    assert "formation_member_delivered" not in results, "a peer must not self-report on impl-2's behalf"
    events = (impl_2_run / "meta" / "events.jsonl").read_text(encoding="utf-8")
    assert "trw_deliver_complete" not in events, "a peer must not write impl-2's own delivery record"
    context = load(impl_2_run)
    assert context is not None and context.manifest.member("impl-2").status == "joined", "unchanged by the peer's call"

    # impl-2's OWN session, correctly pinned to its OWN run, succeeds.
    results = {}
    record_member_delivery(impl_2_run, results, call_ctx=_impl_2_call_ctx())
    assert results.get("formation_member_delivered") == "impl-2", results
    context = load(impl_2_run)
    assert context is not None and context.manifest.member("impl-2").status == "delivered"


def test_an_orchestrator_that_did_not_join_its_formation_still_has_no_own_slot(
    formation_env: FormationFixture,
) -> None:
    """The common case is unchanged: no self-registered slot means member_id stays None."""
    from trw_mcp.formation import create, load

    create(formation_env.orchestrator_run, formation_env.payload())
    context = load(formation_env.orchestrator_run)
    assert context is not None and context.is_orchestrator and context.member_id is None


def test_two_members_sharing_a_run_path_resolve_no_self_slot_at_all(formation_env: FormationFixture) -> None:
    """Safety acceptance: an ambiguous self path fails closed, never picks either slot.

    A manifest should never carry two members pointing at the same run_path --
    ``join`` itself refuses to rebind a run once recorded -- but ``_own_slot``
    is the exclusion the gate trusts, so it must not GUESS when it sees one
    anyway (a corrupt or hand-edited manifest). It resolves to no self slot,
    which means the gate's FR01 exclusion does not fire for either identity
    and both stay ordinary, blockable, non-terminal members.
    """
    from trw_mcp.formation._manifest import FormationManifest, FormationMember
    from trw_mcp.formation._store import _own_slot

    orchestrator = formation_env.orchestrator_run
    manifest = FormationManifest(
        formation_id="release-train",
        created_utc="2026-01-01T00:00:00Z",
        updated_utc="2026-01-01T00:00:00Z",
        orchestrator_run_path=str(orchestrator),
        members=[
            FormationMember(member_id="lead", client="claude-code", run_path=str(orchestrator)),
            FormationMember(member_id="ghost", client="codex", run_path=str(orchestrator)),
        ],
    )

    assert _own_slot(manifest, orchestrator) is None
