"""UF-GATES-02 slice 1: per-test-file receipts recorded from a pytest junit report.

The acceptance manifest needs an EXECUTED receipt per requirement (PRD-QUAL-120): proof that the requirement's
mapped test file ran and passed in this run. Nothing produced one, so every requirement came out UNKNOWN. This
slice records, per test file, the junit outcome counts plus a sha256 of the file's bytes; the manifest consumer
(slice 2) reads them through ``load_test_file_receipts``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite name="pytest">
<testcase classname="tests.test_alpha" name="test_one" time="0.1" />
<testcase classname="tests.test_alpha.TestGroup" name="test_two" time="0.1" />
<testcase classname="tests.test_beta" name="test_bad" time="0.1"><failure message="boom">x</failure></testcase>
<testcase classname="tests.test_beta" name="test_ok" time="0.1" />
<testcase classname="tests.sub.test_gamma" name="test_skip" time="0.0"><skipped message="nope" /></testcase>
<testcase classname="tests.test_delta" name="test_err" time="0.0"><error message="setup">x</error></testcase>
<testcase classname="tests.test_vanished" name="test_x" time="0.0" />
</testsuite></testsuites>
"""


def _suite(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    suite_root = repo / "pkg"
    for rel in ("tests/test_alpha.py", "tests/test_beta.py", "tests/sub/test_gamma.py", "tests/test_delta.py"):
        f = suite_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {rel}\n", encoding="utf-8")
    report = tmp_path / "report.xml"
    report.write_text(_JUNIT, encoding="utf-8")
    run = tmp_path / "run"
    (run / "meta").mkdir(parents=True)
    return repo, suite_root, report


def test_outcomes_are_recorded_per_repo_relative_test_file(tmp_path: Path) -> None:
    from trw_mcp.state.test_receipts import load_test_file_receipts, record_junit_report

    repo, suite_root, report = _suite(tmp_path)
    run = tmp_path / "run"
    record_junit_report(report, suite_root=suite_root, repo_root=repo, run_path=run)
    got = load_test_file_receipts(run)

    alpha = got["pkg/tests/test_alpha.py"]
    assert (alpha.passed, alpha.failed, alpha.errors, alpha.skipped) == (2, 0, 0, 0)  # class suffix folds into its file
    beta = got["pkg/tests/test_beta.py"]
    assert (beta.passed, beta.failed) == (1, 1)
    assert got["pkg/tests/sub/test_gamma.py"].skipped == 1
    assert got["pkg/tests/test_delta.py"].errors == 1
    digest = "sha256:" + hashlib.sha256((suite_root / "tests/test_alpha.py").read_bytes()).hexdigest()
    assert alpha.content_digest == digest
    assert "pkg/tests/test_vanished.py" not in got  # a testcase with no file on disk binds to nothing


def test_a_file_proves_itself_only_with_a_pass_and_no_failure(tmp_path: Path) -> None:
    from trw_mcp.state.test_receipts import load_test_file_receipts, record_junit_report

    repo, suite_root, report = _suite(tmp_path)
    run = tmp_path / "run"
    record_junit_report(report, suite_root=suite_root, repo_root=repo, run_path=run)
    got = load_test_file_receipts(run)
    assert got["pkg/tests/test_alpha.py"].proven
    assert not got["pkg/tests/test_beta.py"].proven  # one failure
    assert not got["pkg/tests/sub/test_gamma.py"].proven  # skipped only
    assert not got["pkg/tests/test_delta.py"].proven  # error


def test_a_second_suite_accumulates_and_a_rerun_of_the_same_file_replaces(tmp_path: Path) -> None:
    from trw_mcp.state.test_receipts import load_test_file_receipts, record_junit_report

    repo, suite_root, report = _suite(tmp_path)
    run = tmp_path / "run"
    record_junit_report(report, suite_root=suite_root, repo_root=repo, run_path=run)
    rerun = tmp_path / "rerun.xml"
    rerun.write_text(
        '<testsuites><testsuite name="pytest"><testcase classname="tests.test_beta" name="test_bad" />'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    record_junit_report(rerun, suite_root=suite_root, repo_root=repo, run_path=run)
    got = load_test_file_receipts(run)
    assert got["pkg/tests/test_beta.py"].proven  # the latest report for a file wins
    assert got["pkg/tests/test_alpha.py"].proven  # files the rerun did not cover are kept


def test_an_unparseable_report_raises_rather_than_recording_nothing(tmp_path: Path) -> None:
    from trw_mcp.state.test_receipts import record_junit_report

    repo, suite_root, _ = _suite(tmp_path)
    bad = tmp_path / "bad.xml"
    bad.write_text("<not-closed", encoding="utf-8")
    with pytest.raises(ValueError):
        record_junit_report(bad, suite_root=suite_root, repo_root=repo, run_path=tmp_path / "run")


def test_a_tampered_receipts_file_loads_as_empty(tmp_path: Path) -> None:
    from trw_mcp.state.test_receipts import RECEIPTS_FILE, load_test_file_receipts, record_junit_report

    repo, suite_root, report = _suite(tmp_path)
    run = tmp_path / "run"
    record_junit_report(report, suite_root=suite_root, repo_root=repo, run_path=run)
    path = run / "meta" / RECEIPTS_FILE
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"]["pkg/tests/test_beta.py"]["failed"] = 0  # forge a pass
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_test_file_receipts(run) == {}


def _tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    from fastmcp import FastMCP

    from tests.conftest import get_tools_sync
    from trw_mcp.tools.build import register_build_tools
    from trw_mcp.tools.orchestration import register_orchestration_tools

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    srv = FastMCP("test")
    register_orchestration_tools(srv)
    register_build_tools(srv)
    return get_tools_sync(srv)


def test_build_check_records_receipts_from_the_junit_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state.test_receipts import load_test_file_receipts

    tools = _tools(tmp_path, monkeypatch)
    run_path = tools["trw_init"].fn(task_name="receipts")["run_path"]  # type: ignore[attr-defined]
    (tmp_path / "pkg" / "tests").mkdir(parents=True)
    (tmp_path / "pkg" / "tests" / "test_alpha.py").write_text("# a\n", encoding="utf-8")
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest"><testcase classname="tests.test_alpha" name="t" /></testsuite></testsuites>',
        encoding="utf-8",
    )
    tools["trw_build_check"].fn(  # type: ignore[attr-defined]
        tests_passed=True,
        test_count=1,
        scope="full",
        options={"run_path": run_path, "junit_xml": str(report), "suite_root": "pkg"},
    )
    assert load_test_file_receipts(Path(run_path))["pkg/tests/test_alpha.py"].proven


def test_build_check_refuses_receipts_with_no_run_to_record_into(tmp_path: Path) -> None:
    from trw_mcp.tools.build._registration import _record_test_receipts

    with pytest.raises(ValueError, match="needs an active run"):
        _record_test_receipts(str(tmp_path / "r.xml"), None, None)


def test_a_symlinked_run_meta_dir_is_refused_not_written_through(tmp_path: Path) -> None:
    from trw_mcp.state._containment import ContainmentError
    from trw_mcp.state.test_receipts import RECEIPTS_FILE, record_junit_report

    repo, suite_root, report = _suite(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    run = repo / ".trw" / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "meta").symlink_to(outside)
    with pytest.raises(ContainmentError):
        record_junit_report(report, suite_root=suite_root, repo_root=repo, run_path=run)
    assert not (outside / RECEIPTS_FILE).exists()


def test_a_custom_run_dir_outside_trw_still_refuses_a_symlinked_meta_dir(tmp_path: Path) -> None:
    from trw_mcp.state._containment import ContainmentError
    from trw_mcp.state.test_receipts import RECEIPTS_FILE, record_junit_report

    repo, suite_root, report = _suite(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    run = tmp_path / "custom-run"
    run.mkdir()
    (run / "meta").symlink_to(outside)
    with pytest.raises(ContainmentError):
        record_junit_report(report, suite_root=suite_root, repo_root=repo, run_path=run)
    assert not (outside / RECEIPTS_FILE).exists()
