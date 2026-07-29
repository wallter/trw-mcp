"""PRD-SEC-013 review round 5 (2026-07-25) — the Python-side half of seven bypasses.

Four findings live here. Two of them were INTRODUCED by the round-4 fix, which is
why every test below pairs the fail-closed direction with a bystander: the round-4
regression was not "a check was too weak", it was "a check started firing for a
project nobody opted into".

* **F-D** — ``record_enrollment_evidence`` wrote with a plain ``Path.write_text``
  in the same module that ships ``read_bytes_nofollow`` to stop symlink
  retargeting on READS. A symlink at the evidence path made the security control
  create an attacker-named file outside the repository, reachable from the real
  post-edit hook, from ``enrollment status``, and from ``refresh_hook_digest`` —
  which the INSTALLER calls.
* **F-E** — ``check_enrollment_status`` self-healed the evidence file BEFORE
  comparing any digest, so the entire bar for permanently arming a project was a
  planted file containing ``schema_version: 1``. Delete the plant afterwards and
  the project reads ``stale`` forever with no un-arm path. That is exactly the
  harm the round-4 fix existed to prevent, re-created by the fix.
* **F-B** — ``_git_run.read_blob`` returned ``None`` for "not in that tree" AND
  for "git could not answer", and every caller reads ``None`` as "there genuinely
  is no contract at this rev". A stub failing only ``git rev-parse <rev>:<path>``
  took pre-commit and pre-push from ``1 BLOCKED`` to a silent ``0``.
* **F-F** — the shell tested ``[ -f ]`` and Python tested ``Path.exists()``, so a
  directory or a symlink to ``/dev/null`` at the evidence path left both hooks
  INERT while ``enrollment status`` reported ``stale`` and exited 1: the tool
  asserting a protection that was not running.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._intent_contract_git import (
    CONTRACT_PATH,
    PROTECTED,
    commit_all,
    contract_yaml,
    git,
    pytest_skip_no_git,
    seed_contract_repo,
    write,
)
from tests._intent_contract_hooks import CONTRACT_REL, EVIDENCE_REL, MARKER_REL, hook_project, make_project
from trw_mcp.security.intent_contract._git_run import BlobUnreadable, read_blob
from trw_mcp.security.intent_contract.enrollment import (
    ENROLLMENT_SCHEMA_VERSION,
    check_enrollment_status,
    clear_enrollment,
    enrollment_evidence_present,
    record_enrollment_evidence,
    refresh_hook_digest,
    write_enrollment,
)
from trw_mcp.security.intent_contract.paths import APPROVALS_PATH, ConfinementError
from trw_mcp.security.intent_contract.signed_commit import check_commit_range
from trw_mcp.security.intent_contract.weaken_edit_detector import (
    UNANSWERABLE_CLAIM_ID,
    approval_exists,
    detect_range_weaken_then_edit,
    detect_same_commit_weaken,
    detect_staged_weaken,
    record_approval,
)

_SRC = str(Path(__file__).resolve().parents[1] / "src")


# --- F-D: the write path gets the discipline the read path already had --------


def _victim(tmp_path: Path) -> Path:
    outside = tmp_path / "OUTSIDE-THE-REPO"
    outside.mkdir(parents=True, exist_ok=True)
    return outside / "planted.conf"


def test_fd_evidence_write_refuses_a_symlink_pointing_out_of_the_repo(tmp_path: Path) -> None:
    """The exact repro: a DANGLING symlink, so ``.exists()`` says "nothing here"."""
    project = make_project(tmp_path / "proj", enroll=False)
    victim = _victim(tmp_path)
    (project / EVIDENCE_REL).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(victim, project / EVIDENCE_REL)

    assert record_enrollment_evidence(project) is False
    assert not victim.exists(), f"the security control created {victim} outside its own project"


def test_fd_evidence_write_refuses_a_symlink_that_stays_inside_the_repo(tmp_path: Path) -> None:
    """Confinement is not the only guard: O_NOFOLLOW must refuse an in-repo symlink too.

    Reverting only the ``classify_target`` half would leave this green, so it is
    kept separate from the escape case above.
    """
    project = make_project(tmp_path / "proj", enroll=False)
    inside = project / ".trw" / "already-here.yaml"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("untouched\n", encoding="utf-8")
    os.symlink(inside, project / EVIDENCE_REL)

    assert record_enrollment_evidence(project) is False
    assert inside.read_text(encoding="utf-8") == "untouched\n"


def test_fd_marker_write_refuses_a_symlinked_marker_path(tmp_path: Path) -> None:
    """The same shape one file over: ``_write_marker`` had the same plain write."""
    project = make_project(tmp_path / "proj", enroll=False)
    victim = _victim(tmp_path)
    (project / MARKER_REL).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(victim, project / MARKER_REL)

    with pytest.raises(ConfinementError):
        write_enrollment(project, CONTRACT_REL, allow_missing_hooks=True)
    assert not victim.exists()


def test_fd_an_ordinary_enrollment_still_writes_both_artifacts(tmp_path: Path) -> None:
    """Bystander for the whole confinement change: the normal path must still work."""
    project = make_project(tmp_path / "proj", enroll=False)
    write_enrollment(project, CONTRACT_REL, allow_missing_hooks=True)

    assert (project / MARKER_REL).is_file()
    assert (project / EVIDENCE_REL).is_file()
    assert check_enrollment_status(project, CONTRACT_REL) == "current"


def test_fd_the_approval_append_refuses_a_symlinked_evidence_path(tmp_path: Path) -> None:
    """Found by sweeping the package for the F-D shape, not by the probe.

    ``record_approval`` was the last plain ``path.open("a")`` on a control-plane
    path, so a symlink there had the security tool append attacker-supplied text
    to an attacker-chosen file.
    """
    project = make_project(tmp_path / "proj", enroll=False)
    outside = tmp_path / "OUTSIDE-THE-REPO"
    outside.mkdir()
    victim = outside / "authorized_keys"
    victim.write_text("untouched\n", encoding="utf-8")
    approvals = project / APPROVALS_PATH
    approvals.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(victim, approvals)

    with pytest.raises(OSError):
        record_approval(project, diff_hash="d", session_id="s", claim_id="C-1", note="n")
    assert victim.read_text(encoding="utf-8") == "untouched\n"


def test_fd_an_ordinary_approval_still_records(tmp_path: Path) -> None:
    """Bystander: the approval path that clears a finding must keep working."""
    project = make_project(tmp_path / "proj", enroll=False)
    record_approval(project, diff_hash="d", session_id="other-session", claim_id="C-1", note="reviewed")
    assert approval_exists(project, "d", "this-session") is True


# --- F-E: self-heal must require evidence that enrollment really happened -----


def _plant_bare_marker(project: Path) -> None:
    """The whole attack: two words in a file nobody validated."""
    (project / MARKER_REL).parent.mkdir(parents=True, exist_ok=True)
    (project / MARKER_REL).write_text(f"schema_version: {ENROLLMENT_SCHEMA_VERSION}\n", encoding="utf-8")


def test_fe_a_planted_marker_never_mints_enrollment_evidence(tmp_path: Path) -> None:
    """Read the status, delete the plant, and the project must forget it happened."""
    project = make_project(tmp_path / "proj", enroll=False)
    _plant_bare_marker(project)

    assert check_enrollment_status(project, CONTRACT_REL) == "stale"
    assert not enrollment_evidence_present(project), "a read minted permanent enrollment evidence"

    (project / MARKER_REL).unlink()
    assert check_enrollment_status(project, CONTRACT_REL) == "never_enrolled"


def test_fe_refresh_hook_digest_never_mints_enrollment_evidence(tmp_path: Path) -> None:
    """The installer calls this. Its own comment claimed it could not arm anyone."""
    project = make_project(tmp_path / "proj", enroll=False)
    _plant_bare_marker(project)

    refresh_hook_digest(project)
    assert not enrollment_evidence_present(project)

    (project / MARKER_REL).unlink()
    assert check_enrollment_status(project, CONTRACT_REL) == "never_enrolled"


def test_fe_a_verified_marker_still_self_heals_the_evidence_file(tmp_path: Path) -> None:
    """The upgrade path the self-heal exists for must survive the gating.

    A project enrolled BEFORE the evidence file existed is exactly a project whose
    marker verifies and whose evidence is missing, so tightening the gate to
    "every digest matches" must not cost it.
    """
    project = make_project(tmp_path / "proj", enroll=False)
    write_enrollment(project, CONTRACT_REL, allow_missing_hooks=True)
    (project / EVIDENCE_REL).unlink()
    assert not enrollment_evidence_present(project)

    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert enrollment_evidence_present(project), "the pre-evidence upgrade path was lost"


def test_fe_a_stale_but_genuine_marker_does_not_mint_either(tmp_path: Path) -> None:
    """Deliberate consequence, pinned so it is a decision rather than a surprise.

    A marker whose digests no longer match cannot distinguish itself from a
    plant, so it does not mint. It is already failing closed on its own presence,
    and a re-enrollment mints the evidence.
    """
    project = make_project(tmp_path / "proj", enroll=False)
    write_enrollment(project, CONTRACT_REL, allow_missing_hooks=True)
    (project / EVIDENCE_REL).unlink()
    (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")

    assert check_enrollment_status(project, CONTRACT_REL) == "stale"
    assert not enrollment_evidence_present(project)


def test_fe_unenroll_is_an_actual_opt_out(tmp_path: Path) -> None:
    """The un-arm path F-E said did not exist except by ignoring a DO NOT DELETE."""
    project = make_project(tmp_path / "proj", enroll=False)
    write_enrollment(project, CONTRACT_REL, allow_missing_hooks=True)

    removed = clear_enrollment(project)
    assert set(removed) == {MARKER_REL, EVIDENCE_REL}
    assert check_enrollment_status(project, CONTRACT_REL) == "never_enrolled"


def test_fe_unenroll_never_follows_a_symlink_out_of_the_project(tmp_path: Path) -> None:
    """The opt-out is a DELETE path, so it gets confined like the write paths."""
    project = make_project(tmp_path / "proj", enroll=False)
    outside = tmp_path / "OUTSIDE-THE-REPO"
    outside.mkdir()
    victim = outside / "keep-me.conf"
    victim.write_text("untouched\n", encoding="utf-8")
    (project / EVIDENCE_REL).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(victim, project / EVIDENCE_REL)

    clear_enrollment(project)
    assert victim.is_file(), "unenroll deleted a file outside the project"


# --- F-F: one shape rule, shared by the shell and by Python -------------------

_EVIDENCE_SHAPES: list[tuple[str, str]] = [
    ("regular-file", "file"),
    ("symlink-to-regular-file", "symlink_file"),
    ("directory", "dir"),
    ("symlink-to-dev-null", "symlink_devnull"),
    ("dangling-symlink", "symlink_dangling"),
    ("absent", "absent"),
]


def _make_shape(project: Path, kind: str) -> None:
    path = project / EVIDENCE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        path.write_text("schema_version: 1\n", encoding="utf-8")
    elif kind == "symlink_file":
        real = project / ".trw" / "real-evidence.yaml"
        real.write_text("schema_version: 1\n", encoding="utf-8")
        os.symlink(real, path)
    elif kind == "dir":
        path.mkdir()
    elif kind == "symlink_devnull":
        os.symlink("/dev/null", path)
    elif kind == "symlink_dangling":
        os.symlink(project / ".trw" / "nothing-here", path)


@pytest.mark.parametrize(("label", "kind"), _EVIDENCE_SHAPES)
def test_ff_shell_and_python_agree_on_what_counts_as_evidence(tmp_path: Path, label: str, kind: str) -> None:
    """``[ -f ]`` is the contract. Python must answer identically for every shape."""
    project = tmp_path / label
    project.mkdir()
    _make_shape(project, kind)

    shell = subprocess.run(
        ["sh", "-c", 'if [ -f "$1" ]; then echo yes; else echo no; fi', "_", str(project / EVIDENCE_REL)],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    shell_says = shell.stdout.strip() == "yes"
    assert enrollment_evidence_present(project) is shell_says, (
        f"{label}: shell says {shell_says}, python says {enrollment_evidence_present(project)}"
    )


def test_ff_the_shape_matrix_is_not_vacuous(tmp_path: Path) -> None:
    """Control for the parametrization: it must contain both answers.

    An all-``False`` (or all-``True``) matrix would let the two layers agree by
    accident while still disagreeing on the shapes that matter.
    """
    answers = set()
    for label, kind in _EVIDENCE_SHAPES:
        project = tmp_path / f"matrix-{label}"
        project.mkdir()
        _make_shape(project, kind)
        answers.add(enrollment_evidence_present(project))
    assert answers == {True, False}


def test_ff_a_squatted_evidence_path_reads_as_not_enrolled_on_both_sides(tmp_path: Path) -> None:
    """The tool must stop claiming protection the hooks are not running.

    Before: hooks inert (``[ -f ]`` false) while status printed ``stale`` and
    exited 1. Now both say "never enrolled" — and the CLI names the squatted path
    rather than downgrading silently.
    """
    project = make_project(tmp_path / "proj", enroll=False)
    (project / EVIDENCE_REL).parent.mkdir(parents=True, exist_ok=True)
    (project / EVIDENCE_REL).mkdir()

    assert check_enrollment_status(project, CONTRACT_REL) == "never_enrolled"

    result = subprocess.run(
        [sys.executable, "-m", "trw_mcp.security.intent_contract.enrollment", "status", "--root", str(project)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
        check=False,
        shell=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "never_enrolled"
    assert "is not a regular file" in result.stderr


# --- F-B: "could not read that rev" is not "no contract at that rev" ----------


@pytest.fixture
def blob_stub(tmp_path: Path) -> Path:
    """A ``git`` that answers everything EXCEPT ``rev-parse <rev>:<path>``.

    Deliberately narrow: it is the ONLY query ``read_blob`` uses to locate a blob,
    so every other check in the package keeps working and the measurement isolates
    the erased distinction rather than a generally-broken git.
    """
    stub_dir = tmp_path / "stubbin"
    stub_dir.mkdir()
    real_git = subprocess.run(["which", "git"], capture_output=True, text=True, check=True, shell=False).stdout.strip()
    stub = stub_dir / "git"
    stub.write_text(
        f'#!/bin/sh\ncase "$1" in rev-parse) case "$2" in *:*) exit 128;; esac;; esac\nexec {real_git} "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub_dir


def _weaken_and_stage(repo: Path) -> None:
    write(repo, CONTRACT_PATH, contract_yaml(state="suspect"))
    write(repo, PROTECTED, "def guard():\n    return False\n")
    git(repo, "add", "-A")


@pytest_skip_no_git
def test_fb_read_blob_raises_rather_than_reporting_an_absent_path(
    tmp_path: Path, blob_stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reader is where the distinction belongs — five call sites needed it."""
    repo, _ = seed_contract_repo(tmp_path, "reader")
    assert read_blob(repo, "HEAD", CONTRACT_PATH) is not None
    assert read_blob(repo, "HEAD", "no/such/file.yaml") is None, "a genuinely absent path must stay None"

    monkeypatch.setenv("PATH", f"{blob_stub}{os.pathsep}{os.environ['PATH']}")
    with pytest.raises(BlobUnreadable):
        read_blob(repo, "HEAD", CONTRACT_PATH)


@pytest_skip_no_git
def test_fb_a_staged_weaken_still_blocks_when_the_blob_read_is_sabotaged(
    tmp_path: Path, blob_stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _ = seed_contract_repo(tmp_path, "staged")
    _weaken_and_stage(repo)
    healthy = detect_staged_weaken(repo)
    assert healthy is not None and healthy.claim_id == "C-1", "the baseline must actually detect"

    monkeypatch.setenv("PATH", f"{blob_stub}{os.pathsep}{os.environ['PATH']}")
    sabotaged = detect_staged_weaken(repo)
    assert sabotaged is not None, "a check that could not run reported a clean tree"
    assert sabotaged.claim_id == UNANSWERABLE_CLAIM_ID, "it must say 'could not evaluate', not 'you weakened a claim'"


@pytest_skip_no_git
def test_fb_the_commit_detectors_fail_closed_under_the_same_stub(
    tmp_path: Path, blob_stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = seed_contract_repo(tmp_path, "commits")
    _weaken_and_stage(repo)
    head = commit_all(repo, "weaken and edit")
    assert getattr(detect_same_commit_weaken(repo, head), "claim_id", None) == "C-1"
    assert [f.claim_id for f in detect_range_weaken_then_edit(repo, base, head)] == ["C-1"]

    monkeypatch.setenv("PATH", f"{blob_stub}{os.pathsep}{os.environ['PATH']}")
    same = detect_same_commit_weaken(repo, head)
    assert same is not None and same.claim_id == UNANSWERABLE_CLAIM_ID
    ranged = detect_range_weaken_then_edit(repo, base, head)
    assert ranged and all(f.claim_id == UNANSWERABLE_CLAIM_ID for f in ranged)


@pytest_skip_no_git
def test_fb_the_signature_gate_still_flags_the_commit_under_the_same_stub(
    tmp_path: Path, blob_stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR01 reads the same blobs, so it was silenced by the same two lines."""
    repo, base = seed_contract_repo(tmp_path, "signature")
    _weaken_and_stage(repo)
    head = commit_all(repo, "weaken and edit")
    assert [v.sha for v in check_commit_range(repo, base, head)] == [head], "the baseline must actually enforce"

    monkeypatch.setenv("PATH", f"{blob_stub}{os.pathsep}{os.environ['PATH']}")
    assert [v.sha for v in check_commit_range(repo, base, head)] == [head]


@pytest_skip_no_git
def test_fb_a_healthy_repo_with_no_weakening_stays_silent(tmp_path: Path) -> None:
    """Bystander for the whole F-B change: unreadable must not become universal.

    A commit that touches nothing protected, under a perfectly healthy git, must
    still produce zero findings — otherwise "fail closed on unanswerable" has
    quietly become "fail closed on everything".
    """
    repo, base = seed_contract_repo(tmp_path, "quiet")
    write(repo, "unrelated.txt", "hello\n")
    head = commit_all(repo, "unrelated change")

    assert detect_same_commit_weaken(repo, head) is None
    assert detect_range_weaken_then_edit(repo, base, head) == []
    assert check_commit_range(repo, base, head) == []
    assert detect_staged_weaken(repo) is None


@pytest_skip_no_git
def test_fb_an_enrolled_project_under_the_stub_reports_current_not_a_lie(tmp_path: Path) -> None:
    """The stub does not touch enrollment, so enrollment must not start reporting.

    Paired with the four tests above: they prove the detectors stop being silent;
    this proves the fix did not spill into an unrelated verdict.
    """
    project = hook_project(tmp_path, "enrolled-under-stub")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
