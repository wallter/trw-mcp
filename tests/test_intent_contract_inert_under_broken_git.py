"""PRD-SEC-013 review round 3 — F2: a broken git is not evidence of enrollment.

The N10 fix (2026-07-24) ruled that ``.git`` present plus a failing ``git
rev-parse --git-dir`` means "stay armed", on the premise that reaching that state
was pathological because you needed git to create the ``.git``. The premise was
wrong. Four states produce it with NO attacker present, on projects that never
opted in:

* ``.git`` is a FILE pointing at a gitdir that no longer exists — a pruned
  worktree, a deinit'd submodule;
* a partial or aborted clone (``.git`` exists, ``HEAD`` does not);
* a syntax error in the user's global ``~/.gitconfig``. ``GIT_CONFIG_GLOBAL`` is
  in the child-env allowlist, so it reaches the checker's git too;
* dubious ownership — a Docker/CI bind mount, a sudo-created clone. This is the
  most common of the four and it is covered BY CONSTRUCTION here rather than by
  enumeration: the fix never inspects WHY git failed, only whether an independent
  filesystem signal exists.

``data/settings.json`` registers both edit-time hooks unconditionally on
Write|Edit|MultiEdit for every installed project, so the blast radius was every
user, not every enrolled user.

The fix is a durable, git-free enrollment signal
(``.trw/intent-enrollment-evidence.yaml``). Its paired security property — an
ENROLLED project with a sabotaged git must STILL block — is asserted in the same
tests, because fixing one by surrendering the other is not a fix.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._intent_contract_git import git, init_repo, pytest_skip_no_git
from tests._intent_contract_hooks import (
    CONTRACT_REL,
    EVIDENCE_REL,
    MARKER_REL,
    POST_HOOK,
    PRE_HOOK,
    contract_yaml,
    hook_project,
    pytest_skip_no_sh,
    run_hook,
    write_hook_env,
)
from trw_mcp.security.intent_contract.enrollment import (
    check_enrollment_status,
    enrollment_evidence_path,
    marker_is_tracked,
    record_enrollment_evidence,
    write_enrollment,
)

Sabotage = Callable[[Path], None]


def _dotgit_file_pointing_nowhere(root: Path) -> None:
    shutil.rmtree(root / ".git")
    (root / ".git").write_text("gitdir: /nonexistent/worktrees/gone\n", encoding="utf-8")


def _partial_clone(root: Path) -> None:
    (root / ".git" / "HEAD").unlink()


def _broken_gitconfig_env(tmp_path: Path) -> dict[str, str]:
    config = tmp_path / "broken.gitconfig"
    config.write_text("[user\nname = unterminated section\n", encoding="utf-8")
    return {"GIT_CONFIG_GLOBAL": str(config)}


_BENIGN_SABOTAGE: list[tuple[str, Sabotage]] = [
    ("dotgit-file-missing-target", _dotgit_file_pointing_nowhere),
    ("partial-clone-no-HEAD", _partial_clone),
]


def _committed_repo(tmp_path: Path, name: str, *, enroll: bool) -> Path:
    """A project with the real hooks installed and everything committed."""
    project = hook_project(tmp_path, name, enroll=enroll)
    init_repo(project.parent, project.name)
    git(project, "add", "-A", "-f")
    git(project, "commit", "-q", "-m", "seed")
    return project


# --- the bystander half: never enrolled must stay INERT ----------------------


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
@pytest.mark.parametrize(("label", "sabotage"), _BENIGN_SABOTAGE)
def test_f2_a_never_enrolled_project_with_a_broken_repo_stays_inert(
    tmp_path: Path, hook: str, label: str, sabotage: Sabotage
) -> None:
    """Measured before the fix: exit 2 at all four control points, no attacker."""
    project = _committed_repo(tmp_path, f"f2-{label}-{hook}", enroll=False)
    sabotage(project)
    result = run_hook(project, hook)
    assert result.returncode == 0, f"{label} blocked a project that never opted in\n{result.stderr}"


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_a_never_enrolled_project_with_a_broken_global_gitconfig_stays_inert(tmp_path: Path, hook: str) -> None:
    """GIT_CONFIG_GLOBAL is allowlisted into the child env, so a user's typo lands here."""
    project = _committed_repo(tmp_path, f"f2-gitconfig-{hook}", enroll=False)
    result = run_hook(project, hook, extra_env=_broken_gitconfig_env(tmp_path))
    assert result.returncode == 0, f"a broken ~/.gitconfig blocked a bystander\n{result.stderr}"


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_a_never_enrolled_project_with_an_unusable_git_binary_stays_inert(tmp_path: Path, hook: str) -> None:
    """Covers dubious-ownership and every other refusal BY CONSTRUCTION.

    A git that refuses for ANY reason is one shape here, because nothing in the
    fix reads the failure text — an ownership refusal, a broken config and a
    shadowed binary are indistinguishable to it, and all three are inert on a
    project with no enrollment evidence.
    """
    project = _committed_repo(tmp_path, f"f2-stub-{hook}", enroll=False)
    stub_dir = tmp_path / f"stub-{hook}"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text("#!/bin/sh\nexit 128\n", encoding="utf-8")
    stub.chmod(0o755)
    result = run_hook(project, hook, extra_env={"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 0, f"an unusable git blocked a bystander\n{result.stderr}"


#: Everything the hooks invoke BEFORE `command -v python3`. A PATH holding only
#: these forces the decision into the shell's own no-python3 branch.
_PRE_PYTHON_TOOLS = ("sh", "cat", "date", "dirname", "git")


def _path_without_python3(tmp_path: Path, name: str) -> str:
    bin_dir = tmp_path / name
    bin_dir.mkdir()
    for tool in _PRE_PYTHON_TOOLS:
        located = shutil.which(tool)
        if located:
            (bin_dir / tool).symlink_to(located)
    assert shutil.which("python3", path=str(bin_dir)) is None
    return str(bin_dir)


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_the_shell_preamble_alone_must_also_read_a_bystander_as_unenrolled(tmp_path: Path, hook: str) -> None:
    """The shell has a fail-closed branch that NEVER reaches Python. Isolate it.

    Every other inert test above is satisfied by the Python layer answering
    "never_enrolled" — so they stay green even if the shell preamble keeps the old
    ruling, and they are therefore not evidence for the shell half.

    The isolator used to be an unreadable ``lib-trw.sh``. Round 5 removed the
    hook's dependency on the lib entirely (finding F-A), so that branch no longer
    exists and this test would have quietly started measuring Python's answer
    instead — the exact way a test stops proving what its name says. The branch
    that IS still shell-only is ``command -v python3``, so the isolator is a PATH
    with no python3 on it.
    """
    project = _committed_repo(tmp_path, f"f2-shell-{hook}", enroll=False)
    _partial_clone(project)
    result = run_hook(project, hook, extra_env={"PATH": _path_without_python3(tmp_path, f"nopy-{hook}")})
    assert result.returncode == 0, f"the shell preamble blocked a bystander on its own\n{result.stderr}"


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_the_shell_preamble_still_blocks_an_enrolled_project_it_cannot_check(tmp_path: Path, hook: str) -> None:
    """Paired half of the test above: the shell-only branch must stay fail-closed."""
    project = _committed_repo(tmp_path, f"f2-shell-armed-{hook}", enroll=True)
    (project / MARKER_REL).unlink()  # only the evidence file is left
    _partial_clone(project)
    result = run_hook(project, hook, extra_env={"PATH": _path_without_python3(tmp_path, f"nopy-armed-{hook}")})
    assert result.returncode == 2, f"an enrolled project stopped failing closed in shell\n{result.stderr}"
    assert "python3 is unavailable" in result.stderr, "the block did not come from the shell-only branch"


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_an_unreadable_lib_is_now_decided_by_the_enrollment_digest(tmp_path: Path, hook: str) -> None:
    """What replaced the shell branch above, asserted rather than assumed.

    ``expected_hook_digest`` covers ``lib-trw.sh``, so ``chmod 000`` digests it as
    absent and enrollment reads ``stale`` — which the Python entry point fails
    closed on. That is a stronger control than the shell branch it replaced, since
    it reasons about the tamper instead of about a failed source, and the paired
    bystander below shows it still costs an opted-out project nothing.
    """
    project = _committed_repo(tmp_path, f"f2-lib-armed-{hook}", enroll=True)
    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.chmod(0o000)
    try:
        assert check_enrollment_status(project, CONTRACT_REL) == "stale"
        result = run_hook(project, hook)
    finally:
        lib.chmod(0o644)
    assert result.returncode == 2, f"an unreadable lib stopped failing closed\n{result.stderr}"


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_an_unreadable_lib_still_costs_a_bystander_nothing(tmp_path: Path, hook: str) -> None:
    project = _committed_repo(tmp_path, f"f2-lib-inert-{hook}", enroll=False)
    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.chmod(0o000)
    try:
        result = run_hook(project, hook)
    finally:
        lib.chmod(0o644)
    assert result.returncode == 0, f"an unreadable lib blocked an opted-out project\n{result.stderr}"


# --- the paired security half: enrolled + sabotaged git must STILL block ------


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
@pytest.mark.parametrize(("label", "sabotage"), _BENIGN_SABOTAGE)
def test_f2_an_enrolled_project_with_the_same_broken_repo_still_blocks(
    tmp_path: Path, hook: str, label: str, sabotage: Sabotage
) -> None:
    """N10 preserved: the marker is deleted and git cannot answer, yet it blocks.

    This is the constraint that makes F2 hard. If it goes red, the exposure was
    traded away rather than fixed.
    """
    project = _committed_repo(tmp_path, f"f2-armed-{label}-{hook}", enroll=True)
    if hook == PRE_HOOK:
        # Staleness is what makes the METADATA-ONLY pre-write guard block at all.
        (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    assert run_hook(project, hook).returncode == 2, "the baseline must actually enforce"

    (project / MARKER_REL).unlink()
    sabotage(project)
    write_hook_env(project, "export HOOKS_ENABLED=false\n")
    result = run_hook(project, hook)
    assert result.returncode == 2, f"{label} + a deleted marker disarmed an enrolled control\n{result.stderr}"


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f2_an_enrolled_project_with_a_broken_global_gitconfig_still_blocks(tmp_path: Path, hook: str) -> None:
    project = _committed_repo(tmp_path, f"f2-armed-gitconfig-{hook}", enroll=True)
    if hook == PRE_HOOK:
        (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    (project / MARKER_REL).unlink()
    result = run_hook(project, hook, extra_env=_broken_gitconfig_env(tmp_path))
    assert result.returncode == 2, f"a broken ~/.gitconfig disarmed an enrolled control\n{result.stderr}"


# --- the Python layer, where the same predicate lives ------------------------


@pytest_skip_no_git
def test_f2_python_never_enrolled_plus_unanswerable_git_is_never_enrolled(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, "py-inert")
    (repo / ".trw" / "contracts").mkdir(parents=True)
    (repo / CONTRACT_REL).write_text(contract_yaml(), encoding="utf-8")
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "contract, never enrolled")
    _partial_clone(repo)

    assert marker_is_tracked(repo) is False
    assert check_enrollment_status(repo, CONTRACT_REL) == "never_enrolled"


@pytest_skip_no_git
def test_f2_python_enrolled_plus_unanswerable_git_is_still_stale(tmp_path: Path) -> None:
    repo = init_repo(tmp_path, "py-armed")
    (repo / ".trw" / "contracts").mkdir(parents=True)
    (repo / CONTRACT_REL).write_text(contract_yaml(), encoding="utf-8")
    write_enrollment(repo, CONTRACT_REL, allow_missing_hooks=True)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "enrolled")

    (repo / MARKER_REL).unlink()
    _partial_clone(repo)
    assert marker_is_tracked(repo) is True
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale"


def test_f2_the_evidence_file_is_written_once_and_never_rewritten(tmp_path: Path) -> None:
    """Re-enrolling must cost no churn — the file's EXISTENCE is the whole signal."""
    (tmp_path / ".trw" / "contracts").mkdir(parents=True)
    (tmp_path / CONTRACT_REL).write_text(contract_yaml(), encoding="utf-8")

    write_enrollment(tmp_path, CONTRACT_REL, allow_missing_hooks=True)
    evidence = enrollment_evidence_path(tmp_path)
    assert evidence.exists()
    first = evidence.read_text(encoding="utf-8")

    write_enrollment(tmp_path, CONTRACT_REL, allow_missing_hooks=True)
    assert evidence.read_text(encoding="utf-8") == first
    assert record_enrollment_evidence(tmp_path) is False


def test_f2_an_upgraded_project_self_heals_its_evidence_from_a_valid_marker(tmp_path: Path) -> None:
    """Projects enrolled BEFORE the evidence file existed must not stay exposed.

    Without the self-heal they keep the pre-fix hole (marker deleted + git
    silenced = inert) until someone re-enrols, which they have no reason to do.
    """
    (tmp_path / ".trw" / "contracts").mkdir(parents=True)
    (tmp_path / CONTRACT_REL).write_text(contract_yaml(), encoding="utf-8")
    write_enrollment(tmp_path, CONTRACT_REL, allow_missing_hooks=True)

    enrollment_evidence_path(tmp_path).unlink()  # the legacy shape
    assert check_enrollment_status(tmp_path, CONTRACT_REL) == "current"
    assert enrollment_evidence_path(tmp_path).exists(), "a valid marker must re-create the durable signal"


def test_f2_self_heal_never_invents_evidence_for_a_project_that_never_enrolled(tmp_path: Path) -> None:
    """Bystander control: only a genuine, schema-valid marker may heal."""
    (tmp_path / ".trw" / "contracts").mkdir(parents=True)
    (tmp_path / CONTRACT_REL).write_text(contract_yaml(), encoding="utf-8")
    assert check_enrollment_status(tmp_path, CONTRACT_REL) == "never_enrolled"
    assert not enrollment_evidence_path(tmp_path).exists()

    (tmp_path / MARKER_REL).write_text("schema_version: 99\n", encoding="utf-8")
    assert check_enrollment_status(tmp_path, CONTRACT_REL) == "stale"
    assert not enrollment_evidence_path(tmp_path).exists(), "an unknown schema must not mint evidence"


def test_f2_a_committed_removal_of_the_evidence_file_is_a_control_plane_finding() -> None:
    """It is load-bearing now, so it costs the same signature as the contract."""
    from trw_mcp.security.intent_contract._control_plane import control_plane_findings

    def base(path: str) -> bytes | None:
        return b"schema_version: 1\n" if path == EVIDENCE_REL else None

    def candidate(path: str) -> bytes | None:
        del path
        return None

    findings = control_plane_findings(base, candidate)
    assert any(EVIDENCE_REL in finding for finding in findings), findings
    assert not control_plane_findings(base, base), "an unchanged tree must produce no finding"
