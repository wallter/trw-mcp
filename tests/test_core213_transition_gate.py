"""PRD-CORE-213 FR04-FR05 — PRD status->implemented transition detection + gate.

FR04: ``detect_status_transitions`` parses a path-limited PRD diff.
FR05: ``check_transition_coherence`` enumerates the missing coherence items; the
deliver-gate dispatch blocks (STRUCTURED override) when they are unmet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools import _prd_transition_gate as tg

# --------------------------------------------------------------------------- #
# FR04 — transition detection (pure)
# --------------------------------------------------------------------------- #

_DIFF_IMPLEMENTED = """diff --git a/docs/requirements-aare-f/prds/PRD-CORE-999.md b/docs/requirements-aare-f/prds/PRD-CORE-999.md
--- a/docs/requirements-aare-f/prds/PRD-CORE-999.md
+++ b/docs/requirements-aare-f/prds/PRD-CORE-999.md
@@ -1,4 +1,4 @@
-  status: draft
+  status: implemented
"""


def test_detects_implemented_transition() -> None:
    assert tg.detect_status_transitions(_DIFF_IMPLEMENTED) == ["PRD-CORE-999"]


def test_prose_only_change_is_not_a_transition() -> None:
    diff = """diff --git a/docs/requirements-aare-f/prds/PRD-CORE-999.md b/docs/requirements-aare-f/prds/PRD-CORE-999.md
--- a/docs/requirements-aare-f/prds/PRD-CORE-999.md
+++ b/docs/requirements-aare-f/prds/PRD-CORE-999.md
@@ -10,3 +10,3 @@
-Some old prose.
+Some new prose describing status of the world.
"""
    assert tg.detect_status_transitions(diff) == []


def test_out_of_dir_status_edit_ignored() -> None:
    diff = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
+  status: implemented
"""
    assert tg.detect_status_transitions(diff) == []


def test_implemented_family_aliases_detected() -> None:
    for alias in ("done", "delivered", "complete"):
        diff = _DIFF_IMPLEMENTED.replace("implemented", alias)
        assert tg.detect_status_transitions(diff) == ["PRD-CORE-999"], alias


def test_removed_status_line_not_a_transition() -> None:
    diff = """diff --git a/docs/requirements-aare-f/prds/PRD-CORE-999.md b/docs/requirements-aare-f/prds/PRD-CORE-999.md
--- a/docs/requirements-aare-f/prds/PRD-CORE-999.md
+++ b/docs/requirements-aare-f/prds/PRD-CORE-999.md
@@ -1,4 +1,4 @@
-  status: implemented
+  status: draft
"""
    assert tg.detect_status_transitions(diff) == []


# --------------------------------------------------------------------------- #
# FR05 — coherence check (integration)
# --------------------------------------------------------------------------- #

# Fixtures claiming ``live`` carry the FR05 content-bound default-path proof —
# PRD-QUAL-119 made it a hard coherence requirement for live claims.
_PRD_FRONT = """---
prd:
  id: {pid}
  title: T
  status: implemented
  priority: {prio}
  functionality_level: {level}
  stubs: {stubs}
  ip_tier: public
default_path_proof:
  receipt: tests/test_default_entrypoint.py::test_public_default_path
  source_digest: sha256:{digest}
  removal_assertion: tests/test_default_entrypoint.py::test_superseded_path_absent
---

# {pid}

{body}
"""

_PUBLIC_UNWIRED_FR = """## 4. Functional Requirements

### {pid}-FR01: A public surface
**Priority**: Must Have
surface: public
**Description**: does a thing.
"""


def _write_prd(
    root: Path, pid: str, *, prio: str = "P0", level: str = "live", stubs: str = "[]", body: str = ""
) -> None:
    prds = root / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    (prds / f"{pid}.md").write_text(
        _PRD_FRONT.format(pid=pid, prio=prio, level=level, stubs=stubs, body=body, digest="a" * 64),
        encoding="utf-8",
    )


def _make_run(
    tmp_path: Path,
    writer: FileStateWriter,
    *,
    run_id: str = "RUN1",
    task_type: str = "coding",
    build_passed: bool = True,
    reviewer_source: str | None = None,
    reviewer_run_id: str | None = None,
) -> Path:
    run_dir = tmp_path / "runs" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    writer.write_yaml(
        meta / "run.yaml",
        {"run_id": run_id, "task": "t", "status": "active", "task_type": task_type, "owner_session_id": "S1"},
    )
    events: list[dict[str, object]] = [{"ts": "2026-07-10T00:00:00Z", "event": "run_init"}]
    if build_passed:
        events.append(
            {
                "ts": "2026-07-10T01:00:00Z",
                "event": "build_check_complete",
                "tests_passed": True,
                "static_checks_clean": True,
            }
        )
    for ev in events:
        writer.append_jsonl(meta / "events.jsonl", ev)
    if reviewer_source is not None:
        # self reviews share the delivering run_id; independent reviewers carry a
        # DISTINCT run_id (verifiable independence). ``reviewer_run_id`` overrides
        # to exercise the asserted-independent (same run_id) path.
        if reviewer_run_id is not None:
            r_run = reviewer_run_id
        elif reviewer_source == "self":
            r_run = run_id
        else:
            r_run = f"{run_id}-REVIEWER"
        reviewer: dict[str, object] = {"source": reviewer_source, "run_id": r_run, "session_id": "S1"}
        if reviewer_source in ("subagent", "cross_model", "operator"):
            reviewer["receipt_id"] = "tok"
        review: dict[str, object] = {"review_id": "r1", "verdict": "pass", "substantive": True, "reviewer": reviewer}
        writer.write_yaml(meta / "review.yaml", review)
    return run_dir


@pytest.fixture
def _patch_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    return tmp_path


def test_all_requirements_met(
    _patch_root: Path, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    # P0 live PRD, no public-surface FR (wiring no-op), build present, independent subagent review.
    _write_prd(_patch_root, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, build_passed=True, reviewer_source="subagent")
    assert tg.check_transition_coherence("PRD-CORE-999", run_dir, reader) == []


def test_self_review_blocks_p0(
    _patch_root: Path, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    _write_prd(_patch_root, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, run_id="RUN1", build_passed=True, reviewer_source="self")
    missing = tg.check_transition_coherence("PRD-CORE-999", run_dir, reader)
    assert "independent_review_receipt" in missing


def test_enumerates_all_missing_items(
    _patch_root: Path, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    # P0 live PRD WITH an unwired public surface; no build; self review.
    body = _PUBLIC_UNWIRED_FR.format(pid="PRD-CORE-999")
    _write_prd(_patch_root, "PRD-CORE-999", prio="P0", level="live", body=body)
    run_dir = _make_run(tmp_path, writer, build_passed=False, reviewer_source="self")
    missing = tg.check_transition_coherence("PRD-CORE-999", run_dir, reader)
    assert "wiring_or_behavioral_evidence" in missing
    assert "build_evidence" in missing
    assert "independent_review_receipt" in missing


def test_functionality_level_incoherent_flagged(
    _patch_root: Path, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    # status implemented but functionality_level stub -> FPI #7 incoherence.
    _write_prd(_patch_root, "PRD-CORE-999", prio="P2", level="stub", stubs="[]")
    run_dir = _make_run(tmp_path, writer, build_passed=True)
    missing = tg.check_transition_coherence("PRD-CORE-999", run_dir, reader)
    assert "functionality_level_incoherent" in missing


def test_p2_no_review_receipt_required(
    _patch_root: Path, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader
) -> None:
    _write_prd(_patch_root, "PRD-CORE-999", prio="P2", level="live")
    run_dir = _make_run(tmp_path, writer, build_passed=True, reviewer_source=None)
    assert tg.check_transition_coherence("PRD-CORE-999", run_dir, reader) == []


def test_wiring_degraded_falls_back_to_presence(
    _patch_root: Path, tmp_path: Path, writer: FileStateWriter, reader: FileStateReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("wiring gate unreachable")

    monkeypatch.setattr("trw_mcp.state.validation._prd_scoring_wiring.check_wiring_gate", _boom)
    # No wiring block in the body -> degraded presence check reports missing.
    _write_prd(_patch_root, "PRD-CORE-999", prio="P2", level="live", body="no wiring here")
    run_dir = _make_run(tmp_path, writer, build_passed=True)
    assert "wiring_or_behavioral_evidence" in tg.check_transition_coherence("PRD-CORE-999", run_dir, reader)
    # With a wiring block present, the degraded check passes.
    _write_prd(_patch_root, "PRD-CORE-999", prio="P2", level="live", body="wiring_test: tests/test_x.py::t")
    assert "wiring_or_behavioral_evidence" not in tg.check_transition_coherence("PRD-CORE-999", run_dir, reader)


# --------------------------------------------------------------------------- #
# FR04+FR05 — end-to-end gate orchestration
# --------------------------------------------------------------------------- #


def _patch_gate(
    monkeypatch: pytest.MonkeyPatch, root: Path, *, gate_mode: str, diff: str, task_type: str = "coding"
) -> None:
    cfg = TRWConfig(prd_transition_gate=gate_mode, deliver_gate_mode="block_coding")
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: root)
    monkeypatch.setattr(tg, "_prd_status_diff", lambda base=None: diff)


def test_evaluate_transition_gate_blocks_incoherent_p0(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, task_type="coding", build_passed=False, reviewer_source="self")
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff=_DIFF_IMPLEMENTED)
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is True
    assert outcome.prd_ids == ["PRD-CORE-999"]
    assert "independent_review_receipt" in outcome.missing_by_prd["PRD-CORE-999"]


def test_evaluate_transition_gate_warn_mode_never_blocks(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, task_type="coding", build_passed=False, reviewer_source="self")
    _patch_gate(monkeypatch, tmp_path, gate_mode="warn", diff=_DIFF_IMPLEMENTED)
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is False
    # warn mode still surfaces the finding for observability (item 2 — no dormant path).
    assert outcome.missing_by_prd  # non-empty
    assert outcome.warning  # observable advisory populated


def test_asserted_independent_blocks_under_block_mode(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # subagent review sharing the delivering run_id = asserted, not verifiable.
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(
        tmp_path,
        writer,
        run_id="RUN1",
        task_type="coding",
        build_passed=True,
        reviewer_source="subagent",
        reviewer_run_id="RUN1",
    )
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff=_DIFF_IMPLEMENTED)
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is True
    assert "independent_review_receipt" in outcome.missing_by_prd["PRD-CORE-999"]


def test_unknown_provenance_warns_not_blocks_in_block_mode(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # item 3 / NFR02: unknown-only receipt shortfall on a P0 transition warns, never blocks.
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, task_type="coding", build_passed=True, reviewer_source=None)
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff=_DIFF_IMPLEMENTED)
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is False
    assert outcome.warning
    assert "review_receipt_provenance_unknown" in outcome.advisory_by_prd["PRD-CORE-999"]


def test_self_same_session_still_blocks_in_block_mode(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # item 3 boundary: self_same_session is NOT downgraded — it hard-blocks.
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, run_id="RUN1", task_type="coding", build_passed=True, reviewer_source="self")
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff=_DIFF_IMPLEMENTED)
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is True
    assert "independent_review_receipt" in outcome.missing_by_prd["PRD-CORE-999"]


def test_evaluate_transition_gate_advisory_task_type_skips(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, task_type="docs", build_passed=False, reviewer_source="self")
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff=_DIFF_IMPLEMENTED, task_type="docs")
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is False


def test_evaluate_transition_gate_certifies_with_independent_review(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")
    run_dir = _make_run(tmp_path, writer, task_type="coding", build_passed=True, reviewer_source="subagent")
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff=_DIFF_IMPLEMENTED)
    outcome = tg.evaluate_transition_gate(run_dir)
    assert outcome.should_block is False


def test_no_transition_detected_no_block(
    tmp_path: Path, writer: FileStateWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _make_run(tmp_path, writer, task_type="coding", build_passed=False, reviewer_source="self")
    _patch_gate(monkeypatch, tmp_path, gate_mode="block", diff="")
    assert tg.evaluate_transition_gate(run_dir).should_block is False


# --------------------------------------------------------------------------- #
# Dispatch wiring — _evaluate_acceptance_integrity in _deliver_gate_dispatch
# --------------------------------------------------------------------------- #


def test_dispatch_sets_acceptance_integrity_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.tools import _deliver_gate_dispatch as gd

    outcome = tg.TransitionGateOutcome(
        should_block=True,
        prd_ids=["PRD-CORE-999"],
        missing_by_prd={"PRD-CORE-999": ["build_evidence"]},
        message="acceptance-integrity block message",
        mode="block",
    )
    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", lambda _run: outcome)
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = gd._evaluate_acceptance_integrity(
        cast("Any", results), errors, tmp_path, cast("Any", tmp_path / ".trw"), False, ""
    )
    assert blocked is True
    assert results["acceptance_integrity_block"] == "acceptance-integrity block message"
    assert results["success"] is False
    assert errors == ["acceptance-integrity block message"]


def test_dispatch_override_proceeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.tools import _deliver_gate_dispatch as gd

    outcome = tg.TransitionGateOutcome(
        should_block=True,
        prd_ids=["PRD-CORE-999"],
        missing_by_prd={"x": ["build_evidence"]},
        message="blk",
        mode="block",
    )
    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", lambda _run: outcome)
    monkeypatch.setattr(
        "trw_mcp.tools._acceptable_failure_validation.apply_structured_override", lambda **kw: (True, None)
    )
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = gd._evaluate_acceptance_integrity(
        cast("Any", results), errors, tmp_path, cast("Any", tmp_path / ".trw"), True, '{"failed_command": "pytest"}'
    )
    assert blocked is False


def test_dispatch_surfaces_warning_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # item 2: a non-blocking advisory writes acceptance_integrity_warning into results.
    from trw_mcp.tools import _deliver_gate_dispatch as gd

    outcome = tg.TransitionGateOutcome(
        should_block=False,
        prd_ids=["PRD-CORE-999"],
        advisory_by_prd={"PRD-CORE-999": ["review_receipt_provenance_unknown"]},
        warning="acceptance-integrity advisory: unknown provenance",
        mode="block",
    )
    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", lambda _run: outcome)
    results: dict[str, Any] = {}
    errors: list[str] = []
    blocked = gd._evaluate_acceptance_integrity(
        cast("Any", results), errors, tmp_path, cast("Any", tmp_path / ".trw"), False, ""
    )
    assert blocked is False
    assert results["acceptance_integrity_warning"] == "acceptance-integrity advisory: unknown provenance"


def test_dispatch_no_active_run_never_blocks(tmp_path: Path) -> None:
    from trw_mcp.tools import _deliver_gate_dispatch as gd

    results: dict[str, Any] = {}
    errors: list[str] = []
    assert (
        gd._evaluate_acceptance_integrity(cast("Any", results), errors, None, cast("Any", "/tmp/x"), False, "") is False
    )


# --------------------------------------------------------------------------- #
# Item 8 — REAL trw_deliver end-to-end
# --------------------------------------------------------------------------- #


def _write_e2e_run(tmp_path: Path) -> Path:
    """A .trw skeleton + run whose diff moves a P0 PRD to implemented, self-review,
    build PRESENT (so the pre-existing build gate does not fire first and mask the
    acceptance-integrity gate under test)."""
    import json

    trw = tmp_path / ".trw"
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "reflections").mkdir(parents=True)
    (trw / "context").mkdir(parents=True)
    _write_prd(tmp_path, "PRD-CORE-999", prio="P0", level="live")

    run_id = "20260710T000000Z-e2e"
    run_dir = tmp_path / ".trw" / "runs" / "task" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\nprd_scope: []\n"
        "task_type: coding\ncomplexity_class: STANDARD\nowner_session_id: SESS-E2E\n",
        encoding="utf-8",
    )
    # reviewer.run_id == the delivering run_id => a SELF review (not independent),
    # which is exactly what the acceptance-integrity gate must block for a P0.
    (meta / "review.yaml").write_text(
        "substantive: true\nverdict: pass\ncritical_count: 0\n"
        f"reviewer:\n  source: self\n  run_id: {run_id}\n  session_id: SESS-E2E\n",
        encoding="utf-8",
    )
    lines = [
        json.dumps({"ts": "2026-07-10T00:00:00Z", "event": "session_start"}),
        json.dumps({"ts": "2026-07-10T00:00:01Z", "event": "file_modified", "data": {"path": "src/x.py"}}),
        json.dumps(
            {
                "ts": "2026-07-10T00:00:02Z",
                "event": "build_check_complete",
                "tests_passed": True,
                "static_checks_clean": True,
            }
        ),
    ]
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir


def _deliver_e2e(tmp_path: Path, run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    from unittest.mock import patch

    from tests.conftest import extract_tool_fn, make_test_server

    deliver_fn = extract_tool_fn(make_test_server("ceremony"), "trw_deliver")
    trw_dir = tmp_path / ".trw"
    # evidence_receipt_mode="observe": this suite exercises the acceptance-integrity /
    # PRD-transition gate in isolation. The fixture records a passing raw
    # build_check_complete event (the pre-existing build gate the test intends to
    # satisfy); observe mode keeps the newer typed-BuildReceipt enforcement from
    # firing first and masking the acceptance-integrity block under test.
    cfg = TRWConfig(
        prd_transition_gate="block",
        deliver_gate_mode="block_coding",
        evidence_receipt_mode="observe",
    )
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.models.config.get_config", lambda: cfg),
        # The evidence-mode + review gates read config through these consumer-site
        # re-exports (separate bindings from models.config.get_config), so patch
        # them too — otherwise a prior test's enforce-mode singleton leaks in and
        # the typed-BuildReceipt gate hard-blocks before acceptance-integrity runs.
        patch("trw_mcp.tools.ceremony.get_config", lambda: cfg),
        patch("trw_mcp.tools._delivery_helpers.get_config", lambda: cfg),
        patch.object(tg, "_prd_status_diff", lambda base=None: _DIFF_IMPLEMENTED),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
    ):
        return deliver_fn(run_path=str(run_dir), skip_reflect=True, **kwargs)


@pytest.mark.integration
def test_real_deliver_blocks_p0_self_review_transition(tmp_path: Path) -> None:
    run_dir = _write_e2e_run(tmp_path)
    result = _deliver_e2e(tmp_path, run_dir)
    assert result["success"] is False
    assert "acceptance_integrity_block" in result
    assert "independent_review_receipt" in str(result["acceptance_integrity_block"])


@pytest.mark.integration
def test_real_deliver_override_proceeds(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timedelta, timezone

    run_dir = _write_e2e_run(tmp_path)
    future = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    reason = json.dumps(
        {
            "failed_command": "trw_review (independent)",
            "residual_risk": "self-review only; tracked follow-up for independent pass",
            "owner": "agent-run-e2e",
            "expiry_iso": future,
        }
    )
    result = _deliver_e2e(tmp_path, run_dir, allow_unverified=True, unverified_reason=reason)
    assert result["success"] is True
    assert result.get("truthfulness_gate_bypassed")


def test_dispatch_gate_degraded_never_blocks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.tools import _deliver_gate_dispatch as gd

    def _boom(_run: object) -> object:
        raise RuntimeError("boom")

    monkeypatch.setattr("trw_mcp.tools._prd_transition_gate.evaluate_transition_gate", _boom)
    results: dict[str, Any] = {}
    errors: list[str] = []
    assert (
        gd._evaluate_acceptance_integrity(cast("Any", results), errors, tmp_path, cast("Any", "/tmp/x"), False, "")
        is False
    )


# ---------------------------------------------------------------------------
# PRD-QUAL-119-FR01: universal typed effective-completion decision
# ---------------------------------------------------------------------------


def test_prd_qual_119_fr01() -> None:
    """FR01 acceptance: Given any priority and any component absent, stale,
    invalid, caller-asserted, or revoked, When implemented or done is requested,
    Then the decision is incomplete, unknown, externally_blocked, or rolled_back
    with reasons; only current complete can transition."""
    from trw_mcp.models.gate_decision import (
        CompletionComponent,
        CompletionComponentState,
        EffectiveCompletionOutcome,
        ExternalGateEvidence,
        derive_effective_completion,
    )

    def component(state: CompletionComponentState, cid: str = "build_receipt") -> CompletionComponent:
        return CompletionComponent(component_id=cid, state=state)

    current = component(CompletionComponentState.CURRENT)

    # Every degraded component state forbids transition, for EVERY priority.
    expected_by_state = {
        CompletionComponentState.ABSENT: EffectiveCompletionOutcome.INCOMPLETE,
        CompletionComponentState.INVALID: EffectiveCompletionOutcome.INCOMPLETE,
        CompletionComponentState.CALLER_ASSERTED: EffectiveCompletionOutcome.INCOMPLETE,
        CompletionComponentState.STALE: EffectiveCompletionOutcome.UNKNOWN,
        CompletionComponentState.REVOKED: EffectiveCompletionOutcome.ROLLED_BACK,
    }
    for priority in ("P0", "P1", "P2", "P3"):
        for state, expected in expected_by_state.items():
            decision = derive_effective_completion(
                "PRD-CORE-001",
                priority=priority,
                components=(current, component(state, "wiring_receipt")),
            )
            assert decision.outcome is expected, (priority, state)
            assert decision.reasons  # reasons are mandatory on non-complete
            assert not decision.permits_transition

    # Only CURRENT-complete transitions.
    complete = derive_effective_completion("PRD-CORE-001", priority="P0", components=(current,))
    assert complete.outcome is EffectiveCompletionOutcome.COMPLETE
    assert complete.permits_transition

    # Evidenced external gate -> externally_blocked (still no transition).
    blocked = derive_effective_completion(
        "PRD-CORE-001",
        priority="P1",
        components=(current,),
        external_gates=(ExternalGateEvidence(gate_id="pypi_release", evidenced=True),),
    )
    assert blocked.outcome is EffectiveCompletionOutcome.EXTERNALLY_BLOCKED
    assert not blocked.permits_transition

    # Unverified external claim -> unknown, never externally_blocked (FR02 seam).
    unverified = derive_effective_completion(
        "PRD-CORE-001",
        components=(current,),
        external_gates=(ExternalGateEvidence(gate_id="vendor_claim", evidenced=False),),
    )
    assert unverified.outcome is EffectiveCompletionOutcome.UNKNOWN

    # Operator rollback supersedes a fully-current component set.
    rolled = derive_effective_completion(
        "PRD-CORE-001",
        components=(current,),
        rolled_back=True,
        rollback_reason="safety rollback",
        superseded_decision_id="dec-prior",
    )
    assert rolled.outcome is EffectiveCompletionOutcome.ROLLED_BACK
    assert rolled.superseded_decision_id == "dec-prior"

    # NFR01 fail-closed: NO components recorded is unknown, never complete.
    empty = derive_effective_completion("PRD-CORE-001")
    assert empty.outcome is EffectiveCompletionOutcome.UNKNOWN
    assert "no_completion_components_recorded" in empty.reasons


def test_prd_qual_119_fr05() -> None:
    """FR05 acceptance: Given only unit or substrate tests pass, When completion
    is requested, Then it fails until a content-bound default-path integration
    receipt and removal assertion exist."""
    from trw_mcp.tools._prd_transition_gate import (
        MISSING_DEFAULT_PATH_PROOF,
        default_path_proof_blocking,
    )

    # A live claim with no proof block fails.
    assert default_path_proof_blocking({}, "live") == [MISSING_DEFAULT_PATH_PROOF]
    # Unit/substrate evidence alone (no receipt block) fails.
    assert default_path_proof_blocking({"verification": {"unit_tests": "pass"}}, "live") == [MISSING_DEFAULT_PATH_PROOF]
    # Partial blocks fail: missing removal assertion, missing content binding.
    assert default_path_proof_blocking(
        {"default_path_proof": {"receipt": "itest-receipt-1", "source_digest": "sha256:aa"}}, "live"
    ) == [MISSING_DEFAULT_PATH_PROOF]
    assert default_path_proof_blocking(
        {"default_path_proof": {"receipt": "itest-receipt-1", "removal_assertion": "test_absent"}}, "live"
    ) == [MISSING_DEFAULT_PATH_PROOF]
    # A digest that is not content-bound (wrong scheme) fails.
    assert default_path_proof_blocking(
        {
            "default_path_proof": {
                "receipt": "itest-receipt-1",
                "source_digest": "md5:zz",
                "removal_assertion": "test_absent",
            }
        },
        "live",
    ) == [MISSING_DEFAULT_PATH_PROOF]

    # The complete content-bound proof passes.
    assert (
        default_path_proof_blocking(
            {
                "default_path_proof": {
                    "receipt": "tests/test_x.py::test_default_public_entrypoint",
                    "source_digest": "sha256:" + "a" * 64,
                    "removal_assertion": "tests/test_x.py::test_superseded_path_absent",
                }
            },
            "live",
        )
        == []
    )
    # Non-live claims are not subject to the live proof requirement.
    assert default_path_proof_blocking({}, "partial") == []


def test_transition_gate_filters_unrelated_shared_worktree_prds() -> None:
    """Concurrent PRD transitions outside an explicit run scope are not certified or blocked."""
    from trw_mcp.tools._prd_transition_gate import _scope_detected_prds

    detected = ["PRD-CORE-140", "PRD-INFRA-104", "PRD-CORE-149"]
    run_data = {"prd_scope": ["PRD-CORE-140", "PRD-CORE-149"]}

    assert _scope_detected_prds(detected, run_data) == ["PRD-CORE-140", "PRD-CORE-149"]
    assert _scope_detected_prds(detected, {"prd_scope": []}) == detected


def test_qual_119_activation_gates_flow_into_decision() -> None:
    """FR02 wiring through the transition decision: external evidenced gates
    yield externally_blocked; unverified ones yield unknown; repo gates block."""
    from trw_mcp.models.gate_decision import EffectiveCompletionOutcome
    from trw_mcp.tools._prd_transition_gate import CoherenceReport, derive_transition_decision

    evidenced = derive_transition_decision(
        "PRD-X",
        CoherenceReport(),
        {"activation_gates": [{"gate_id": "release", "ownership": "external_release", "evidence_receipt": "r-1"}]},
        "content",
    )
    assert evidenced.outcome is EffectiveCompletionOutcome.EXTERNALLY_BLOCKED

    unverified = derive_transition_decision(
        "PRD-X",
        CoherenceReport(),
        {"activation_gates": [{"gate_id": "vendor", "ownership": "external_system"}]},
        "content",
    )
    assert unverified.outcome is EffectiveCompletionOutcome.UNKNOWN
