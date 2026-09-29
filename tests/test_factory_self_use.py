"""PRD-CORE-340 FR10: the packaged slice records its own attempt through public entry points.

Formation init (orchestrator member) -> START checkpoint -> a real ``trw_build_check`` with
explicit ``command_results`` -> READY -> ``receipt verify`` by a separate receiver run -> USED
-> ``trw-mcp factory status``. Every receipt is written by the product; none is hand-made.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state import _factory_experiment as fx

DAY = (2026, 11, 3)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def _exit(fn: Any, args: argparse.Namespace) -> int:
    with pytest.raises(SystemExit) as excinfo:
        fn(args)
    return int(excinfo.value.code)


def _parse(*argv: str) -> argparse.Namespace:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    return _build_arg_parser().parse_args(list(argv))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Inject the event clock (every checkpoint event is stamped through persistence.datetime)."""

    class _Clock(datetime):
        current = datetime(*DAY, 10, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr("trw_mcp.state.persistence.datetime", _Clock)

    def at(hour: int, minute: int) -> None:
        _Clock.current = datetime(*DAY, hour, minute, tzinfo=timezone.utc)

    return at


@pytest.fixture
def project(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_project.parent))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "1")
    monkeypatch.setattr(fx, "_utc_now", lambda: datetime(*DAY, 12, 0, tzinfo=timezone.utc))
    for cmd in (["init", "-q"], ["config", "user.email", "t@e.com"], ["config", "user.name", "T"]):
        _git(tmp_project, *cmd)
    (tmp_project / ".gitignore").write_text(".trw/\n", encoding="utf-8")
    (tmp_project / "docs").mkdir()
    (tmp_project / "docs" / "rules.md").write_text("rules\n", encoding="utf-8")
    (tmp_project / "src").mkdir()
    (tmp_project / "src" / "feature.py").write_text("print('feature works')\n", encoding="utf-8")
    _git(tmp_project, "add", ".")
    _git(tmp_project, "commit", "-qm", "candidate")
    return tmp_project


def _run_dir(project: Path, name: str) -> Path:
    run = project / ".trw" / "runs" / "task" / name
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "run.yaml").write_text(f"run_id: {name}\nstatus: active\nphase: implement\n", encoding="utf-8")
    (run / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return run


def _checkpoint(run: Path, payload: dict[str, object]) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    result = execute_checkpoint(str(run), json.dumps(payload), None)
    assert result["recorded"] is True, result


def _journal(run: Path, payload: dict[str, object]) -> None:
    """Append a factory line straight to the journal, as a hand-written or older journal would hold it.

    ``trw_checkpoint`` refuses a READY/USED naming a receipt that does not resolve, so the reader's
    tolerance of such journals is exercised through the file, not through the tool.
    """
    from trw_mcp.state import persistence

    line = {
        "ts": persistence.datetime.now(timezone.utc).isoformat(),
        "event": "checkpoint",
        "message": json.dumps(payload),
    }
    with (run / "meta" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


def _status(lead: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    from trw_mcp.server._cli_factory import run_factory

    capsys.readouterr()
    now = f"{DAY[0]}-{DAY[1]:02d}-{DAY[2]:02d}T11:00:00+00:00"
    code = _exit(run_factory, _parse("factory", "status", "--run", str(lead), "--now", now, "--json"))
    return code, json.loads(capsys.readouterr().out)


def _close_loop(
    project: Path,
    clock: Any,
    build_check_invoke: Any,
    capsys: pytest.CaptureFixture[str],
    exit_code: int | None = None,
) -> tuple[Path, Path, str]:
    """START -> real build_check -> READY -> receiver ``receipt verify`` -> USED; returns the ids' homes."""
    from trw_mcp.tools._orchestration_formation import create_formation
    from trw_mcp.tools._receipt_cli import run_receipt

    lead, receiver = _run_dir(project, "run-lead"), _run_dir(project, "run-receiver")
    manifest = create_formation(
        lead,
        {
            "formation_id": "factory-self",
            "shared_rules_ref": "docs/rules.md",
            "members": [{"member_id": "impl-1", "client": "codex", "open_join": True, "owned_paths": ["src"]}],
        },
        None,
        pin_key="lead-pin",
    )
    assert {m.member_id for m in manifest.members} == {"orchestrator", "impl-1"}

    clock(10, 0)
    _checkpoint(lead, {"factory": 1, "kind": "START", "attempt": "attempt-1"})

    with (lead / "meta" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "file_modified", "file": str(project / "src" / "feature.py")}) + "\n")
    commands = [
        {"command_id": "tests", "label": "pytest feature", "command_class": "test", "exit_code": 0},
        {"command_id": "static_checks", "label": "ruff feature", "command_class": "static", "exit_code": 0},
    ]
    built = build_check_invoke(
        tests_passed=True, static_checks_clean=True, scope="feature", run_path=str(lead), command_results=commands
    )
    assert built["typed_receipt_state"] == "written"
    build_id = built["build_receipt_id"]
    head = _git(project, "rev-parse", "HEAD")

    clock(10, 5)
    _checkpoint(
        lead,
        {"factory": 1, "kind": "READY", "attempt": "attempt-1", "subject_sha": head, "receipts": {"build": [build_id]}},
    )

    # The receiver is a separate run that exercises the candidate itself and reports the real exit code.
    check = subprocess.run(
        [sys.executable, str(project / "src" / "feature.py")], capture_output=True, text=True, check=False
    )
    receipt_argv = ["receipt", "verify", "--run", str(receiver), "--subject", head, "--check", "run src/feature.py"]
    receipt_argv += [
        "--exit-code",
        str(check.returncode if exit_code is None else exit_code),
        "--evidence",
        "src/feature.py",
        "--json",
    ]
    capsys.readouterr()
    receipt_code = _exit(run_receipt, _parse(*receipt_argv))
    assert receipt_code == 0 or exit_code is not None
    verification_id = json.loads(capsys.readouterr().out)["receipt_id"]

    clock(10, 12)
    ref = {"run_path": ".trw/runs/task/run-receiver", "receipt_id": verification_id}
    _checkpoint(lead, {"factory": 1, "kind": "USED", "attempt": "attempt-1", "receipts": {"verification": [ref]}})

    return lead, receiver, verification_id


def test_factory_records_its_own_attempt(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, receiver, verification_id = _close_loop(project, clock, build_check_invoke, capsys)
    code, report = _status(lead, capsys)
    assert code == 0 and report["banner"] == fx.BANNER and report["descriptive_only"] is True
    (run_report,) = report["runs"]
    (attempt,) = run_report["attempts"]
    assert (attempt["start_to_ready"], attempt["ready_to_used"], attempt["status"]) == (300.0, 420.0, "completed")
    states = {(r["kind"], r["label"].split(":")[0]): r["state"] for r in attempt["receipts"]}
    assert states == {("READY", "build"): "referenced", ("USED", "verification"): "referenced"}
    assert run_report["counts"] == {
        "completed": 1,
        "incomplete": 0,
        "excluded": 0,
        "unresolved": 0,
        "voided": 0,
        "failed": 0,
        "unverified": 0,
    }
    assert report["denominators"] == {
        "runs": 1,
        "attempts_seen": 1,
        "start_to_ready_n": 1,
        "ready_to_used_n": 1,
        "voided": 0,
        "completed": 1,
        "open": 0,
        "failed": 0,
        "excluded": 0,
        "conflicting": 0,
        "malformed": 0,
        "stale": 0,
        "evidence_invalid": 0,
        "binding_unverifiable": 0,
    }
    assert run_report["diagnostics"] == []

    # The text rendering (what the script adapter delegates to) reports the same thing. The script
    # adapter itself is exercised in scripts/tests/test_factory_status.py, which trw-mcp exports omit.
    from trw_mcp.server._cli_factory import run_factory

    capsys.readouterr()
    now = f"{DAY[0]}-{DAY[1]:02d}-{DAY[2]:02d}T11:00:00+00:00"
    assert _exit(run_factory, _parse("factory", "status", "--run", str(lead), "--now", now)) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[0] == fx.BANNER and "start_to_ready=300s ready_to_used=420s" in text


def _assert_unresolved(lead: Path, capsys: pytest.CaptureFixture[str], verification_id: str, reason: str) -> None:
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    (attempt,) = run_report["attempts"]
    assert code == 1
    assert attempt["status"] == "unresolved"
    assert run_report["counts"] == {
        "completed": 0,
        "incomplete": 0,
        "excluded": 0,
        "unresolved": 1,
        "voided": 0,
        "failed": 0,
        "unverified": 0,
    }
    assert run_report["intervals"]["ready_to_used"] is None  # not in the aggregate: n=0
    assert any(
        f"verification:{verification_id} {reason}" in d and "events.jsonl:" in d for d in run_report["diagnostics"]
    )


def test_deleted_receiver_evidence_leaves_the_attempt_unresolved(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    (receiver / "meta" / "receipts" / "verification" / f"{vid}.json").unlink()
    _assert_unresolved(lead, capsys, vid, "missing")


def test_receiver_evidence_from_another_run_is_wrong_run(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    path = receiver / "meta" / "receipts" / "verification" / f"{vid}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["run_id"] = "some-other-run"
    path.write_text(json.dumps(body), encoding="utf-8")
    _assert_unresolved(lead, capsys, vid, "wrong_run")


def test_used_without_a_verification_reference_is_unresolved(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead = _run_dir(project, "run-lead")
    for minute, kind, receipts in (
        (0, "START", None),
        (5, "READY", {"build": ["b1"]}),
        (12, "USED", {"build": ["b1"]}),
    ):
        clock(10, minute)
        _journal(lead, {"factory": 1, "kind": kind, "attempt": "a1", **({"receipts": receipts} if receipts else {})})
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1 and run_report["attempts"][0]["status"] == "unresolved"
    assert any("no verification receipt reference" in d for d in run_report["diagnostics"])


@pytest.mark.parametrize(
    ("kind", "receipts", "rule"),
    [
        ("READY", {"verification": ["v1"]}, "READY may reference build/review receipts; verification belongs to USED"),
        ("READY", {"bogus": ["x1"]}, "READY may reference build/review receipts"),
        ("START", {"build": ["b1"]}, "START carries no receipts"),
    ],
)
def test_ready_with_verification_ref_names_the_rule(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str], kind: str, receipts: dict[str, list[str]], rule: str
) -> None:
    """The reader still diagnoses these lines in a hand-written or older journal. The tool no longer records them
    (FACTORY-START-VALIDATE), so the line is appended to the journal directly."""
    lead = _run_dir(project, "run-lead")
    clock(10, 0)
    _journal(lead, {"factory": 1, "kind": kind, "attempt": "a1", "receipts": receipts})
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1
    assert any(rule in d and "events.jsonl:1:" in d for d in run_report["diagnostics"]), run_report["diagnostics"]
    assert not any("path_escape" in d for d in run_report["diagnostics"])


def _void_journal(project: Path, clock: Any, lines: list[tuple[int, dict[str, object]]]) -> Path:
    lead = _run_dir(project, "run-lead")
    for minute, payload in lines:
        clock(10, minute)
        _journal(lead, {"factory": 1, "attempt": "a1", **payload})
    return lead


def test_void_after_used_is_not_completed(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)
    before = (lead / "meta" / "events.jsonl").read_text(encoding="utf-8")
    clock(10, 30)
    _checkpoint(lead, {"factory": 1, "kind": "VOID", "attempt": "attempt-1", "reason": "live swap went red"})
    assert (lead / "meta" / "events.jsonl").read_text(encoding="utf-8").startswith(before)  # append-only
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    (attempt,) = run_report["attempts"]
    assert code == 0 and attempt["status"] == "voided"
    assert run_report["counts"] == {
        "completed": 0,
        "incomplete": 0,
        "excluded": 0,
        "unresolved": 0,
        "voided": 1,
        "failed": 0,
        "unverified": 0,
    }
    assert run_report["intervals"]["ready_to_used"] is None  # a voided attempt is not a ready_to_used sample
    assert run_report["intervals"]["start_to_ready"]["n"] == 1
    assert "live swap" not in json.dumps(report)


def test_void_text_output_counts_voided_and_never_prints_the_reason(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _r, _v = _close_loop(project, clock, build_check_invoke, capsys)
    clock(10, 30)
    _checkpoint(lead, {"factory": 1, "kind": "VOID", "attempt": "attempt-1", "reason": "SECRET-REASON"})
    from trw_mcp.state._factory_status import render_run, report_run

    text = "\n".join(render_run(report_run(lead, datetime(*DAY, 11, 0, tzinfo=timezone.utc))))
    assert "status=voided" in text and "voided=1" in text and "SECRET-REASON" not in text


_USED = {"kind": "USED", "receipts": {"verification": ["v1"]}}


@pytest.mark.parametrize(
    "lines",
    [
        [(0, {"kind": "START"}), (5, {"kind": "VOID", "reason": "early"})],  # no READY at all
        [(0, {"kind": "START"}), (3, {"kind": "VOID", "reason": "early"}), (12, _USED)],  # VOID before any READY
    ],
)
def test_void_before_any_ready_is_ignored_with_a_diagnostic(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str], lines: list[tuple[int, dict[str, object]]]
) -> None:
    lead = _void_journal(project, clock, lines)
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1 and run_report["counts"]["voided"] == 0
    assert run_report["attempts"][0]["status"] != "voided"
    assert any("VOID for attempt a1 does not follow a READY and is ignored" in d for d in run_report["diagnostics"])


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "VOID"},
        {"kind": "VOID", "reason": ""},
        {"kind": "VOID", "reason": 7},
        {"kind": "VOID", "reason": "x" * 201},
        {"kind": "VOID", "reason": "line\nbreak"},
        {"kind": "VOID", "reason": "ok", "receipts": {"verification": ["v1"]}},
    ],
)
def test_malformed_void_is_diagnosed_without_echo(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str], bad: dict[str, object]
) -> None:
    lines: list[tuple[int, dict[str, object]]] = [
        (0, {"kind": "START"}),
        (5, {"kind": "READY", "receipts": {"build": ["b1"]}}),
        (12, _USED),
        (20, bad),
    ]
    lead = _void_journal(project, clock, lines)
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1 and run_report["counts"]["voided"] == 0
    assert any("VOID" in d and "events.jsonl:4:" in d for d in run_report["diagnostics"])
    dumped = json.dumps(run_report)
    assert "line\\nbreak" not in dumped and "xxxxxxxx" not in dumped


def test_void_on_an_attempt_missing_ready_is_ignored_with_a_diagnostic(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead = _void_journal(project, clock, [(0, {"kind": "START"}), (12, _USED), (20, {"kind": "VOID", "reason": "r"})])
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1 and run_report["attempts"][0]["status"] == "incomplete" and run_report["counts"]["voided"] == 0
    assert any("VOID for attempt a1 does not follow a READY and is ignored" in d for d in run_report["diagnostics"])


def test_used_recorded_after_a_ready_void_does_not_resurrect_the_attempt(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A VOID retracts an attempt that reached READY; rework is a new attempt id, so a later USED cannot undo it."""
    ready = {"kind": "READY", "receipts": {"build": ["b1"]}}
    lines: list[tuple[int, dict[str, object]]] = [
        (0, {"kind": "START"}),
        (5, ready),
        (8, {"kind": "VOID", "reason": "verifier FAIL"}),
        (12, _USED),
    ]
    lead = _void_journal(project, clock, lines)
    _code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert run_report["attempts"][0]["status"] == "voided" and run_report["counts"]["voided"] == 1
    assert run_report["intervals"]["ready_to_used"] is None, "a retracted attempt contributes no ready_to_used sample"
    assert not any("is ignored" in d for d in run_report["diagnostics"]), run_report["diagnostics"]


def test_a_void_on_a_ready_only_attempt_is_voided_without_any_used(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """The verifier's FAIL on an attempt that never reached USED (FACTORY-VOID-READY-ONLY)."""
    lines: list[tuple[int, dict[str, object]]] = [
        (0, {"kind": "START"}),
        (5, {"kind": "READY", "receipts": {"build": ["b1"]}}),
        (9, {"kind": "VOID", "reason": "verifier FAIL at the tip"}),
    ]
    lead = _void_journal(project, clock, lines)
    _code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    (attempt,) = run_report["attempts"]
    assert attempt["status"] == "voided" and attempt["missing"] == []
    assert run_report["counts"]["voided"] == 1 and run_report["counts"]["incomplete"] == 0
    assert report["denominators"]["voided"] == 1
    assert not any("VOID" in d and "ignored" in d for d in run_report["diagnostics"]), run_report["diagnostics"]
    assert "verifier FAIL" not in json.dumps(report), "a VOID reason is never echoed"


def test_voided_samples_are_visible_in_denominators_and_limits(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _r, _v = _close_loop(project, clock, build_check_invoke, capsys)
    clock(10, 30)
    _checkpoint(lead, {"factory": 1, "kind": "VOID", "attempt": "attempt-1", "reason": "red"})
    _code, report = _status(lead, capsys)
    assert report["denominators"]["voided"] == 1 and report["denominators"]["ready_to_used_n"] == 0
    assert any("VOID is unauthenticated" in x for x in report["limitations"])


def test_a_void_before_any_ready_never_claims_the_slot_of_the_real_one(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lines: list[tuple[int, dict[str, object]]] = [
        (0, {"kind": "START"}),
        (3, {"kind": "VOID", "reason": "too early"}),
        (5, {"kind": "READY", "receipts": {"build": ["b1"]}}),
        (8, {"kind": "VOID", "reason": "really red"}),
    ]
    lead = _void_journal(project, clock, lines)
    _code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert run_report["attempts"][0]["status"] == "voided" and run_report["counts"]["voided"] == 1
    ignored = [d for d in run_report["diagnostics"] if "VOID for attempt a1 does not follow a READY" in d]
    assert len(ignored) == 1 and "conflict" not in json.dumps(run_report["diagnostics"])


def test_a_second_void_for_the_same_attempt_is_a_no_op_not_a_conflict(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lines: list[tuple[int, dict[str, object]]] = [
        (0, {"kind": "START"}),
        (5, {"kind": "READY", "receipts": {"build": ["b1"]}}),
        (8, {"kind": "VOID", "reason": "first"}),
        (12, _USED),
        (20, {"kind": "VOID", "reason": "second, different reason"}),
    ]
    lead = _void_journal(project, clock, lines)
    _code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert run_report["attempts"][0]["status"] == "voided" and run_report["counts"]["voided"] == 1
    assert run_report["counts"]["excluded"] == 0 and "conflict" not in json.dumps(run_report["diagnostics"])


def test_void_without_start_still_removes_the_ready_to_used_sample(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lines: list[tuple[int, dict[str, object]]] = [
        (5, {"kind": "READY", "receipts": {"build": ["b1"]}}),
        (12, _USED),
        (20, {"kind": "VOID", "reason": "red"}),
    ]
    lead = _void_journal(project, clock, lines)
    receipts = lead / "meta" / "receipts" / "verification"  # a resolvable receipt, so only the VOID can drop the sample
    receipts.mkdir(parents=True)
    (receipts / "v1.json").write_text(json.dumps({"receipt_id": "v1", "run_id": "run-lead"}), encoding="utf-8")
    _code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert run_report["attempts"][0]["status"] == "incomplete"
    assert run_report["intervals"]["ready_to_used"] is None and report["denominators"]["ready_to_used_n"] == 0
    assert any("VOID for attempt a1 has no START and is ignored" in d for d in run_report["diagnostics"])


def _fail_loop(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> tuple[Path, Path, str]:
    return _close_loop(project, clock, build_check_invoke, capsys, exit_code=1)


def test_used_with_fail_receipt_is_not_completed(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _receiver, _vid = _fail_loop(project, clock, build_check_invoke, capsys)
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    (attempt,) = run_report["attempts"]
    assert code == 0  # a recorded FAIL is a legitimate result, not a diagnostic
    assert attempt["status"] == "failed" and attempt["missing"] == []
    assert (run_report["counts"]["failed"], run_report["counts"]["completed"]) == (1, 0)
    assert run_report["intervals"]["ready_to_used"] is None  # a failed USED is not a measured ready_to_used
    denominators = report["denominators"]
    assert (denominators["failed"], denominators["completed"], denominators["open"]) == (1, 0, 0)
    assert denominators["ready_to_used_n"] == 0


def test_void_after_a_fail_used_counts_once_as_voided(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _receiver, _vid = _fail_loop(project, clock, build_check_invoke, capsys)
    clock(10, 30)
    _checkpoint(lead, {"factory": 1, "kind": "VOID", "attempt": "attempt-1", "reason": "retracted"})
    _code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert run_report["attempts"][0]["status"] == "voided"
    counts = run_report["counts"]
    assert (counts["voided"], counts["failed"], counts["completed"]) == (1, 0, 0)
    assert sum(counts.values()) == 1  # one attempt, one bucket
    assert (report["denominators"]["voided"], report["denominators"]["failed"]) == (1, 0)


def test_pass_receipt_with_changed_evidence_is_still_completed_and_stale(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)
    (project / "src" / "feature.py").write_text("print('changed after the check')\n", encoding="utf-8")
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    (attempt,) = run_report["attempts"]
    assert code == 0 and attempt["status"] == "completed" and attempt["stale"] is True
    assert (
        "binding_unverifiable" not in attempt
    )  # the blob at the recorded subject still matches: later edits never unbind
    d = report["denominators"]
    assert (d["completed"], d["stale"], d["binding_unverifiable"], d["open"]) == (1, 1, 0, 0)
    assert d["ready_to_used_n"] == 1  # a stale PASS still contributes its measured interval


def test_receipt_whose_subject_commit_is_unknown_is_flagged_binding_unverifiable(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models._evidence_core import domain_digest

    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    path = receiver / "meta" / "receipts" / "verification" / f"{vid}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["subject_sha"] = "a" * 40
    body["mapping_digest"] = domain_digest(
        "verification_mapping", {"subject": body["subject_sha"], "check": body["pass_condition_evaluation"]}
    )
    path.write_text(json.dumps(body), encoding="utf-8")
    _code, report = _status(lead, capsys)
    (attempt,) = report["runs"][0]["attempts"]
    assert attempt["status"] == "completed" and attempt["binding_unverifiable"] is True
    assert report["denominators"]["binding_unverifiable"] == 1


def test_receipt_that_is_not_self_consistent_is_unverified(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    path = receiver / "meta" / "receipts" / "verification" / f"{vid}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["mapping_digest"] = "0" * 64  # no longer the digest of (subject, check)
    path.write_text(json.dumps(body), encoding="utf-8")
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1 and run_report["attempts"][0]["status"] == "unverified"
    assert any("is unverified" in d for d in run_report["diagnostics"])


def test_aggregate_open_conflicting_and_malformed_counts(
    project: Path, clock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead = _run_dir(project, "run-lead")
    clock(10, 0)
    _checkpoint(lead, {"factory": 1, "kind": "START", "attempt": "a1"})
    clock(10, 1)
    _checkpoint(lead, {"factory": 1, "kind": "START", "attempt": "a1", "model_id": "other"})  # conflicts with line 1
    clock(10, 2)
    _checkpoint(lead, {"factory": 1, "kind": "START", "attempt": "a2"})  # open: never reaches READY/USED
    with (lead / "meta" / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")  # malformed row
    _code, report = _status(lead, capsys)
    d = report["denominators"]
    assert (d["conflicting"], d["malformed"], d["excluded"], d["open"], d["completed"]) == (1, 1, 1, 1, 0)
    assert d["attempts_seen"] == 2


def test_messages_and_metrics_confer_no_authority(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.state._trust_receipts import collect_positive_trust_evidence

    lead, receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)
    before = collect_positive_trust_evidence(lead, project)
    files = sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file() and ".git" not in p.parts)
    events = (lead / "meta" / "events.jsonl").read_bytes()
    code, report = _status(lead, capsys)
    assert code == 0 and report["descriptive_only"] is True
    assert report["denominators"]["completed"] == 1  # a completed count exists ...
    text = json.dumps(report).lower()
    assert not any(word in text for word in ("approved", "accepted", "rank", "authorized"))
    # ... yet reading it wrote nothing and the trust gate's evidence (the only authority state) is unchanged.
    assert (lead / "meta" / "events.jsonl").read_bytes() == events
    assert collect_positive_trust_evidence(lead, project) == before
    assert sorted(p.relative_to(project) for p in project.rglob("*") if p.is_file() and ".git" not in p.parts) == files


def _edit_receipt(receiver: Path, vid: str, **changes: Any) -> None:
    path = receiver / "meta" / "receipts" / "verification" / f"{vid}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body.update(changes)
    path.write_text(json.dumps(body), encoding="utf-8")


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "inconclusive"},
        {"outcome": "not_run"},
        {"outcome": "bogus"},
        {"evidence_artifact_digest": 7},  # fails strict model validation: unparsable
    ],
)
def test_inconclusive_or_unparsable_receipt_is_unverified_with_exit_one(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str], changes: dict[str, Any]
) -> None:
    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    _edit_receipt(receiver, vid, **changes)
    code, report = _status(lead, capsys)
    assert code == 1 and report["runs"][0]["attempts"][0]["status"] == "unverified"
    assert report["denominators"]["open"] == 1


def test_receipt_with_id_mismatch_is_unverified(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.state._factory_verdict import verification_verdict

    _lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    assert verification_verdict(receiver, vid, project)[0] == "pass"
    _edit_receipt(receiver, vid, receipt_id="verification-other")
    assert verification_verdict(receiver, vid, project) == ("unverified", {})


def test_cross_run_reference_is_judged_from_the_owner_run(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.state._factory_verdict import verification_verdict

    lead, _receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    ref = {"run_path": ".trw/runs/task/run-receiver", "receipt_id": vid}
    verdict, flags = verification_verdict(lead, ref, project)  # ``lead`` holds no such receipt: owner is the receiver
    assert verdict == "pass" and flags == {"stale": False, "evidence_invalid": "", "binding_unverifiable": False}


def test_empty_binding_entries_never_count_as_bound(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    path = receiver / "meta" / "receipts" / "verification" / f"{vid}.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["content_binding"]["entries"] = []
    path.write_text(json.dumps(body), encoding="utf-8")
    _code, report = _status(lead, capsys)
    (attempt,) = report["runs"][0]["attempts"]
    assert attempt["status"] == "unverified"  # the model itself refuses an empty binding


@pytest.mark.parametrize("failure", [OSError("no git"), subprocess.TimeoutExpired("git", 10)])
def test_git_failure_or_timeout_flags_binding_unverifiable(
    project: Path,
    clock: Any,
    build_check_invoke: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    from trw_mcp.state import _factory_verdict as fv

    lead, _receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)

    def boom(*_a: object, **_k: object) -> None:
        raise failure

    monkeypatch.setattr(fv.subprocess, "run", boom)
    code, report = _status(lead, capsys)
    (attempt,) = report["runs"][0]["attempts"]
    assert code == 0 and attempt["status"] == "completed" and attempt["binding_unverifiable"] is True


def test_oversize_blob_is_binding_unverifiable(
    project: Path,
    clock: Any,
    build_check_invoke: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.state import _factory_verdict as fv

    lead, _receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)
    monkeypatch.setattr(fv, "_BLOB_CAP", 1)
    (attempt,) = _status(lead, capsys)[1]["runs"][0]["attempts"]
    assert attempt["status"] == "completed" and attempt["binding_unverifiable"] is True


@pytest.mark.parametrize(
    ("path", "safe"),
    [
        ("src/feature.py", True),
        ("/etc/passwd", False),
        ("../secret", False),
        ("a/../b", False),
        ("./src/feature.py", False),
        ("-rf", False),
        ("a:b", False),
        ("", False),
    ],
)
def test_entry_paths_are_validated_before_git_sees_them(path: str, safe: bool) -> None:
    from trw_mcp.state._factory_verdict import _safe_entry_path

    assert _safe_entry_path(path) is safe


def test_git_runs_hermetically_without_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state._factory_verdict import _git_env

    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    env = _git_env()
    assert not any(k.startswith("GIT_") and k not in ("GIT_NO_LAZY_FETCH", "GIT_TERMINAL_PROMPT") for k in env)
    assert env["GIT_NO_LAZY_FETCH"] == "1" and env["GIT_TERMINAL_PROMPT"] == "0"


def test_run_outside_a_project_runs_dir_skips_git_and_flags_binding_unverifiable(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.state._factory_verdict import verification_verdict

    _lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    assert verification_verdict(receiver, vid, None) == (
        "pass",
        {"stale": False, "evidence_invalid": "", "binding_unverifiable": True},
    )


def test_mixed_pass_and_fail_references_make_the_attempt_failed(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.tools._receipt_cli import run_receipt

    lead, receiver, fail_id = _fail_loop(project, clock, build_check_invoke, capsys)
    head = _git(project, "rev-parse", "HEAD")
    argv = ["receipt", "verify", "--run", str(receiver), "--subject", head, "--check", "second check"]
    argv += ["--exit-code", "0", "--evidence", "src/feature.py", "--json"]
    capsys.readouterr()
    assert _exit(run_receipt, _parse(*argv)) == 0
    pass_id = json.loads(capsys.readouterr().out)["receipt_id"]
    events = lead / "meta" / "events.jsonl"
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        msg = json.loads(row["message"]) if row.get("event") == "checkpoint" else {}
        if msg.get("kind") == "USED":
            run_path = ".trw/runs/task/run-receiver"
            msg["receipts"] = {"verification": [{"run_path": run_path, "receipt_id": i} for i in (pass_id, fail_id)]}
            row["message"] = json.dumps(msg)
    events.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    code, report = _status(lead, capsys)
    assert code == 0 and report["runs"][0]["attempts"][0]["status"] == "failed"


def test_deleted_evidence_is_a_content_change_so_stale(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, _receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)
    (project / "src" / "feature.py").unlink()  # the bound content is no longer what was recorded
    (attempt,) = _status(lead, capsys)[1]["runs"][0]["attempts"]
    assert attempt["status"] == "completed" and attempt["stale"] is True and "evidence_invalid" not in attempt


def test_evidence_the_tree_cannot_judge_is_evidence_invalid_not_stale(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    _edit_receipt(receiver, vid, evidence_artifact_path="../escape.txt")  # binding still current; artifact unjudgeable
    code, report = _status(lead, capsys)
    (attempt,) = report["runs"][0]["attempts"]
    assert code == 0 and attempt["status"] == "completed"
    assert "stale" not in attempt and attempt["evidence_invalid"] == "artifact_path_invalid"
    d = report["denominators"]
    assert (d["stale"], d["evidence_invalid"], d["completed"]) == (0, 1, 1)


@pytest.mark.parametrize("hazard", ["symlink", "oversize"])
def test_unsafe_verification_receipt_is_unresolved_never_read(
    hazard: str,
    project: Path,
    clock: Any,
    build_check_invoke: Any,
    capsys: pytest.CaptureFixture[str],
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """FR03/NFR02: a symlinked or oversized USED verification receipt cannot complete an attempt."""
    from trw_mcp.models._evidence_core import EvidenceLimits

    lead, receiver, vid = _close_loop(project, clock, build_check_invoke, capsys)
    path = receiver / "meta" / "receipts" / "verification" / f"{vid}.json"
    if hazard == "symlink":
        outside = tmp_path_factory.mktemp("outside") / path.name
        outside.write_bytes(path.read_bytes())  # a byte-perfect, otherwise-valid receipt
        path.unlink()
        path.symlink_to(outside)
    else:
        path.write_bytes(path.read_bytes() + b" " * EvidenceLimits.MAX_CANONICAL_RECEIPT_BYTES)
    code, report = _status(lead, capsys)
    (run_report,) = report["runs"]
    assert code == 1 and run_report["attempts"][0]["status"] == "unresolved"
    assert any(("path_escape" if hazard == "symlink" else "oversize") in d for d in run_report["diagnostics"])


def _refused(run: Path, payload: dict[str, object]) -> dict[str, object]:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    return execute_checkpoint(str(run), json.dumps(payload), None)


def _events(run: Path) -> str:
    return (run / "meta" / "events.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("kind", "receipts"),
    [
        ("READY", {"build": ["build-PENDING"]}),
        ("READY", {"review": ["review-PENDING"]}),
        ("USED", {"verification": [{"run_path": ".trw/runs/task/run-lead", "receipt_id": "verification-PENDING"}]}),
        ("USED", {"verification": ["verification-PENDING"]}),
    ],
)
def test_a_receipt_id_with_no_receipt_file_is_refused_and_never_written(
    project: Path, clock: Any, kind: str, receipts: dict[str, list[object]]
) -> None:
    lead = _run_dir(project, "run-lead")
    clock(10, 0)
    before = _events(lead)

    result = _refused(lead, {"factory": 1, "kind": kind, "attempt": "a1", "receipts": receipts})

    assert result["recorded"] is False
    assert result["reason"] == "factory_receipt_unresolved" and result["error_type"] == "factory_receipt_unresolved"
    assert "PENDING (missing)" in str(result["remedy"]), result
    assert _events(lead) == before, "a refused checkpoint must not touch the journal"
    assert not (lead / "meta" / "checkpoints.jsonl").exists()


def test_a_real_build_receipt_and_a_real_verification_receipt_are_accepted(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    # _close_loop records READY (build) and USED (verification, in ANOTHER run) through the real tool.
    lead, _receiver, _vid = _close_loop(project, clock, build_check_invoke, capsys)
    code, report = _status(lead, capsys)
    assert report["runs"][0]["attempts"][0]["status"] == "completed", (code, report)


def test_one_unresolved_id_among_resolved_ones_refuses_the_whole_checkpoint_naming_it(
    project: Path, clock: Any, build_check_invoke: Any
) -> None:
    lead = _run_dir(project, "run-lead")
    built = build_check_invoke(tests_passed=True, scope="feature", run_path=str(lead))
    real = built["build_receipt_id"]
    before = _events(lead)

    result = _refused(
        lead, {"factory": 1, "kind": "READY", "attempt": "a1", "receipts": {"build": [real, "build-PENDING"]}}
    )

    assert result["recorded"] is False
    assert "build-PENDING" in str(result["remedy"]) and real not in str(result["remedy"])
    assert _events(lead) == before


def test_a_present_receipt_bound_to_another_sha_is_accepted_and_left_to_the_reader(
    project: Path, clock: Any, build_check_invoke: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Report-time judgments (build_sha_mismatch, binding_unverifiable) are not this gate's to make."""
    lead = _run_dir(project, "run-lead")
    real = build_check_invoke(tests_passed=True, scope="feature", run_path=str(lead))["build_receipt_id"]

    result = _refused(
        lead, {"factory": 1, "kind": "READY", "attempt": "a1", "subject_sha": "0" * 40, "receipts": {"build": [real]}}
    )

    assert result["recorded"] is True, result


def test_start_void_and_prose_are_recorded_and_a_schema_invalid_ready_is_refused_at_write(
    project: Path, clock: Any
) -> None:
    lead = _run_dir(project, "run-lead")
    for payload in (
        {"factory": 1, "kind": "START", "attempt": "a1"},
        {"factory": 1, "kind": "VOID", "attempt": "a1", "reason": "r"},
    ):
        assert _refused(lead, payload)["recorded"] is True, payload
    invalid = _refused(lead, {"factory": 1, "kind": "READY", "attempt": "a1", "receipts": {"verification": ["v1"]}})
    assert invalid["recorded"] is False and invalid["reason"] == "factory_payload_invalid", invalid
