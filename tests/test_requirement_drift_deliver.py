"""PRD-CORE-321 Slice 2: requirement drift reaches the ``trw_deliver`` response (FR02, FR03, NFR02).

The end-to-end test drives the REAL ``run_trw_deliver`` (no patch on
``evaluate_delivery_gates`` or the drift modules) against a git fixture whose
root is the project root. Deleting the ``compute_requirement_drift`` call from
``_deliver_gate_dispatch.evaluate_delivery_gates`` turns it red on the missing
``requirement_drift`` key. Modules under test are imported inside test bodies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._requirement_drift_fixtures import Mapping, Repo, prd_text
from trw_mcp.models.config import TRWConfig

_FR01: Mapping = {"id": "PRD-X-001-FR01", "criteria": ["strict"], "evidence": "tests/test_x.py"}


def _seed_run(project: Path, scope: list[str]) -> Path:
    trw = project / ".trw"
    for sub in ("learnings/entries", "reflections", "context"):
        (trw / sub).mkdir(parents=True, exist_ok=True)
    run_id = "20260926T000000Z-drift"
    run = trw / "runs" / "task" / run_id
    (run / "meta").mkdir(parents=True)
    (run / "reports").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\nprd_scope: {json.dumps(scope)}\n"
        "task_type: coding\ncomplexity_class: MINIMAL\n",
        encoding="utf-8",
    )
    events = [
        {"ts": "2026-09-26T00:00:00Z", "event": "session_start"},
        {
            "ts": "2026-09-26T00:00:02Z",
            "event": "build_check_complete",
            "test_count": 12,
            "scope": "pytest tests",
            "tests_passed": True,
            "static_checks_clean": True,
        },
    ]
    (run / "meta" / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return run


def _deliver(project: Path, run: Path) -> dict[str, Any]:
    from tests.conftest import extract_tool_fn, make_test_server

    deliver_fn = extract_tool_fn(make_test_server("ceremony"), "trw_deliver")
    cfg = TRWConfig(deliver_gate_mode="block_coding", evidence_receipt_mode="observe")  # type: ignore[call-arg]
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=project / ".trw"),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
        patch("trw_mcp.models.config.get_config", lambda: cfg),
        patch("trw_mcp.tools.ceremony.get_config", lambda: cfg),
        patch("trw_mcp.tools._delivery_helpers.get_config", lambda: cfg),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
    ):
        return dict(deliver_fn(skip_reflect=True))


@pytest.mark.integration
def test_scoped_prd_always_has_an_entry(tmp_path: Path) -> None:
    """NFR02 + FR02 on the real deliver path: every scoped id has an entry, whatever the gates decide.

    PRD-X-001 was approved in a commit and its criterion edited in the working
    tree; PRD-X-404 names no file. Both must appear under ``requirement_drift``.
    """
    repo = Repo(tmp_path)
    repo.write("PRD-X-001-drift.md", prd_text("PRD-X-001", "draft", [_FR01]))
    repo.commit("draft")
    repo.write("PRD-X-001-drift.md", prd_text("PRD-X-001", "approved", [_FR01]))
    approval = repo.commit("approve")
    repo.write("PRD-X-001-drift.md", prd_text("PRD-X-001", "approved", [{**_FR01, "criteria": ["loose"]}]))
    run = _seed_run(tmp_path, ["PRD-X-001", "PRD-X-404"])

    result = _deliver(tmp_path, run)

    assert "requirement_drift" in result, "compute_requirement_drift never ran on the real deliver path"
    report = result["requirement_drift"]
    assert report["scope"] == "declared"
    assert sorted(report["prds"]) == ["PRD-X-001", "PRD-X-404"]
    resolved = report["prds"]["PRD-X-001"]
    assert (resolved["baseline_status"], resolved["reason"], resolved["baseline_sha"]) == ("resolved", None, approval)
    assert (resolved["effective_mode"], resolved["orphan_check"]) == ("warn", "not_applicable")
    assert resolved["findings"] == [
        {
            "requirement_id": "PRD-X-001-FR01",
            "kind": "changed",
            "changed_fields": ["acceptance_criteria"],
            "reason": "",
            "recorded": False,
            "record_reason": None,
        }
    ]
    missing = report["prds"]["PRD-X-404"]
    assert (missing["baseline_status"], missing["reason"], missing["findings"]) == (
        "baseline_unresolvable",
        "prd_not_found",
        [],
    )
    # Slice 3: PRD-X-404's safety flag cannot be read, so its mode is block, and it leaves the advisory. The
    # safety-critical gate (earlier in the cascade) already refuses this delivery on the same unreadable id,
    # so the drift gate is not reached; PRD-X-001 never declared the flag, so its finding only warns.
    assert missing["effective_mode"] == "block"
    assert "PRD-X-001-FR01 changed" in result["requirement_drift_warning"]
    assert "PRD-X-404" not in result["requirement_drift_warning"]
    assert "safety_critical_adversarial_block" in result and "requirement_drift_block" not in result


# --------------------------------------------------------------------------- #
# NFR02 — every scoped id gets exactly one entry with a named status (direct on compute)
# --------------------------------------------------------------------------- #


def _compute(project: Path, run: Path) -> Any:
    from trw_mcp.tools._deliver_requirement_drift import compute_requirement_drift

    with patch("trw_mcp.state._paths.resolve_project_root", return_value=project):
        return compute_requirement_drift(run)


def _approved_then(repo: Repo, working_status: str | None) -> None:
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "draft", [_FR01]))
    repo.commit("draft")
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01]))
    repo.commit("approve")
    if working_status is not None:
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", working_status, [_FR01]))


def _setup(case: str, tmp_path: Path) -> Path:
    """Build the project for one NFR02 case; returns the project root."""
    project = tmp_path / "project"
    if case == "no_git":
        prds = project / "docs/requirements-aare-f/prds"
        prds.mkdir(parents=True)
        (prds / "PRD-X-001-a.md").write_text(prd_text("PRD-X-001", "approved", [_FR01]), encoding="utf-8")
        return project
    if case == "shallow_clone":
        origin = Repo(tmp_path / "origin")
        _approved_then(origin, None)
        origin.git("clone", "-q", "--depth", "1", f"file://{origin.root}", str(project))
        return project
    repo = Repo(project)
    if case in {"resolved", "resolver_raises"}:
        _approved_then(repo, None)
    elif case == "untracked":
        repo.write("PRD-Y-002-anchor.md", prd_text("PRD-Y-002", "draft", []))
        repo.commit("unrelated first commit")
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01]))
    elif case == "current_unparseable":
        _approved_then(repo, None)
        repo.write("PRD-X-001-a.md", "no frontmatter at all\n")
    elif case == "prd_not_found":
        repo.write("PRD-Y-002-anchor.md", prd_text("PRD-Y-002", "draft", []))
        repo.commit("no PRD-X-001 anywhere")
    else:  # an uncommitted status after a committed approval
        _approved_then(repo, case)
    return project


@pytest.mark.parametrize(
    ("case", "status", "reason", "kinds"),
    [
        ("resolved", "resolved", None, []),
        ("no_git", "baseline_unresolvable", "no_git", []),
        ("untracked", "baseline_unresolvable", "untracked", []),
        ("shallow_clone", "resolved", None, ["shallow_clone"]),
        ("resolver_raises", "not_evaluated", "RuntimeError", []),
        ("current_unparseable", "baseline_unresolvable", "current_unparseable", []),
        ("prd_not_found", "baseline_unresolvable", "prd_not_found", []),
        ("draft", "resolved", None, ["status_regressed"]),
        ("partial", "resolved", None, ["status_regressed"]),
        ("in_progress", "resolved", None, ["status_regressed"]),
    ],
)
def test_every_scoped_id_gets_a_named_entry(
    tmp_path: Path, case: str, status: str, reason: str | None, kinds: list[str]
) -> None:
    """NFR02: exact status, reason and finding kinds per case.

    A resolver gated on the working-tree status fails the last three cases; one
    that swallows the exception (dropping the entry) fails ``resolver_raises``.
    """
    project = _setup(case, tmp_path)
    run = _seed_run(project, ["PRD-X-001"])
    if case == "resolver_raises":
        with patch(
            "trw_mcp.state.validation.requirement_baseline.resolve_requirement_baseline",
            side_effect=RuntimeError("git log failed"),
        ):
            report = _compute(project, run)
    else:
        report = _compute(project, run)

    assert report["scope"] == "declared"
    assert list(report["prds"]) == ["PRD-X-001"]
    entry = report["prds"]["PRD-X-001"]
    assert (entry["baseline_status"], entry["reason"]) == (status, reason)
    assert [finding["kind"] for finding in entry["findings"]] == kinds
    if kinds == ["status_regressed"]:
        assert entry["findings"][0]["reason"] == f"current status {case}"


def test_not_applicable_needs_read_history_without_approval(tmp_path: Path) -> None:
    """NFR02: a PRD whose committed history never reached approval and whose status is draft is not_applicable."""
    repo = Repo(tmp_path)
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "draft", [_FR01]))
    repo.commit("draft only")

    entry = _compute(tmp_path, _seed_run(tmp_path, ["PRD-X-001"]))["prds"]["PRD-X-001"]

    assert (entry["baseline_status"], entry["reason"], entry["findings"]) == ("not_applicable", None, [])


@pytest.mark.parametrize("scope", [None, []], ids=["no_run", "empty_scope"])
def test_no_declared_scope_is_the_not_declared_report(tmp_path: Path, scope: list[str] | None) -> None:
    """FR05/NFR03 shape: no run, or an empty union, gives exactly the not_declared mapping."""
    from trw_mcp.tools._deliver_requirement_drift import compute_requirement_drift

    run = None if scope is None else _seed_run(tmp_path, scope)

    assert compute_requirement_drift(run) == {"scope": "not_declared", "prds": {}}


def test_scope_forms_are_keyed_by_prd_id(tmp_path: Path) -> None:
    """A bare id and a path to the same PRD share one entry; a non-PRD glob is keyed as declared."""
    repo = Repo(tmp_path)
    _approved_then(repo, None)
    run = _seed_run(tmp_path, ["PRD-X-001", "docs/requirements-aare-f/prds/PRD-X-001-a.md", "src/seeds/**"])

    report = _compute(tmp_path, run)

    assert sorted(report["prds"]) == ["PRD-X-001", "src/seeds/**"]
    assert report["prds"]["PRD-X-001"]["baseline_status"] == "resolved"
    assert (report["prds"]["src/seeds/**"]["baseline_status"], report["prds"]["src/seeds/**"]["reason"]) == (
        "baseline_unresolvable",
        "prd_not_found",
    )


def test_receipt_only_prd_id_is_in_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scope is prd_scope UNION review-receipt prd_ids, the rule the safety-critical gate reads."""
    from datetime import datetime, timezone

    from trw_mcp.tools._delivery_safety_critical_gate import declared_scope_union
    from trw_mcp.tools._review_manual import handle_manual_mode

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    repo = Repo(tmp_path)
    _approved_then(repo, None)
    run = _seed_run(tmp_path, ["PRD-Y-404"])
    handle_manual_mode([], run, "rv-1", datetime.now(timezone.utc).isoformat(), ["PRD-X-001"], review_completed=True)

    assert declared_scope_union(run) == ["PRD-X-001", "PRD-Y-404"]
    report = _compute(tmp_path, run)
    assert sorted(report["prds"]) == ["PRD-X-001", "PRD-Y-404"]
    assert report["prds"]["PRD-X-001"]["baseline_status"] == "resolved"


def test_declared_scope_union_is_sorted_and_deduplicated(tmp_path: Path) -> None:
    """Direct: run.yaml entries are united, sorted, de-duplicated and emptied of blanks."""
    from trw_mcp.tools._delivery_safety_critical_gate import declared_scope_union

    run = _seed_run(tmp_path, ["PRD-B-002", "", "PRD-A-001", "PRD-B-002"])

    assert declared_scope_union(run) == ["PRD-A-001", "PRD-B-002"]


# --------------------------------------------------------------------------- #
# The advisory text and the dispatch seam
# --------------------------------------------------------------------------- #


def _finding(kind: str, *, recorded: bool = False, reason: str = "") -> dict[str, Any]:
    return {
        "requirement_id": "PRD-X-001-FR01",
        "kind": kind,
        "changed_fields": [],
        "reason": reason,
        "recorded": recorded,
        "record_reason": None,
    }


def _report(status: str, reason: str | None, findings: list[dict[str, Any]], mode: str = "warn") -> Any:
    entry = {
        "baseline_status": status,
        "reason": reason,
        "baseline_sha": None,
        "approval_date": None,
        "effective_mode": mode,
        "orphan_check": "applied",
        "findings": findings,
    }
    return {"scope": "declared", "prds": {"PRD-X-001": entry}}


@pytest.mark.parametrize(
    ("report", "blocks_task", "expected_items"),
    [
        (_report("resolved", None, []), False, None),
        (_report("not_applicable", None, []), False, None),
        (_report("resolved", None, [_finding("evidence_not_checkable")]), False, None),
        (_report("resolved", None, [_finding("changed", recorded=True)]), False, None),
        (_report("resolved", None, [_finding("dropped")]), False, "PRD-X-001-FR01 dropped"),
        (
            _report("resolved", None, [_finding("orphaned", reason="evidence artifact missing")]),
            False,
            "PRD-X-001-FR01 orphaned (evidence artifact missing)",
        ),
        (_report("not_evaluated", "RuntimeError", []), False, "PRD-X-001 not_evaluated (RuntimeError)"),
        (_report("baseline_unresolvable", "no_git", []), False, "PRD-X-001 baseline_unresolvable (no_git)"),
        (_report("resolved", None, [_finding("dropped")], mode="block"), True, None),
        (_report("resolved", None, [_finding("dropped")], mode="block"), False, "PRD-X-001-FR01 dropped"),
        (_report("resolved", None, [_finding("dropped")]), True, "PRD-X-001-FR01 dropped"),
    ],
)
def test_drift_warning(report: Any, blocks_task: bool, expected_items: str | None) -> None:
    """Direct: unrecorded findings and unchecked PRDs are named unless they block; recorded/not-checkable never.

    A block-mode PRD's items leave the advisory only when the run's task type blocks (they
    become ``requirement_drift_block``); a warn-mode PRD's items stay advisory whatever the task type.
    """
    from trw_mcp.tools._deliver_requirement_drift import drift_warning

    text = drift_warning(report, blocks_task)

    if expected_items is None:
        assert text == ""
    else:
        assert text.startswith(f"Requirement drift advisory (PRD-CORE-321): {expected_items}. ")
        assert text.endswith("Advisory only — this does not block delivery.")


def test_dispatch_reports_drift_even_when_a_hard_gate_blocks(tmp_path: Path) -> None:
    """Direct on evaluate_delivery_gates: the report lands before the NO_ESCAPE gate returns."""
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    repo = Repo(tmp_path)
    _approved_then(repo, "partial")
    run = _seed_run(tmp_path, ["PRD-X-001"])
    results: dict[str, Any] = {}

    with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
        blocked = evaluate_delivery_gates(
            {"integration_review_block": "hard"},
            results,  # type: ignore[arg-type]
            [],
            run,
            tmp_path / ".trw",
            False,
            "",
        )

    assert blocked is True
    entry = results["requirement_drift"]["prds"]["PRD-X-001"]
    assert [finding["kind"] for finding in entry["findings"]] == ["status_regressed"]
    assert "PRD-X-001 status_regressed" in results["requirement_drift_warning"]


def test_dispatch_sets_no_warning_without_a_finding(tmp_path: Path) -> None:
    """Direct on evaluate_delivery_gates: a clean resolved PRD gets its entry and no warning key."""
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    repo = Repo(tmp_path)
    _approved_then(repo, None)
    run = _seed_run(tmp_path, ["PRD-X-001"])
    results: dict[str, Any] = {}

    with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
        evaluate_delivery_gates({}, results, [], run, tmp_path / ".trw", False, "")  # type: ignore[arg-type]

    assert results["requirement_drift"]["prds"]["PRD-X-001"]["baseline_status"] == "resolved"
    assert "requirement_drift_warning" not in results


# --------------------------------------------------------------------------- #
# Slice 3 — FR04 amendments and FR05 block mode, on the real deliver path
# --------------------------------------------------------------------------- #

_FUTURE = "2099-12-31"


def _amendments(*rows: tuple[str, str, str, str, str]) -> str:
    """A ``## Requirement Amendments`` section with one row per (Requirement, Date, Reason, Owner, Expiry)."""
    lines = ["", "## Requirement Amendments", "", "| Requirement | Date | Reason | Owner | Expiry |"]
    lines += ["|-------------|------|--------|-------|--------|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def _edited_project(root: Path, *, amendment: str = "") -> str:
    """draft, approved (committed), criterion edited in the working tree; returns the approval sha."""
    repo = Repo(root)
    repo.write("PRD-X-001-drift.md", prd_text("PRD-X-001", "draft", [_FR01]))
    repo.commit("draft")
    repo.write("PRD-X-001-drift.md", prd_text("PRD-X-001", "approved", [_FR01]))
    approval = repo.commit("approve")
    edited = prd_text("PRD-X-001", "approved", [{**_FR01, "criteria": ["loose"]}])
    repo.write("PRD-X-001-drift.md", edited + amendment)
    return approval


@pytest.mark.integration
def test_edited_criterion_is_reported_end_to_end(tmp_path: Path) -> None:
    """FR05 AC1 + success criteria 1-2 through run_trw_deliver: ``changed, unrecorded``, then ``changed, recorded``.

    The two projects differ ONLY by one dated Requirement Amendments row naming
    the requirement id (same day as the approval commit: the inclusive boundary).
    """
    plain, amended = tmp_path / "plain", tmp_path / "amended"
    approval = _edited_project(plain)
    row = ("PRD-X-001-FR01", "2026-01-02", "criterion narrowed after review", "", "")
    assert _edited_project(amended, amendment=_amendments(row)) == approval

    before = _deliver(plain, _seed_run(plain, ["PRD-X-001"]))
    after = _deliver(amended, _seed_run(amended, ["PRD-X-001"]))

    entry = before["requirement_drift"]["prds"]["PRD-X-001"]
    assert (entry["baseline_sha"], entry["approval_date"], entry["effective_mode"]) == (approval, "2026-01-02", "warn")
    changed = {"requirement_id": "PRD-X-001-FR01", "kind": "changed", "changed_fields": ["acceptance_criteria"]}
    assert entry["findings"] == [{**changed, "reason": "", "recorded": False, "record_reason": None}]
    assert "PRD-X-001-FR01 changed" in before["requirement_drift_warning"]
    recorded = after["requirement_drift"]["prds"]["PRD-X-001"]["findings"]
    assert recorded == [{**changed, "reason": "", "recorded": True, "record_reason": "criterion narrowed after review"}]
    assert "requirement_drift_warning" not in after
    assert "requirement_drift_block" not in before and "requirement_drift_block" not in after


def _cfg(gate: str | None = None, mode: str = "block_coding") -> TRWConfig:
    cfg = TRWConfig(deliver_gate_mode=mode, evidence_receipt_mode="observe")  # type: ignore[call-arg]
    # model_copy(update=...) skips validation, so this cell is red by ASSERTION on a tree without the field.
    return cfg.model_copy(update={"requirement_drift_gate": gate}) if gate is not None else cfg


def _gate(project: Path, cfg: TRWConfig, *, task_type: str = "coding", record: str = "") -> tuple[bool, dict[str, Any]]:
    """Drive evaluate_delivery_gates (no fired gate_result key) with *cfg*; returns (blocked, results)."""
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    run = _seed_run(project, ["PRD-X-001"])
    run_yaml = run / "meta" / "run.yaml"
    run_yaml.write_text(run_yaml.read_text(encoding="utf-8").replace("task_type: coding", f"task_type: {task_type}"))
    results: dict[str, Any] = {}
    with (
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
        patch("trw_mcp.models.config.get_config", lambda: cfg),
    ):
        blocked = evaluate_delivery_gates({}, results, [], run, project / ".trw", bool(record), record)  # type: ignore[arg-type]
    return blocked, results


_SAFE = "  safety_critical: true"
_UNSAFE = "  safety_critical: false"


def _safety_history(repo: Repo, cell: str) -> None:
    """Commit PRD-X-001's history for one safety cell and leave an edited criterion in the working tree."""
    edited: list[Mapping | str] = [{**_FR01, "criteria": ["loose"]}]
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "draft", [_FR01]))
    repo.commit("draft")
    if cell == "downgraded":
        # The baseline (A1) never declared the flag; a later approved version did; a re-approval (A2) set it false.
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01]))
        repo.commit("approve without the flag")
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01], extra=_SAFE))
        repo.commit("declare safety critical")
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "draft", [_FR01], extra=_SAFE))
        repo.commit("back to draft")
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01], extra=_UNSAFE))
        repo.commit("re-approve, flag dropped")
        repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", edited, extra=_UNSAFE))
        return
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01], extra=_SAFE if cell == "baseline" else ""))
    repo.commit("approve")
    current_flag = _SAFE if cell in {"baseline", "current_only"} else ""
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", edited, extra=current_flag))


@pytest.mark.parametrize(
    ("cell", "gate", "blocks"),
    [
        ("never", None, False),
        ("baseline", None, True),
        ("current_only", None, True),
        ("downgraded", None, True),
        ("baseline", "warn", False),
        ("never", "block", True),
    ],
)
def test_block_mode_cells(tmp_path: Path, cell: str, gate: str | None, blocks: bool) -> None:
    """FR05 per (requirement_drift_gate, safety flag in history, safety flag current) cell.

    ``downgraded`` is the discriminating cell: the baseline version and the
    current version both lack the flag, only a later approved version held it,
    so an implementation reading only the baseline OR only the current flag warns.
    """
    _safety_history(Repo(tmp_path), cell)

    blocked, results = _gate(tmp_path, _cfg(gate))

    entry = results["requirement_drift"]["prds"]["PRD-X-001"]
    assert entry["effective_mode"] == ("block" if blocks else "warn")
    assert blocked is blocks
    if blocks:
        assert "PRD-X-001-FR01 changed" in results.get("requirement_drift_block", "")
        assert "requirement_drift_warning" not in results
    else:
        assert "requirement_drift_block" not in results
        assert "PRD-X-001-FR01 changed" in results["requirement_drift_warning"]


def test_block_mode_downgrades_to_warning_when_deliver_gate_mode_does_not_block(tmp_path: Path) -> None:
    """FR05: a block-mode PRD under a non-build task type warns instead (gate_mode_blocks_task is False)."""
    _safety_history(Repo(tmp_path), "baseline")

    blocked, results = _gate(tmp_path, _cfg(), task_type="docs")

    assert results["requirement_drift"]["prds"]["PRD-X-001"]["effective_mode"] == "block"
    assert blocked is False
    assert "requirement_drift_block" not in results
    assert "PRD-X-001-FR01 changed" in results["requirement_drift_warning"]


def test_block_mode_recorded_finding_needs_owner_and_expiry(tmp_path: Path) -> None:
    """FR04 under block: an Owner-less row does not record; a complete row does, and nothing blocks or warns."""
    incomplete, complete = tmp_path / "incomplete", tmp_path / "complete"
    for root, owner in ((incomplete, ""), (complete, "lead")):
        _edited_project(root, amendment=_amendments(("PRD-X-001-FR01", "2026-01-02", "narrowed", owner, _FUTURE)))

    blocked_incomplete, results_incomplete = _gate(incomplete, _cfg("block"))
    blocked_complete, results_complete = _gate(complete, _cfg("block"))

    finding = results_incomplete["requirement_drift"]["prds"]["PRD-X-001"]["findings"][0]
    assert (finding["recorded"], finding["record_reason"], blocked_incomplete) == (False, "amendment_incomplete", True)
    finding = results_complete["requirement_drift"]["prds"]["PRD-X-001"]["findings"][0]
    assert (finding["recorded"], finding["record_reason"], blocked_complete) == (True, "narrowed", False)
    assert "requirement_drift_block" not in results_complete and "requirement_drift_warning" not in results_complete


def test_unresolvable_scoped_prd_blocks_by_default(tmp_path: Path) -> None:
    """FR05: ``prd_not_found`` cannot read the safety flag, so its mode is block, and it blocks unset config."""
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    Repo(tmp_path).write("PRD-Y-002-anchor.md", prd_text("PRD-Y-002", "draft", []))
    run = _seed_run(tmp_path, ["PRD-X-404"])
    results: dict[str, Any] = {}
    cfg = _cfg()
    with (
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.models.config.get_config", lambda: cfg),
    ):
        blocked = evaluate_delivery_gates({}, results, [], run, tmp_path / ".trw", False, "")  # type: ignore[arg-type]

    assert results["requirement_drift"]["prds"]["PRD-X-404"]["effective_mode"] == "block"
    assert blocked is True
    assert "PRD-X-404 baseline_unresolvable (prd_not_found)" in results.get("requirement_drift_block", "")


def test_not_evaluated_is_block_eligible(tmp_path: Path) -> None:
    """FR05: a checker fault on a block-mode PRD blocks rather than passing silently."""
    _edited_project(tmp_path)
    with patch(
        "trw_mcp.state.validation.requirement_baseline.resolve_requirement_baseline",
        side_effect=RuntimeError("git log failed"),
    ):
        blocked, results = _gate(tmp_path, _cfg("block"))

    assert blocked is True
    assert "PRD-X-001 not_evaluated (RuntimeError)" in results.get("requirement_drift_block", "")


def test_acceptable_failure_record_lets_the_block_pass(tmp_path: Path) -> None:
    """FR05: the PRD-CORE-191 record is the only override, and a valid one passes the block."""
    refused_root, passed_root = tmp_path / "refused", tmp_path / "passed"
    _edited_project(refused_root)
    _edited_project(passed_root)
    record = json.dumps(
        {
            "failed_command": "requirement drift PRD-X-001-FR01",
            "residual_risk": "criterion change reviewed out of band",
            "owner": "lead",
            "expiry_iso": _FUTURE,
        }
    )

    refused, refused_results = _gate(refused_root, _cfg("block"), record="free text")
    passed, passed_results = _gate(passed_root, _cfg("block"), record=record)

    assert refused is True and "requirement_drift_block" in refused_results
    assert passed is False
    assert "requirement_drift_block" not in passed_results


def test_nfr03_config_and_result_keys() -> None:
    """NFR03: the gate field defaults to None, a config without it loads unchanged, and the block key is optional."""
    from trw_mcp.models.typed_dicts import DeliverResultDict

    assert TRWConfig().requirement_drift_gate is None
    assert TRWConfig.model_validate({"deliver_gate_mode": "advisory"}).requirement_drift_gate is None
    assert TRWConfig.model_validate({"requirement_drift_gate": "block"}).requirement_drift_gate == "block"
    keys = {"requirement_drift", "requirement_drift_warning", "requirement_drift_block"}
    assert keys <= DeliverResultDict.__optional_keys__


# --------------------------------------------------------------------------- #
# FR04 — which row records which finding kind (direct on compute_requirement_drift)
# --------------------------------------------------------------------------- #


def _kinds(entry: Any) -> list[tuple[str, str, bool, str | None]]:
    return [(f["requirement_id"], f["kind"], f["recorded"], f["record_reason"]) for f in entry["findings"]]


@pytest.mark.parametrize(
    ("row_id", "recorded", "record_reason"),
    [("PRD-X-001", True, "back to draft for rework"), ("PRD-X-001-FR01", False, None)],
    ids=["prd_id_row", "requirement_id_row"],
)
def test_status_regressed_is_recorded_only_by_a_prd_id_row(
    tmp_path: Path, row_id: str, recorded: bool, record_reason: str | None
) -> None:
    """FR04: ``status_regressed`` is a PRD-level finding, recordable by a row naming the PRD id only."""
    repo = Repo(tmp_path)
    _approved_then(repo, None)
    row = (row_id, "2026-01-02", "back to draft for rework", "lead", _FUTURE)
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "draft", [_FR01]) + _amendments(row))

    entry = _compute(tmp_path, _seed_run(tmp_path, ["PRD-X-001"]))["prds"]["PRD-X-001"]

    assert _kinds(entry) == [("PRD-X-001", "status_regressed", recorded, record_reason)]


@pytest.mark.parametrize(
    ("row_date", "recorded", "record_reason"),
    [("2026-01-02", False, "amendment_predates_approval"), ("2026-01-04", True, "re-approved")],
    ids=["dated_first_approval", "dated_newest_approval"],
)
def test_reapproval_row_is_dated_against_the_newest_approval(
    tmp_path: Path, row_date: str, recorded: bool, record_reason: str | None
) -> None:
    """FR04: a PRD-id row records ``baseline_reapproved`` only when dated on or after the NEWEST approval.

    The criterion changed since the first approval stays unrecorded: a PRD-id row never absorbs it.
    """
    repo = Repo(tmp_path)
    _approved_then(repo, None)  # 2026-01-01 draft, 2026-01-02 approval A1
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "draft", [_FR01]))
    repo.commit("back to draft")  # 2026-01-03
    narrowed = prd_text("PRD-X-001", "approved", [{**_FR01, "criteria": ["loose"]}])
    repo.write("PRD-X-001-a.md", narrowed)
    repo.commit("re-approve")  # 2026-01-04, A2
    repo.write("PRD-X-001-a.md", narrowed + _amendments(("PRD-X-001", row_date, "re-approved", "lead", _FUTURE)))

    entry = _compute(tmp_path, _seed_run(tmp_path, ["PRD-X-001"]))["prds"]["PRD-X-001"]

    assert _kinds(entry) == [
        ("PRD-X-001", "baseline_reapproved", recorded, record_reason),
        ("PRD-X-001-FR01", "changed", False, None),
    ]


def test_orphan_is_recorded_by_its_requirement_id_row(tmp_path: Path) -> None:
    """FR04: an orphan finding is recordable by a row naming its requirement id (a moved test, for example)."""
    repo = Repo(tmp_path)
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "implemented", [_FR01]))
    repo.commit("implemented")  # 2026-01-01; tests/test_x.py does not exist
    row = ("PRD-X-001-FR01", "2026-01-01", "test moved to tests/test_x2.py", "lead", _FUTURE)
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "implemented", [_FR01]) + _amendments(row))

    entry = _compute(tmp_path, _seed_run(tmp_path, ["PRD-X-001"]))["prds"]["PRD-X-001"]

    assert _kinds(entry) == [("PRD-X-001-FR01", "orphaned", True, "test moved to tests/test_x2.py")]


def test_row_for_an_id_without_a_finding_adds_nothing(tmp_path: Path) -> None:
    """FR04: an amendment row is a record, never a finding of its own."""
    repo = Repo(tmp_path)
    _approved_then(repo, None)
    rows = [("PRD-X-001-FR01", "2026-01-02", "nothing changed", "lead", _FUTURE)]
    rows.append(("PRD-X-001", "2026-01-02", "nothing regressed", "lead", _FUTURE))
    repo.write("PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [_FR01]) + _amendments(*rows))

    entry = _compute(tmp_path, _seed_run(tmp_path, ["PRD-X-001"]))["prds"]["PRD-X-001"]

    assert (entry["baseline_status"], entry["findings"]) == ("resolved", [])


@pytest.mark.parametrize("case", ["untracked", "shallow_clone"])
def test_unreadable_baseline_kinds_are_never_recorded(tmp_path: Path, case: str) -> None:
    """FR04/FR05: ``baseline_unresolvable`` and the resolved ``shallow_clone`` finding ignore a PRD-id row.

    Both are limits on what the checker could read, so only a PRD-CORE-191 record passes them;
    under ``requirement_drift_gate=block`` each still blocks with the row present.
    """
    project = _setup(case, tmp_path)
    prd = project / "docs/requirements-aare-f/prds/PRD-X-001-a.md"
    row = ("PRD-X-001", "2026-01-02", "acknowledged", "lead", _FUTURE)
    prd.write_text(prd.read_text(encoding="utf-8") + _amendments(row), encoding="utf-8")

    blocked, results = _gate(project, _cfg("block"))

    entry = results["requirement_drift"]["prds"]["PRD-X-001"]
    if case == "untracked":
        assert (entry["baseline_status"], entry["reason"], entry["findings"]) == (
            "baseline_unresolvable",
            "untracked",
            [],
        )
        assert "PRD-X-001 baseline_unresolvable (untracked)" in results.get("requirement_drift_block", "")
    else:
        assert _kinds(entry) == [("PRD-X-001", "shallow_clone", False, None)]
        assert "PRD-X-001 shallow_clone" in results.get("requirement_drift_block", "")
    assert blocked is True


@pytest.mark.parametrize(
    ("setup", "current_text", "raises", "mode"),
    [
        ("no_git", None, False, "warn"),
        ("no_git", prd_text("PRD-X-001", "approved", [_FR01], extra=_SAFE), False, "block"),
        ("current_unparseable", None, False, "block"),
        ("resolved", None, True, "warn"),
        ("resolved", "no frontmatter at all\n", True, "block"),
    ],
    ids=["no_git_plain", "no_git_safe", "current_unparseable", "not_evaluated_plain", "not_evaluated_unparseable"],
)
def test_unresolved_entry_mode_reads_the_current_flag(
    tmp_path: Path, setup: str, current_text: str | None, raises: bool, mode: str
) -> None:
    """FR05: an entry without a baseline reads the CURRENT flag; only an unreadable flag forces block.

    ``no_git`` on a PRD that never declared the flag warns (the safety-critical gate's regression
    ``test_non_safety_critical_scope_never_evaluates_the_gate`` relies on it); ``current_unparseable``
    and a ``not_evaluated`` PRD whose file does not parse cannot read it, so they block.
    """
    project = _setup(setup, tmp_path)
    if current_text is not None:
        (project / "docs/requirements-aare-f/prds/PRD-X-001-a.md").write_text(current_text, encoding="utf-8")
    run = _seed_run(project, ["PRD-X-001"])
    cfg = _cfg()
    fault = RuntimeError("git log failed") if raises else None
    resolver = "trw_mcp.state.validation.requirement_baseline.resolve_requirement_baseline"

    with patch("trw_mcp.models.config.get_config", lambda: cfg):
        if fault is None:
            entry = _compute(project, run)["prds"]["PRD-X-001"]
        else:
            with patch(resolver, side_effect=fault):
                entry = _compute(project, run)["prds"]["PRD-X-001"]

    assert (entry["baseline_status"], entry["effective_mode"]) == (
        "not_evaluated" if raises else "baseline_unresolvable",
        mode,
    )


def test_a_checker_fault_keeps_the_historical_safety_flag(tmp_path: Path) -> None:
    """core321-s3 r1 P1: a checker exception after the baseline resolved must not drop safety_critical_in_history.

    The downgraded history (flag held by a later approved version, dropped by the re-approval and by
    the current text) plus a duplicate mapping makes detect_requirement_drift raise; the entry must
    still be block mode and block, not fall back to the current flag and warn.
    """
    repo = Repo(tmp_path)
    _safety_history(repo, "downgraded")
    repo.write(
        "PRD-X-001-a.md", prd_text("PRD-X-001", "approved", [{**_FR01, "criteria": ["loose"]}, _FR01], extra=_UNSAFE)
    )

    blocked, results = _gate(tmp_path, _cfg(None))

    entry = results["requirement_drift"]["prds"]["PRD-X-001"]
    assert (entry["baseline_status"], entry["reason"]) == ("not_evaluated", "DuplicateRequirementIdError")
    assert entry["effective_mode"] == "block"
    # The resolved baseline survives the fault: its sha is the first approval commit, not None.
    first_approval = repo.git("log", "--format=%H", "--grep=approve without the flag").strip()
    assert entry["baseline_sha"] == first_approval
    assert blocked is True
    assert "PRD-X-001 not_evaluated (DuplicateRequirementIdError)" in results.get("requirement_drift_block", "")
