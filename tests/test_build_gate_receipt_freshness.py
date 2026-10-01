"""PRD-CORE-205 FR05 wiring — content-bound build staleness in the delivery gate."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import TreeStatus
from trw_mcp.state._tree_binding import snapshot_tree
from trw_mcp.tools._delivery_build_gates import build_receipt_content_stale_warning, build_receipt_gate_findings
from trw_mcp.tools._evidence_writers import latest_build_receipt, load_latest_build_evidence, record_build_receipt


def _run_with_build_receipt(project: Path, files: dict[str, str]) -> Path:
    run = project / ".trw" / "runs" / "task" / "run1"
    meta = run / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    lines = []
    for rel, content in files.items():
        p = project / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        lines.append(json.dumps({"event": "file_modified", "file": str(p)}))
    (meta / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = record_build_receipt(
        run,
        project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=90.0,
        policy_mode="observe",
    )
    assert out is not None and out.ok
    return run


class TestBuildReceiptContentStaleWarning:
    def test_no_warning_when_content_current(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        assert build_receipt_content_stale_warning(run) is None

    def test_warning_when_bound_byte_changes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        # Edit a bound file AFTER the build receipt was recorded.
        (project / "src" / "a.py").write_text("EDITED AFTER BUILD", encoding="utf-8")
        warning = build_receipt_content_stale_warning(run)
        assert warning is not None
        assert "Content-stale build evidence" in warning

    def test_no_warning_for_unrelated_out_of_scope_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        # Another agent's unrelated file — NOT in this run's bound scope.
        (project / "src" / "unrelated.py").write_text("other work", encoding="utf-8")
        assert build_receipt_content_stale_warning(run) is None

    def test_no_run_is_not_applicable_but_empty_run_lacks_evidence(self, tmp_path: Path) -> None:
        assert build_receipt_content_stale_warning(None) is None
        empty_run = tmp_path / "empty"
        (empty_run / "meta").mkdir(parents=True)
        warning = build_receipt_content_stale_warning(empty_run)
        assert warning is not None and "No usable build check was found" in warning


# --- E2E-INC-018: whole-working-tree binding (edits the run journal never saw) -------------------


def _git(project: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


def _git_project_with_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Committed app.py; a run whose journal names it; a passing build receipt recorded on top."""
    project = tmp_path / "proj"
    project.mkdir()
    _git(project, "init", "-q")
    (project / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (project / "app.py").write_text("v1\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "init")
    # app.py was committed outside this run: the journal has no event for it, exactly the
    # E2E-INC-018 shape (later edits via a shell never produce one either).
    run = _run_with_build_receipt(project, {})
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    return project, run


class TestWorkingTreeBinding:
    def test_no_edit_passes_and_is_bound(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _, run = _git_project_with_receipt(tmp_path, monkeypatch)
        assert build_receipt_gate_findings(run) == (None, None)

    def test_shell_edit_without_journal_event_is_stale_and_names_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        # `sed -i` from a shell: bytes change, no Write/Edit event reaches the journal.
        (project / "app.py").write_text("v2 edited via shell\n", encoding="utf-8")
        warning, advisory = build_receipt_gate_findings(run)
        assert advisory is None
        assert warning is not None
        assert "bound_tree_changed" in warning and "app.py" in warning
        assert "re-run validation" in warning and "another agent" in warning

    def test_commit_of_unchanged_content_stays_current(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        _git(project, "commit", "-q", "--allow-empty", "-m", "no content change")
        assert build_receipt_gate_findings(run) == (None, None)

    def test_edit_then_commit_still_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (project / "app.py").write_text("v2\n", encoding="utf-8")
        _git(project, "commit", "-q", "-am", "edit")
        warning, _ = build_receipt_gate_findings(run)
        assert warning is not None and "app.py" in warning

    def test_writes_under_trw_dir_do_not_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (run / "meta" / "review.yaml").write_text("verdict: pass\n", encoding="utf-8")
        (run / "meta" / "checkpoints.jsonl").write_text('{"message": "m"}\n', encoding="utf-8")
        with (run / "meta" / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "checkpoint"}) + "\n")
        context = project / ".trw" / "context"
        context.mkdir(parents=True, exist_ok=True)
        (context / "ceremony-state.json").write_text("{}", encoding="utf-8")
        assert build_receipt_gate_findings(run) == (None, None)

    def test_tracked_trw_state_committed_after_check_stays_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (project / ".trw" / "config.yaml").write_text("x: 1\n", encoding="utf-8")
        _git(project, "add", "-f", ".trw/config.yaml")
        _git(project, "commit", "-q", "-m", "track trw state")
        (project / ".trw" / "config.yaml").write_text("x: 2\n", encoding="utf-8")
        assert build_receipt_gate_findings(run) == (None, None)

    def test_new_untracked_file_is_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (project / "new_module.py").write_text("x = 1\n", encoding="utf-8")
        warning, _ = build_receipt_gate_findings(run)
        assert warning is not None and "new_module.py" in warning

    def test_edit_of_ignored_file_does_not_stale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (project / "ignored.log").write_text("noise\n", encoding="utf-8")
        assert build_receipt_gate_findings(run) == (None, None)

    def test_real_index_and_refs_are_untouched(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (project / "app.py").write_text("v2\n", encoding="utf-8")
        (project / "new.py").write_text("n\n", encoding="utf-8")

        def observe() -> tuple[str, str, bytes]:
            status = _git(project, "--no-optional-locks", "status", "-s")
            return status, _git(project, "show-ref"), (project / ".git" / "index").read_bytes()

        before = observe()
        build_receipt_gate_findings(run)
        assert observe() == before

    def test_temp_index_is_removed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, _ = _git_project_with_receipt(tmp_path, monkeypatch)
        created: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*args: object, **kwargs: object) -> str:
            path = real_mkdtemp(*args, **kwargs)  # type: ignore[call-overload]
            created.append(path)
            return str(path)

        monkeypatch.setattr("trw_mcp.state._tree_binding.tempfile.mkdtemp", spy)
        assert snapshot_tree(project, (".trw",)).tree_sha is not None
        assert created and not any(Path(p).exists() for p in created)


class TestTreeUnbound:
    def test_non_git_dir_is_unbound_advisory_never_bound(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        warning, advisory = build_receipt_gate_findings(run)
        assert warning is None  # advisory, not blocking
        assert advisory is not None and "build_pass_tree_unbound" in advisory and "UNBOUND" in advisory
        outcome, _ = load_latest_build_evidence(run, project)
        assert outcome.tree_status is TreeStatus.UNBOUND
        assert outcome.reason_code == "build_pass_tree_unbound"  # never the "current" reason

    def test_non_git_reason_code_recorded_on_receipt(self, tmp_path: Path) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})
        receipt = latest_build_receipt(run)
        assert receipt is not None
        assert receipt.content_binding.tree_digest is None
        assert receipt.content_binding.tree_unbound_reason == "tree_unbound_not_git_repo"

    def test_budget_exceeded_at_record_time_is_unbound(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _git(project, "init", "-q")
        monkeypatch.setattr("trw_mcp.state._tree_binding.TREE_BUDGET_SECS", 0.0)
        run = _run_with_build_receipt(project, {"a.py": "x\n"})
        receipt = latest_build_receipt(run)
        assert receipt is not None
        assert receipt.content_binding.tree_digest is None
        assert receipt.content_binding.tree_unbound_reason == "tree_unbound_budget_exceeded"

    def test_git_error_at_record_time_is_unbound(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        _git(project, "init", "-q")
        real_run = subprocess.run

        def failing(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            if argv[:2] == ["git", "add"]:
                return subprocess.CompletedProcess(argv, 128, "", "fatal: boom")
            return real_run(argv, *args, **kwargs)  # type: ignore[call-overload, no-any-return]

        monkeypatch.setattr("trw_mcp.state._tree_binding.subprocess.run", failing)
        snap = snapshot_tree(project, (".trw",))
        assert snap.tree_sha is None and snap.unbound_reason == "tree_unbound_git_error"

    def test_recheck_failure_after_bound_receipt_is_unbound_not_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, run = _git_project_with_receipt(tmp_path, monkeypatch)
        monkeypatch.setattr("trw_mcp.state._tree_binding.TREE_BUDGET_SECS", 0.0)
        warning, advisory = build_receipt_gate_findings(run)
        assert warning is None
        assert advisory is not None and "tree_unbound_recheck_failed:tree_unbound_budget_exceeded" in advisory

    def test_git_missing_is_unbound(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError("git")

        monkeypatch.setattr("trw_mcp.state._tree_binding.subprocess.run", boom)
        snap = snapshot_tree(tmp_path, ())
        assert snap.tree_sha is None and snap.unbound_reason == "tree_unbound_git_unavailable"

    def test_legacy_receipt_without_tree_fields_is_unbound(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        path = next((run / "meta" / "receipts" / "build").glob("*.json"))
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("tree_digest", "tree_excludes", "tree_unbound_reason"):
            data["content_binding"].pop(key, None)
        path.write_text(json.dumps(data), encoding="utf-8")
        (project / "app.py").write_text("edited\n", encoding="utf-8")
        warning, advisory = build_receipt_gate_findings(run)
        assert warning is None and advisory is not None and "tree_unbound_legacy_receipt" in advisory


class TestBuildCheckToDeliverFlow:
    def test_checkpoint_review_then_deliver_gate_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_check -> checkpoint -> review artifacts -> deliver-time build gate stays green."""
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (run / "meta" / "checkpoints.jsonl").write_text('{"message": "milestone"}\n', encoding="utf-8")
        (run / "meta" / "review.yaml").write_text("verdict: pass\nfindings: []\n", encoding="utf-8")
        (run / "reports").mkdir(exist_ok=True)
        (run / "reports" / "final.md").write_text("done\n", encoding="utf-8")
        assert build_receipt_gate_findings(run) == (None, None)
        # ...and the same flow goes red the moment the code moves under it.
        (project / "app.py").write_text("sneaky\n", encoding="utf-8")
        assert build_receipt_gate_findings(run)[0] is not None


def _preview(run: Path, project: Path) -> dict[str, object]:
    from trw_mcp.tools._orchestration_gate_scan import compute_deliver_gate_status

    events: list[dict[str, object]] = [
        {
            "event": "build_check_complete",
            "data": {"test_count": 10, "scope": "full", "tests_passed": True, "static_checks_clean": True},
        }
    ]
    return dict(compute_deliver_gate_status(events, project / ".trw", run))


class TestStatusPreviewSeesTreeBinding:
    """trw_status previews the same tree-binding verdict deliver enforces (E2E-INC-018)."""

    def test_untouched_tree_is_ready_without_tree_advisory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        status = _preview(run, project)
        assert status["build_gate_ready"] is True
        assert "working tree" not in str(status["deliver_gate_summary"])

    def test_shell_edit_is_not_ready_and_names_the_cause(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        (project / "app.py").write_text("changed by sed -i\n", encoding="utf-8")
        status = _preview(run, project)
        assert status["build_gate_ready"] is False
        summary = str(status["deliver_gate_summary"])
        assert "bound_tree_changed" in summary and "app.py" in summary
        assert summary != "READY"

    def test_commit_after_build_check_stays_ready(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        project, run = _git_project_with_receipt(tmp_path, monkeypatch)
        _git(project, "commit", "-q", "--allow-empty", "-m", "same content")
        status = _preview(run, project)
        assert status["build_gate_ready"] is True
        assert "bound_tree_changed" not in str(status["deliver_gate_summary"])

    def test_unbound_binding_is_surfaced_not_silently_ready(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = tmp_path / "proj"
        project.mkdir()
        run = _run_with_build_receipt(project, {"src/a.py": "code"})  # non-git: UNBOUND at record time
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
        status = _preview(run, project)
        assert status["build_gate_ready"] is True  # advisory, not blocking
        summary = str(status["deliver_gate_summary"])
        assert "not bound to the working tree" in summary and "build_pass_tree_unbound" in summary


class TestTreeBlindSpots:
    """codex r1 known issues (W5): an index entry the digest cannot see through unbinds the tree, never 'current'."""

    def _repo(self, tmp_path: Path) -> Path:
        project = tmp_path / "proj"
        project.mkdir()
        _git(project, "init", "-q")
        (project / "app.py").write_text("v1\n", encoding="utf-8")
        (project / "other.py").write_text("o\n", encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", "init")
        return project

    def test_assume_unchanged_entry_is_unbound_and_named(self, tmp_path: Path) -> None:
        project = self._repo(tmp_path)
        _git(project, "update-index", "--assume-unchanged", "app.py")
        snap = snapshot_tree(project, (".trw",))
        assert snap.tree_sha is None
        assert snap.unbound_reason == "tree_unbound_hidden_index_entries:app.py"

    def test_skip_worktree_entry_present_on_disk_is_unbound(self, tmp_path: Path) -> None:
        project = self._repo(tmp_path)
        _git(project, "update-index", "--skip-worktree", "app.py")
        snap = snapshot_tree(project, (".trw",))
        assert snap.tree_sha is None and snap.unbound_reason.startswith("tree_unbound_hidden_index_entries:")

    def test_skip_worktree_entry_absent_from_disk_stays_bound(self, tmp_path: Path) -> None:
        # The outside of a sparse checkout: nothing on disk can hide an edit.
        project = self._repo(tmp_path)
        _git(project, "update-index", "--skip-worktree", "app.py")
        (project / "app.py").unlink()
        assert snapshot_tree(project, (".trw",)).tree_sha is not None

    def test_skip_worktree_entry_that_is_a_dangling_symlink_is_unbound(self, tmp_path: Path) -> None:
        # A dangling symlink is still an on-disk entry (lexists), so the skip-worktree flag can hide an edit to it.
        project = self._repo(tmp_path)
        _git(project, "update-index", "--skip-worktree", "app.py")
        (project / "app.py").unlink()
        (project / "app.py").symlink_to(project / "no-such-target")
        snap = snapshot_tree(project, (".trw",))
        assert snap.tree_sha is None
        assert snap.unbound_reason == "tree_unbound_hidden_index_entries:app.py"

    def test_submodule_gitlink_is_unbound_and_named(self, tmp_path: Path) -> None:
        project = self._repo(tmp_path)
        head = _git(project, "rev-parse", "HEAD").strip()
        _git(project, "update-index", "--add", "--cacheinfo", f"160000,{head},vendor/lib")
        snap = snapshot_tree(project, (".trw",))
        assert snap.tree_sha is None
        assert snap.unbound_reason == "tree_unbound_submodule:vendor/lib"


def test_a_temp_dir_inside_the_checkout_is_refused_not_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Policy census: the temp index lives outside the checkout; TMPDIR pointing inside it unbinds instead."""
    project = TestTreeBlindSpots()._repo(tmp_path)
    inside = project / "tmp-inside"
    inside.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(inside))
    snap = snapshot_tree(project, (".trw",))
    assert snap.tree_sha is None and snap.unbound_reason == "tree_unbound_git_error"
    assert list(inside.iterdir()) == []  # the refused temp dir was removed, nothing left in the checkout
