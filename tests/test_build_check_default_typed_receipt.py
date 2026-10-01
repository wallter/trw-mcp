"""trw_build_check writes a typed BuildReceipt from scalar arguments (BUILD-CHECK-TYPED-RECEIPT-DEFAULT)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models._evidence_records import BuildReceipt


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "src" / "feature.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True, exist_ok=True)
    (run / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return run


def _receipts(run: Path) -> list[BuildReceipt]:
    files = sorted((run / "meta" / "receipts" / "build").glob("*.json"))
    return [BuildReceipt.model_validate_json(p.read_text()) for p in files]


def test_scalar_call_writes_typed_receipt_with_synthesized_results(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    result = build_check_invoke(tests_passed=True, static_checks_clean=True, scope="pytest -q unit", run_path=str(run))

    assert result["typed_receipt_state"] == "written"
    (receipt,) = _receipts(run)
    assert result["build_receipt_id"] == receipt.receipt_id
    by_id = {r.command_id: r for r in receipt.command_results}
    assert set(by_id) == {"tests", "static_checks"}
    assert by_id["tests"].label == "pytest -q unit"
    assert by_id["tests"].command_class.value == "test"
    assert by_id["tests"].exit_code == 0
    assert by_id["static_checks"].command_class.value == "static"
    assert by_id["static_checks"].exit_code == 0


def _deliver_gate_warning(run: Path) -> str | None:
    """The deliver-time typed build gate, read from the receipt the tool just wrote."""
    from trw_mcp.tools._delivery_build_gates import build_receipt_content_stale_warning

    return build_receipt_content_stale_warning(run)


NOT_RECORDED_MESSAGE = "static_checks_clean was not recorded; record true/false from your project's static checks"


def test_static_checks_unreported_is_recorded_as_not_run_never_as_passed(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    result = build_check_invoke(
        tests_passed=True, test_count=12, scope="full", run_path=str(run), static_checks_clean=None
    )

    assert result["typed_receipt_state"] == "written"
    assert result["static_checks_clean"] == "not_run", "the echo must not claim static checks were clean"
    (receipt,) = _receipts(run)
    by_id = {r.command_id: r for r in receipt.command_results}
    assert set(by_id) == {"tests", "static_checks"}, "the plan requires both commands"
    static = by_id["static_checks"]
    assert static.exit_code != 0
    assert static.not_run is True
    assert static.limitations == "not run (static_checks_clean omitted)"
    assert by_id["tests"].not_run is False
    assert receipt.legacy_static_checks_clean is not True
    assert receipt.derived_outcome(("tests", "static_checks"), None) is False


def test_omitted_static_blocks_deliver_and_names_the_cause(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-001: the refusal says what to do, not "no passing build" nor a legacy contradiction."""
    run = _run(tmp_path, monkeypatch)
    build_check_invoke(tests_passed=True, test_count=12, scope="full", run_path=str(run))

    warning = _deliver_gate_warning(run)
    assert warning is not None
    assert NOT_RECORDED_MESSAGE in warning
    assert "build_legacy_contradiction" not in warning
    assert "No usable build check was found" not in warning


@pytest.mark.parametrize(
    ("static", "blocks"),
    [(True, False), (False, True)],
    ids=["explicit-true-passes", "explicit-false-blocks"],
)
def test_explicit_static_outcome_gates_deliver(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, static: bool, blocks: bool
) -> None:
    run = _run(tmp_path, monkeypatch)
    result = build_check_invoke(
        tests_passed=True, test_count=12, scope="full", run_path=str(run), static_checks_clean=static
    )

    assert result["static_checks_clean"] is static
    warning = _deliver_gate_warning(run)
    assert (warning is not None) is blocks
    if warning is not None:
        assert NOT_RECORDED_MESSAGE not in warning, "an explicit false is a failure, not an omission"


def test_status_preview_agrees_with_deliver_for_omitted_static(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The preview reuses the deliver-path receipt validation, so it is never READY when deliver blocks."""
    from trw_mcp.tools._orchestration_gate_scan import _build_gate_ready

    run = _run(tmp_path, monkeypatch)
    build_check_invoke(tests_passed=True, test_count=12, scope="full", run_path=str(run))
    events = [json.loads(line) for line in (run / "meta" / "events.jsonl").read_text().splitlines() if line.strip()]
    assert _build_gate_ready(events, run) is False
    assert _deliver_gate_warning(run) is not None


def _build_event(*, tests_passed: bool) -> dict[str, object]:
    return {
        "event": "build_check_complete",
        "data": {"test_count": 10, "scope": "full", "tests_passed": tests_passed, "static_checks_clean": True},
    }


@pytest.mark.parametrize("missing_build", [True, False], ids=["no-pass-recorded", "earlier-pass-then-fail"])
def test_status_preview_blocks_on_latest_failed_build_in_a_docs_run(tmp_path: Path, missing_build: bool) -> None:
    """E2E S3-F2: deliver blocks a latest failed build whatever the gate mode; the preview must too."""
    from trw_mcp.tools._orchestration_gate_scan import _build_gate_would_block

    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text("task_type: docs\n", encoding="utf-8")
    events = [_build_event(tests_passed=True), _build_event(tests_passed=False)]

    assert _build_gate_would_block(run, missing_build, events) is True


def test_plain_failing_tests_are_reported_as_a_failed_command_not_a_legacy_contradiction(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    build_check_invoke(
        tests_passed=False, test_count=10, failure_count=2, static_checks_clean=True, scope="full", run_path=str(run)
    )

    warning = _deliver_gate_warning(run)
    assert warning is not None
    assert "build_required_command_failed" in warning
    assert "build_legacy_contradiction" not in warning


def test_deliver_gate_projection_source_has_no_or_omitted_and_mirrors_canon() -> None:
    """Canon 1.a Path 1 has no "(or omitted)"; neither may the source that renders Deliver Gate (a)."""
    from trw_mcp.state.claude_md.sections._tool_lifecycle import (
        _FALLBACK_TOOL_LIFECYCLE,
        load_tool_lifecycle,
        render_deliver_gate_statement,
    )

    for text in (_FALLBACK_TOOL_LIFECYCLE, load_tool_lifecycle(), render_deliver_gate_statement()):
        assert "or omitted" not in text
        assert "`static_checks_clean=true`" in text


def test_oversized_scope_still_writes_a_receipt_with_bounded_labels(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models._evidence_core import EvidenceLimits

    run = _run(tmp_path, monkeypatch)
    scope = "é" * (EvidenceLimits.MAX_FREE_TEXT_BYTES + 100)
    result = build_check_invoke(tests_passed=True, static_checks_clean=True, scope=scope, run_path=str(run))

    assert result["typed_receipt_state"] == "written"
    assert result["scope"] == scope, "the full scope stays in the response/status cache"
    (receipt,) = _receipts(run)
    for r in receipt.command_results:
        assert 0 < len(r.label.encode("utf-8")) <= EvidenceLimits.MAX_FREE_TEXT_BYTES


def test_failed_tests_synthesize_exit_code_one(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    build_check_invoke(tests_passed=False, static_checks_clean=False, scope="full", run_path=str(run))

    (receipt,) = _receipts(run)
    assert {r.command_id: r.exit_code for r in receipt.command_results} == {"tests": 1, "static_checks": 1}


def test_explicit_command_results_are_not_replaced(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    supplied = [
        {"command_id": "tests", "label": "pytest -q", "command_class": "test", "exit_code": 0, "test_count": 7},
        {"command_id": "static_checks", "label": "ruff", "command_class": "static", "exit_code": 0},
    ]
    build_check_invoke(command_results=supplied, run_path=str(run))

    (receipt,) = _receipts(run)
    assert [r.label for r in receipt.command_results] == ["pytest -q", "ruff"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def test_receipt_git_sha_is_none_outside_a_git_tree(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    build_check_invoke(scope="full", run_path=str(run))
    assert _receipts(run)[0].git_sha is None


def test_receipt_git_sha_is_head_of_a_clean_git_tree(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    (tmp_path / ".gitignore").write_text(".trw/\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    _git(tmp_path, "add", ".gitignore", "src")
    _git(tmp_path, "commit", "-qm", "base")
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    build_check_invoke(scope="full", run_path=str(run))
    assert _receipts(run)[0].git_sha == head


def test_build_check_response_reports_working_tree_binding_truthfully(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-INC-018: non-git is UNBOUND with a reason; a git repo is BOUND. Never a bare 'current'."""
    run = _run(tmp_path, monkeypatch)
    unbound = build_check_invoke(tests_passed=True, static_checks_clean=True, scope="s", run_path=str(run))
    assert unbound["tree_binding"] == "unbound"
    assert unbound["tree_binding_reason"] == "tree_unbound_not_git_repo"

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    bound = build_check_invoke(tests_passed=True, static_checks_clean=True, scope="s", run_path=str(run))
    assert bound["tree_binding"] == "bound"
    assert "tree_binding_reason" not in bound
    by_id = {r.receipt_id: r for r in _receipts(run)}
    assert by_id[str(bound["build_receipt_id"])].content_binding.tree_digest


def test_a_not_run_result_is_never_a_pass_even_with_exit_code_zero() -> None:
    """codex r1 known issue (W5): the not_run invariant is enforced by the model, not only commented."""
    from trw_mcp.models._evidence_plans import BuildCommandResult, CommandClass

    hand_built = BuildCommandResult(
        command_id="static_checks", label="static", command_class=CommandClass.STATIC, exit_code=0, not_run=True
    )
    assert hand_built.passed is False
