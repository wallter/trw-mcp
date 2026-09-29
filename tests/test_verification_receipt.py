"""Software-factory slice 1: the content-bound VerificationReceipt writer + ``receipt verify`` CLI."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import VerificationOutcome
from trw_mcp.models._evidence_records import VerificationReceipt
from trw_mcp.tools._evidence_receipts import canonical_receipt_bytes, record_verification_receipt
from trw_mcp.tools._verification_receipt import VerificationReceiptRefusedError

SUBJECT = "a" * 40


@pytest.fixture(autouse=True)
def _factory_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The writer sits behind the experimental switch (PRD-CORE-340-FR11); these tests exercise it enabled."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "1")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".gitignore").write_text(".trw/\n", encoding="utf-8")
    (repo / "one.py").write_text("one = 1\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "two.py").write_text("two = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


@pytest.fixture
def run(project: Path) -> Path:
    path = project / ".trw" / "runs" / "task" / "run1"
    (path / "meta").mkdir(parents=True)
    return path


def _record(run: Path, project: Path, **overrides: object):
    kwargs: dict[str, object] = {
        "subject": SUBJECT,
        "check": "pytest one and two",
        "exit_code": 0,
        "evidence_paths": ["one.py", "sub/two.py"],
        "project_root": project,
    }
    kwargs.update(overrides)
    return record_verification_receipt(run, **kwargs)  # type: ignore[arg-type]


def test_passing_check_writes_valid_receipt_bound_to_head(run: Path, project: Path) -> None:
    result = _record(run, project)
    assert result.typed_receipt_state == "valid"
    assert result.validation["is_positive"] is True
    assert result.passed is True
    assert result.path == run / "meta" / "receipts" / "verification" / f"{result.receipt_id}.json"
    receipt = VerificationReceipt.model_validate_json(result.path.read_bytes())
    assert receipt.outcome is VerificationOutcome.PASS
    assert receipt.git_sha == _git(project, "rev-parse", "HEAD")
    assert receipt.subject_sha == SUBJECT
    assert receipt.run_id == "run1"
    assert [e.path for e in receipt.content_binding.entries] == ["one.py", "sub/two.py"]
    assert receipt.evidence_artifact_path == "one.py"


def test_absolute_path_inside_project_is_recorded_relative(run: Path, project: Path) -> None:
    result = _record(run, project, evidence_paths=[str(project / "sub" / "two.py"), "one.py"])
    receipt = VerificationReceipt.model_validate_json(result.path.read_bytes())
    assert [e.path for e in receipt.content_binding.entries] == ["one.py", "sub/two.py"]
    assert receipt.evidence_artifact_path == "sub/two.py"
    assert result.typed_receipt_state == "valid"


def test_failing_exit_code_records_actual_outcome(run: Path, project: Path) -> None:
    result = _record(run, project, exit_code=3)
    receipt = VerificationReceipt.model_validate_json(result.path.read_bytes())
    assert result.passed is False
    assert receipt.outcome is VerificationOutcome.FAIL
    assert "exit_code=3" in receipt.observed_values
    assert result.typed_receipt_state == "valid"  # the evidence is valid; the outcome is a failure


def test_round_trip_preserves_binding_and_canonical_bytes(run: Path, project: Path) -> None:
    result = _record(run, project)
    raw = result.path.read_bytes()
    receipt = VerificationReceipt.model_validate_json(raw)
    assert canonical_receipt_bytes(receipt) == raw
    reloaded = VerificationReceipt.model_validate_json(canonical_receipt_bytes(receipt))
    assert reloaded.content_binding == receipt.content_binding
    assert reloaded.content_binding.manifest_digest == receipt.content_binding.manifest_digest


def test_identical_inputs_twice_mint_distinct_receipts(run: Path, project: Path) -> None:
    first, second = _record(run, project), _record(run, project)
    assert first.receipt_id != second.receipt_id
    assert first.path.exists() and second.path.exists()


def test_stale_evidence_is_rejected_on_revalidation(run: Path, project: Path) -> None:
    from trw_mcp.tools._evidence_receipts import validate_verification_receipt

    result = _record(run, project)
    receipt = VerificationReceipt.model_validate_json(result.path.read_bytes())
    (project / "one.py").write_text("one = 999\n", encoding="utf-8")
    verdict = validate_verification_receipt(receipt, receipt.mapping_digest, project)
    assert verdict.state is ReceiptState.STALE_CONTENT


def test_dirty_tree_records_null_git_sha_but_still_validates(run: Path, project: Path) -> None:
    (project / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    result = _record(run, project)
    receipt = VerificationReceipt.model_validate_json(result.path.read_bytes())
    assert receipt.git_sha is None
    assert result.typed_receipt_state == "valid"


def _too_many(project: Path) -> list[str]:
    return ["one.py"] * 257


def _oversized(project: Path) -> list[str]:
    big = project / "big.bin"
    with big.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)
    return ["big.bin"]


def _dotdot_inside(project: Path) -> list[str]:
    return ["sub/../../x"]


def _absolute_dotdot(project: Path) -> list[str]:
    return [f"{project}/nonexistent/../one.py"]


def _dir_symlink_escape(project: Path) -> list[str]:
    outside = project.parent / "outdir"
    outside.mkdir()
    (outside / "f.txt").write_text("x", encoding="utf-8")
    (project / "esc").symlink_to(outside)
    return ["esc/f.txt"]


def _dir_symlink_inside(project: Path) -> list[str]:
    (project / "alias").symlink_to(project / "sub")
    return ["alias/two.py"]


def _symlink(project: Path) -> list[str]:
    (project / "link.py").symlink_to("one.py")
    return ["link.py"]


def _outside(project: Path) -> list[str]:
    (project.parent / "secret.txt").write_text("x", encoding="utf-8")
    return ["../secret.txt"]


def _absolute_outside(project: Path) -> list[str]:
    (project.parent / "secret.txt").write_text("x", encoding="utf-8")
    return [str(project.parent / "secret.txt")]


@pytest.mark.parametrize(
    ("overrides", "paths", "reason"),
    [
        ({"subject": "not-a-sha"}, None, "subject_invalid"),
        ({"subject": "A" * 40}, None, "subject_invalid"),
        ({"subject": "a" * 39}, None, "subject_invalid"),
        ({"exit_code": "0"}, None, "exit_code_invalid"),
        ({"exit_code": True}, None, "exit_code_invalid"),
        ({"check": ""}, None, "check_invalid"),
        ({"check": "x" * 5000}, None, "check_invalid"),
        ({"note": "n" * 5000}, None, "note_invalid"),
        ({"evidence_paths": []}, None, "evidence_missing"),
        ({}, ["missing.py"], "evidence_path_invalid"),
        ({}, ["sub"], "evidence_path_invalid"),
        ({}, _outside, "evidence_path_invalid"),
        ({}, _absolute_outside, "evidence_path_invalid"),
        ({}, _symlink, "evidence_path_invalid"),
        ({}, _dotdot_inside, "evidence_path_invalid"),
        ({}, _absolute_dotdot, "evidence_path_invalid"),
        ({}, _dir_symlink_escape, "evidence_path_invalid"),
        ({}, _dir_symlink_inside, "evidence_path_invalid"),
        ({}, _too_many, "evidence_too_many"),
        ({}, _oversized, "evidence_unreadable"),
    ],
)
def test_refusals_write_nothing(
    run: Path, project: Path, overrides: dict[str, object], paths: object, reason: str
) -> None:
    if callable(paths):
        overrides = {**overrides, "evidence_paths": paths(project)}
    elif paths is not None:
        overrides = {**overrides, "evidence_paths": paths}
    with pytest.raises(VerificationReceiptRefusedError) as excinfo:
        _record(run, project, **overrides)
    assert excinfo.value.reason_code == reason
    assert not (run / "meta" / "receipts" / "verification").exists()


def test_evidence_file_deleted_mid_check_is_a_typed_refusal(
    run: Path, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_resolve = Path.resolve

    def vanishing_resolve(self: Path, strict: bool = False) -> Path:
        if strict and self.name == "one.py":
            raise FileNotFoundError(str(self))
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", vanishing_resolve)
    with pytest.raises(VerificationReceiptRefusedError) as excinfo:
        _record(run, project, evidence_paths=["one.py"])
    assert excinfo.value.reason_code == "evidence_path_invalid"
    assert not (run / "meta" / "receipts" / "verification").exists()


def test_missing_run_dir_is_refused(project: Path) -> None:
    with pytest.raises(VerificationReceiptRefusedError) as excinfo:
        _record(project / "no-such-run", project)
    assert excinfo.value.reason_code == "run_invalid"


def _cli(run: Path, project: Path, *extra: str) -> argparse.Namespace:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    argv = ["receipt", "verify", "--run", str(run), "--subject", SUBJECT, "--check", "chk", "--exit-code", "0", *extra]
    return _build_arg_parser().parse_args(argv)


def test_cli_success_prints_json_and_exits_zero(
    run: Path, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.tools._receipt_cli import run_receipt

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    args = _cli(run, project, "--evidence", "one.py", "--evidence", "sub/two.py", "--json")
    with pytest.raises(SystemExit) as excinfo:
        run_receipt(args)
    assert excinfo.value.code == 0
    document = json.loads(capsys.readouterr().out)
    assert set(document) >= {"receipt_id", "typed_receipt_state", "path", "validation", "passed"}
    assert document["typed_receipt_state"] == "valid"
    assert Path(document["path"]).exists()


def test_cli_refusal_exits_one_and_writes_nothing(
    run: Path, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.tools._receipt_cli import run_receipt

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    args = _cli(run, project, "--evidence", "../escape.py", "--json")
    with pytest.raises(SystemExit) as excinfo:
        run_receipt(args)
    assert excinfo.value.code == 1
    document = json.loads(capsys.readouterr().out)
    assert document["error"] == "evidence_path_invalid"
    assert not (run / "meta" / "receipts" / "verification").exists()


def test_cli_human_output_names_id_and_state(
    run: Path, project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.tools._receipt_cli import run_receipt

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    with pytest.raises(SystemExit) as excinfo:
        run_receipt(_cli(run, project, "--evidence", "one.py"))
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "verification-" in out and "valid" in out
