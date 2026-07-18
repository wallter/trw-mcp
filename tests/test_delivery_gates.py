"""Tests for delivery gate — complexity drift detection (R-02 + R-05).

Covers _check_complexity_drift(): re-evaluate complexity at delivery time
by comparing actual file_modified event count against the initial
complexity_signals.files_affected estimate from run.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._ceremony_helpers import (
    _check_complexity_drift,
    _read_run_events,
    _read_run_yaml,
    check_delivery_gates,
)
from trw_mcp.tools._delivery_build_gates import (
    _build_evidence_is_stale,
    _check_build_and_work_events,
)


def _write_run_yaml(
    run_dir: Path,
    *,
    complexity_class: str = "MINIMAL",
    files_affected: int = 1,
) -> None:
    """Write a minimal run.yaml with complexity fields."""
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    content = (
        f"run_id: test-run\n"
        f"status: active\n"
        f"phase: implement\n"
        f"task_name: test-task\n"
        f"complexity_class: {complexity_class}\n"
        f"complexity_signals:\n"
        f"  files_affected: {files_affected}\n"
        f"  novel_patterns: false\n"
        f"  cross_cutting: false\n"
    )
    (meta / "run.yaml").write_text(content, encoding="utf-8")


def _write_file_modified_events(
    run_dir: Path,
    count: int,
) -> None:
    """Write N file_modified events to events.jsonl."""
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for i in range(count):
        event = {
            "ts": f"2026-03-01T12:0{i % 10}:00Z",
            "event": "file_modified",
            "data": {"path": f"src/module_{i}.py"},
        }
        lines.append(json.dumps(event))
    # Also add some non-file_modified events to ensure we only count file_modified
    lines.append(json.dumps({"ts": "2026-03-01T12:00:00Z", "event": "checkpoint"}))
    lines.append(json.dumps({"ts": "2026-03-01T12:00:01Z", "event": "build_check_complete"}))
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture()
def reader() -> FileStateReader:
    return FileStateReader()


@pytest.mark.integration
class TestCheckComplexityDrift:
    """Complexity drift detection at delivery time (R-02 + R-05)."""

    def test_complexity_drift_warning_fires(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """MINIMAL with files_affected=1 but 13 file_modified events -> warning."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 13)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is not None
        assert "Complexity drift detected" in result
        assert "MINIMAL" in result
        assert "1 files planned" in result
        assert "13 files were modified" in result
        assert "REVIEW" in result

    def test_complexity_drift_no_warning_for_standard(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """STANDARD classification -> no warning even with many files."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="STANDARD", files_affected=3)
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_below_threshold(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """MINIMAL with only 4 file_modified events -> no warning (<=5 threshold)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 4)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_when_estimate_accurate(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """MINIMAL with files_affected=5 and actual=6 -> no warning (not >2x)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=5)
        _write_file_modified_events(run_dir, 6)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_exactly_at_threshold_no_warning(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Exactly 5 files and exactly 2x -> no warning (requires >5 AND >2x)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=2)
        _write_file_modified_events(run_dir, 5)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        # 5 is not >5, so no warning
        assert result is None

    def test_complexity_drift_failopen_on_missing_run_yaml(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Missing run.yaml -> returns None (fail-open)."""
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_failopen_on_missing_events(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Missing events.jsonl -> returns None (fail-open)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        # Remove events.jsonl if it was created
        events_path = run_dir / "meta" / "events.jsonl"
        if events_path.exists():
            events_path.unlink()

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_failopen_on_corrupt_yaml(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """Corrupt run.yaml -> returns None (fail-open)."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text("{{invalid yaml", encoding="utf-8")
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_when_no_complexity_class(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """run.yaml without complexity_class -> returns None."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: test-run\nstatus: active\nphase: implement\n",
            encoding="utf-8",
        )
        _write_file_modified_events(run_dir, 20)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None

    def test_complexity_drift_no_warning_for_comprehensive(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """COMPREHENSIVE classification -> no warning (only fires for MINIMAL)."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="COMPREHENSIVE", files_affected=2)
        _write_file_modified_events(run_dir, 30)

        result = _check_complexity_drift(_read_run_yaml(run_dir, reader), _read_run_events(run_dir, reader))

        assert result is None


@pytest.mark.integration
class TestCheckDeliveryGatesComplexityDrift:
    """Wiring test: complexity drift flows through check_delivery_gates."""

    def test_drift_warning_surfaces_in_delivery_gates(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """check_delivery_gates includes complexity_drift_warning when drift detected."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 13)

        result = check_delivery_gates(run_dir, reader)

        assert "complexity_drift_warning" in result
        assert "Complexity drift detected" in str(result["complexity_drift_warning"])

    def test_no_drift_warning_in_delivery_gates_when_under_threshold(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """check_delivery_gates omits complexity_drift_warning when no drift."""
        run_dir = tmp_path / "run"
        _write_run_yaml(run_dir, complexity_class="MINIMAL", files_affected=1)
        _write_file_modified_events(run_dir, 3)

        result = check_delivery_gates(run_dir, reader)

        assert "complexity_drift_warning" not in result


# ── codex cross-model review #2: stale build evidence ──────────────────────


def _build_pass(ts: str) -> dict[str, object]:
    return {"ts": ts, "event": "build_check_complete", "tests_passed": True, "static_checks_clean": True}


def _edit(ts: str) -> dict[str, object]:
    return {"ts": ts, "event": "file_modified", "data": {"path": "src/x.py"}}


@pytest.mark.integration
class TestStaleBuildEvidence:
    """FRAMEWORK.md §'Build evidence MUST postdate the last change it covers'.

    A passing build followed by a later edit is STALE — the recorded build no
    longer covers the current tree (codex cross-model review).
    """

    def test_pass_then_edit_is_stale(self) -> None:
        events = [_build_pass("2026-06-11T00:00:00Z"), _edit("2026-06-11T00:00:05Z")]
        assert _build_evidence_is_stale(events) is True

    def test_edit_then_pass_is_not_stale(self) -> None:
        events = [_edit("2026-06-11T00:00:00Z"), _build_pass("2026-06-11T00:00:05Z")]
        assert _build_evidence_is_stale(events) is False

    def test_equal_timestamps_not_stale(self) -> None:
        # An edit at the SAME ts as the build did not happen strictly after it.
        events = [_build_pass("2026-06-11T00:00:00Z"), _edit("2026-06-11T00:00:00Z")]
        assert _build_evidence_is_stale(events) is False

    def test_fractional_python_build_after_hook_z_edit_is_fresh(self) -> None:
        events = [
            _edit("2026-07-11T12:00:00Z"),
            _build_pass("2026-07-11T12:00:00.900000+00:00"),
        ]
        assert _build_evidence_is_stale(events) is False

    def test_fractional_python_edit_after_hook_z_build_is_stale(self) -> None:
        events = [
            _build_pass("2026-07-11T12:00:00Z"),
            _edit("2026-07-11T12:00:00.900000+00:00"),
        ]
        assert _build_evidence_is_stale(events) is True

    def test_no_passing_build_not_stale_here(self) -> None:
        # Missing-build is handled by the no-passing-build branch, not staleness.
        events = [_edit("2026-06-11T00:00:00Z")]
        assert _build_evidence_is_stale(events) is False

    def test_no_edits_not_stale(self) -> None:
        events = [_build_pass("2026-06-11T00:00:00Z")]
        assert _build_evidence_is_stale(events) is False

    def test_latest_pass_wins_when_re_run_after_edit(self) -> None:
        # pass(t0) -> edit(t1) -> pass(t2): the LATEST passing build postdates the
        # edit, so evidence is fresh again.
        events = [
            _build_pass("2026-06-11T00:00:00Z"),
            _edit("2026-06-11T00:00:05Z"),
            _build_pass("2026-06-11T00:00:10Z"),
        ]
        assert _build_evidence_is_stale(events) is False

    def test_stale_sets_build_warning(self) -> None:
        events = [_build_pass("2026-06-11T00:00:00Z"), _edit("2026-06-11T00:00:05Z")]
        build_warning, _premature = _check_build_and_work_events(events)
        assert build_warning is not None
        assert "Stale build evidence" in build_warning

    def test_fresh_evidence_no_build_warning(self) -> None:
        events = [_edit("2026-06-11T00:00:00Z"), _build_pass("2026-06-11T00:00:05Z")]
        build_warning, _premature = _check_build_and_work_events(events)
        assert build_warning is None

    def test_stale_blocks_under_block_coding_mode(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """A stale build on a coding-task run promotes to delivery_blocked (block_coding)."""
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: r\nstatus: active\nphase: deliver\ntask_type: coding\ncomplexity_class: MINIMAL\n",
            encoding="utf-8",
        )
        lines = [
            json.dumps({"ts": "2026-06-11T00:00:00Z", "event": "session_start"}),
            json.dumps(_build_pass("2026-06-11T00:00:01Z")),
            json.dumps(_edit("2026-06-11T00:00:05Z")),
        ]
        (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = check_delivery_gates(run_dir, reader, tmp_path / ".trw")

        assert "delivery_blocked" in result
        assert "build_check" == result.get("missing_gate")

    def test_fresh_evidence_does_not_block(
        self,
        tmp_path: Path,
        reader: FileStateReader,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """edit-then-pass on a coding-task run is clean (no delivery_blocked)."""
        legacy_config = TRWConfig().model_copy(update={"evidence_receipt_mode": "observe"})
        monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: legacy_config)
        run_dir = tmp_path / "run"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: r\nstatus: active\nphase: deliver\ntask_type: coding\ncomplexity_class: MINIMAL\n",
            encoding="utf-8",
        )
        lines = [
            json.dumps({"ts": "2026-06-11T00:00:00Z", "event": "session_start"}),
            json.dumps(_edit("2026-06-11T00:00:01Z")),
            json.dumps(_build_pass("2026-06-11T00:00:05Z")),
        ]
        (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = check_delivery_gates(run_dir, reader, tmp_path / ".trw")

        assert "delivery_blocked" not in result
        assert "build_gate_warning" not in result


# ---------------------------------------------------------------------------
# PRD-QUAL-119-FR06: delivery consumes universal completion truth
# ---------------------------------------------------------------------------


def _write_scoped_run(tmp_path: Path, prd_id: str, status: str) -> tuple[Path, Path]:
    """Run dir with prd_scope + a PRD file at the given lifecycle status."""
    import yaml

    run_dir = tmp_path / "run"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text(
        yaml.safe_dump({"run_id": "r1", "prd_scope": [prd_id], "task_type": "coding"}),
        encoding="utf-8",
    )
    prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True)
    (prds_dir / f"{prd_id}.md").write_text(
        f"---\nprd:\n  id: {prd_id}\n  title: T\n  status: {status}\n  priority: P0\n"
        f"functionality_level: planned\n---\n# {prd_id}\n",
        encoding="utf-8",
    )
    return run_dir, prds_dir


def test_prd_qual_119_fr06(tmp_path: Path) -> None:
    """FR06 acceptance: delivery-driven promotion consumes the typed decision —
    only current complete satisfies the lifecycle claim, each non-complete
    field stays distinct, and a MISSING guard fails closed (the L-EQwV
    incident: deferred auto-progress walked planned PRDs to done)."""
    from trw_mcp.state.validation.prd_progression import auto_progress_prds

    prd_id = "PRD-CORE-901"
    run_dir, prds_dir = _write_scoped_run(tmp_path, prd_id, "approved")
    config = TRWConfig()

    # FAIL-CLOSED regression (the incident): no guard injected -> NO promotion.
    results = auto_progress_prds(run_dir, "deliver", prds_dir, config)
    assert len(results) == 1
    assert results[0]["applied"] is False
    assert results[0]["reason"] == "effective_completion:completion_decision_unavailable"
    content = (prds_dir / f"{prd_id}.md").read_text(encoding="utf-8")
    assert "status: approved" in content  # lifecycle untouched

    # Distinct non-complete outcomes refuse with the outcome named.
    for block_reason in (
        "incomplete: absent: build_evidence",
        "externally_blocked: pypi_release",
        "rolled_back: safety rollback",
        "unknown: stale: repo_health_receipt",
    ):
        results = auto_progress_prds(
            run_dir, "deliver", prds_dir, config, completion_guard=lambda _pid, r=block_reason: r
        )
        assert results[0]["applied"] is False
        assert results[0]["reason"] == f"effective_completion:{block_reason}"

    # Only a COMPLETE decision (guard returns None) permits the claim; the
    # remaining state-machine guards then govern as before.
    results = auto_progress_prds(run_dir, "deliver", prds_dir, config, completion_guard=lambda _pid: None)
    assert len(results) == 1
    # Promotion may proceed (or stop at an existing transition guard) — but the
    # completion gate itself no longer refuses.
    assert not str(results[0].get("reason", "")).startswith("effective_completion:")


def test_qual_119_fr06_non_completion_phases_unaffected(tmp_path: Path) -> None:
    """A phase whose target is NOT implemented-family needs no completion guard."""
    from trw_mcp.state.validation.prd_progression import auto_progress_prds

    prd_id = "PRD-CORE-902"
    run_dir, prds_dir = _write_scoped_run(tmp_path, prd_id, "draft")
    results = auto_progress_prds(run_dir, "plan", prds_dir, TRWConfig())
    # draft -> review carries no completion claim; guard absence changes nothing.
    assert all(not str(r.get("reason", "")).startswith("effective_completion:") for r in results)


def test_qual_119_fr06_real_guard_blocks_planned_prd_with_build_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-audit F1/F2 regression (incident L-EQwV): drive the REAL production
    guard (_do_auto_progress) over a still-approved, functionality_level:planned
    PRD in a run that HAS passing build evidence — the exact condition under
    which deferred auto-progress previously walked planned PRDs to done. The
    promotion must be refused and the PRD bytes untouched."""
    import json

    from trw_mcp.tools._deferred_steps_learning import _do_auto_progress

    prd_id = "PRD-CORE-903"
    run_dir, prds_dir = _write_scoped_run(tmp_path, prd_id, "approved")
    # Passing build evidence (the norm at deliver time).
    (run_dir / "meta" / "events.jsonl").write_text(
        json.dumps(
            {
                "event": "build_check_complete",
                "data": {"tests_passed": True, "static_checks_clean": True},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    result = _do_auto_progress(run_dir)

    assert result["status"] == "success"
    progressions = result["progressions"]
    assert len(progressions) == 1
    assert progressions[0]["applied"] is False
    reason = str(progressions[0]["reason"])
    assert reason.startswith("effective_completion:")
    assert "incomplete" in reason  # target-status evaluation fires FPI #7
    content = (prds_dir / f"{prd_id}.md").read_text(encoding="utf-8")
    assert "status: approved" in content and "functionality_level: planned" in content


def test_qual_119_p09_gate_default_is_block() -> None:
    """P09 activation + superseded-default removal proof: the shipped default
    enforces completion truth; the warn-era default cannot win silently."""
    assert TRWConfig().prd_transition_gate == "block"


def test_qual_120_deliver_writes_acceptance_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """QUAL-120 P09 activation: the deliver default path persists an out-of-band
    AcceptanceManifest per scoped PRD, carrying the derived completion outcome,
    without touching the authored PRD bytes."""
    import json

    from trw_mcp.state.acceptance_manifest import load_manifest
    from trw_mcp.tools._deferred_steps_learning import _do_auto_progress

    prd_id = "PRD-CORE-904"
    run_dir, prds_dir = _write_scoped_run(tmp_path, prd_id, "approved")
    (run_dir / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "build_check_complete", "data": {"tests_passed": True}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    prd_bytes_before = (prds_dir / f"{prd_id}.md").read_bytes()

    result = _do_auto_progress(run_dir)

    assert result["status"] == "success"
    manifest = load_manifest(tmp_path / ".trw", prd_id)
    assert manifest is not None
    assert manifest.prd_id == prd_id
    # The guard refused promotion (planned PRD), and the manifest records the
    # non-complete outcome out-of-band.
    assert manifest.completion_outcome == "incomplete"
    assert (prds_dir / f"{prd_id}.md").read_bytes() == prd_bytes_before


def test_qual_120_f7_partial_transition_never_projects_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit F7 empirical reproduction (P0): a PRD whose coherence checks pass
    HYPOTHETICALLY (live level, empty stubs, valid default_path_proof) but
    whose transition stalls short of the implemented family must never get
    completion_outcome=complete — the manifest binds ACTUAL resulting status."""
    import json

    from trw_mcp.state.acceptance_manifest import load_manifest
    from trw_mcp.tools._deferred_steps_learning import _do_auto_progress

    prd_id = "PRD-CORE-905"
    run_dir = tmp_path / "run"
    (run_dir / "meta").mkdir(parents=True)
    import yaml

    (run_dir / "meta" / "run.yaml").write_text(
        yaml.safe_dump({"run_id": "r1", "prd_scope": [prd_id], "task_type": "coding"}),
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "build_check_complete", "data": {"tests_passed": True}}) + "\n",
        encoding="utf-8",
    )
    prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds_dir.mkdir(parents=True)
    # Hypothetically coherent (live + empty stubs + content-bound proof) but a
    # sparse draft body: the quality-tier transition guard stalls the BFS
    # before the implemented family. Actual resulting status != implemented.
    (prds_dir / f"{prd_id}.md").write_text(
        f"---\nprd:\n  id: {prd_id}\n  title: T\n  status: draft\n  priority: P3\n"
        "functionality_level: live\nstubs: []\n"
        "default_path_proof:\n  receipt: tests/t.py::t\n"
        f"  source_digest: sha256:{'a' * 64}\n"
        "  removal_assertion: tests/t.py::absent\n---\n# sparse\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    result = _do_auto_progress(run_dir)
    assert result["status"] == "success"

    manifest = load_manifest(tmp_path / ".trw", prd_id)
    assert manifest is not None
    # Whatever the hypothetical coherence said, the file never reached the
    # implemented family — the manifest must not claim complete.
    final_status = (prds_dir / f"{prd_id}.md").read_text(encoding="utf-8")
    if "status: implemented" not in final_status and "status: done" not in final_status:
        assert manifest.completion_outcome != "complete", manifest.completion_outcome


def test_qual_120_happy_path_complete_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Auditor follow-up: a PRD that GENUINELY reaches the implemented family
    persists completion_outcome=complete — the F7 cross-check only ever narrows
    complete->incomplete, never inverts a genuine success."""
    import json

    from trw_mcp.state.acceptance_manifest import load_manifest
    from trw_mcp.tools._deferred_steps_learning import _do_auto_progress

    prd_id = "PRD-CORE-906"
    run_dir, prds_dir = _write_scoped_run(tmp_path, prd_id, "approved")
    # Rewrite as a coherent, ALREADY-implemented live PRD (no transition needed).
    (prds_dir / f"{prd_id}.md").write_text(
        f"---\nprd:\n  id: {prd_id}\n  title: T\n  status: done\n  priority: P3\n"
        "functionality_level: live\nstubs: []\n"
        "default_path_proof:\n  receipt: tests/t.py::t\n"
        f"  source_digest: sha256:{'b' * 64}\n"
        "  removal_assertion: tests/t.py::absent\n---\n# body\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "build_check_complete", "data": {"tests_passed": True}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    result = _do_auto_progress(run_dir)
    assert result["status"] == "success"
    manifest = load_manifest(tmp_path / ".trw", prd_id)
    assert manifest is not None
    assert manifest.completion_outcome == "complete"
