"""PRD-SEC-013 re-probe (2026-07-24): the six bypasses the FIXES themselves opened.

The first adversarial pass closed nine attacks. Attacking the closures found six
more, two of which were one-command total disarms. Every test here is written
against a REPRODUCED bypass — each one fails on the pre-fix tree — and each names
the finding it pins so a regression says which wall came down:

* N1 — a SYMLINK alias to an anchored file evaded enforcement.
* N2 — a HARDLINK out of a DIRECTORY-anchored tree evaded enforcement.
* N3 — the enrollment signal read the git INDEX, which the attacker can write.
* N4 — the marker's fail-closed fix moved the hole to the LEDGER.
* N5 — a corrupt marker yielded an override with NO ledger record.
* N6 — `chmod 000 .claude/hooks/lib-trw.sh` disarmed both hooks, invisibly.
* N7 — the override evidence was gitignored, so C9 could never see it.
* N8 — `HOOKS_ENABLED=false` in the gitignored `.trw/runtime/hook-env.sh` disarmed
  both hooks; so did `PATH=/nonexistent` in the same file, via `$(cat) || exit 0`.
* N10 — a `git` stub in any writable PATH directory made every history query fail,
  and a failure to ANSWER fell back to "never enrolled".

The counterpart of every fail-closed test is an INERT test: an unenrolled project,
and a project that never recorded an override, must stay untouched. Fail-closed
that also fires on bystanders is not a fix, it is a different bug.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests._intent_contract_git import git, init_repo, pytest_skip_no_git
from tests._intent_contract_hooks import (
    CONTRACT_REL,
    EVIDENCE_REL,
    HOOK_SRC,
    MARKER_REL,
    POST_HOOK,
    PRE_HOOK,
    PROTECTED,
    contract_yaml,
    hook_project,
    make_project,
    pytest_skip_no_sh,
    run_hook,
    write_hook_env,
)
from trw_mcp.security.intent_contract._anchors import enforceable_claims
from trw_mcp.security.intent_contract._control_plane import (
    EVIDENCE_VISIBILITY_PATH,
    EVIDENCE_VISIBILITY_RULES,
    control_plane_findings,
)
from trw_mcp.security.intent_contract.enrollment import check_enrollment_status, write_enrollment
from trw_mcp.security.intent_contract.ledger import (
    checkpoint_path,
    ledger_path,
    ledger_tamper_reason,
    record_violation,
)
from trw_mcp.security.intent_contract.loader import load_contract_bytes
from trw_mcp.security.intent_contract.paths import OPEN_VIOLATIONS_PATH
from trw_mcp.security.intent_contract.violations import (
    intent_violation_gate_block,
    record_gate_override,
    record_open_violation,
)

_HOOK_SRC = HOOK_SRC
_PRE_HOOK = PRE_HOOK
_POST_HOOK = POST_HOOK
_MARKER_REL = MARKER_REL


# --- N3: the enrollment signal must be anchored to HEAD, not to the index -----


def _enrolled_repo(tmp_path: Path, name: str) -> Path:
    """A committed, enrolled project — the state an attacker starts from."""
    repo = init_repo(tmp_path, name)
    make_project(repo)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "seed + enroll")
    assert check_enrollment_status(repo, CONTRACT_REL) == "current"
    return repo


@pytest_skip_no_git
def test_n3_git_rm_of_the_marker_reads_stale_not_never_enrolled(tmp_path: Path) -> None:
    """`git rm -f <marker>` is ONE uncommitted command that clears file AND index."""
    repo = _enrolled_repo(tmp_path, "git-rm")
    git(repo, "rm", "-f", "-q", _MARKER_REL)
    assert not (repo / _MARKER_REL).exists()
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale"


@pytest_skip_no_git
def test_n3_index_wipe_of_the_marker_reads_stale_not_never_enrolled(tmp_path: Path) -> None:
    """`git rm --cached` + `rm`, and a wholesale `.git/index` deletion, are the same attack."""
    repo = _enrolled_repo(tmp_path, "index-wipe")
    git(repo, "rm", "--cached", "-q", _MARKER_REL)
    (repo / _MARKER_REL).unlink()
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale"

    (repo / ".git" / "index").unlink()
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale"


@pytest_skip_no_git
def test_n3_inert_project_that_never_committed_a_marker_stays_never_enrolled(tmp_path: Path) -> None:
    """The release-safety property: HEAD anchoring must not enroll bystanders."""
    repo = init_repo(tmp_path, "inert")
    make_project(repo, enroll=False)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "seed without enrollment")
    assert check_enrollment_status(repo, CONTRACT_REL) == "never_enrolled"


# --- N6: a lib that cannot be sourced is a disarm, not an inconvenience -------


_hook_project = hook_project
_run_hook = run_hook


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
@pytest.mark.parametrize(
    ("sabotage", "label"),
    [
        (lambda lib: lib.chmod(0o000), "chmod-000"),
        (lambda lib: lib.write_text("if [ \n", encoding="utf-8"), "syntax-error"),
        (lambda lib: lib.write_text("exit 0\n", encoding="utf-8"), "self-exiting"),
    ],
)
def test_n6_unsourceable_lib_fails_closed_once_enrolled(
    tmp_path: Path, hook: str, sabotage: object, label: str
) -> None:
    """Every way of breaking the shared lib must block, not silently allow.

    Before the fix `. lib-trw.sh 2>/dev/null || exit 0` ran BEFORE the enrollment
    check, so `chmod 000` — which changes no tracked byte, leaves `git status`
    clean and enrollment reading `current` — turned both control points into
    unconditional exit 0.

    WHICH control answers this changed in round 5 and the outcome did not: the
    hook no longer consults the lib to decide anything (finding F-A), so the block
    now comes from `expected_hook_digest` covering lib-trw.sh — any tamper, content
    or mode, makes enrollment `stale` and the Python entry point fails closed. The
    seam is named in `test_f2_an_unreadable_lib_is_now_decided_by_the_enrollment_digest`.
    """
    project = _hook_project(tmp_path, f"n6-{label}-{hook}")
    sabotage(project / ".claude" / "hooks" / "lib-trw.sh")  # type: ignore[operator]
    result = _run_hook(project, hook)
    assert result.returncode == 2, f"{label} must fail closed; got {result.returncode}\n{result.stderr}"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n6_unsourceable_lib_is_still_a_no_op_before_enrollment(tmp_path: Path, hook: str) -> None:
    """Inert-by-default: an unenrolled project must never be blocked by a broken lib."""
    project = _hook_project(tmp_path, f"n6-inert-{hook}", enroll=False)
    (project / ".claude" / "hooks" / "lib-trw.sh").chmod(0o000)
    assert _run_hook(project, hook).returncode == 0


def test_n6_enrollment_digest_covers_the_shared_hook_library(tmp_path: Path) -> None:
    """The digest half of N6: lib-trw.sh belongs to `expected_hook_digest`.

    It registers no hook of its own, which is exactly why it was the blind spot.
    An unreadable file digests as absent, so a MODE change is as visible here as a
    content change.
    """
    project = _hook_project(tmp_path, "n6-digest")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"

    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.write_text(lib.read_text(encoding="utf-8") + "\n# quietly appended\n", encoding="utf-8")
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"

    write_enrollment(project, CONTRACT_REL)
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    lib.chmod(0o000)
    try:
        assert check_enrollment_status(project, CONTRACT_REL) == "stale"
    finally:
        lib.chmod(0o644)


# --- N8: a gitignored env file must not be able to disarm an enrolled control -


_write_hook_env = write_hook_env


def _stale_hook_project(tmp_path: Path, name: str) -> Path:
    """An enrolled project whose contract was edited after enrollment.

    Staleness is the one condition that makes the METADATA-ONLY pre-write guard
    block, so it is how N8 is pinned on the FR05 hook as well as the FR07 one.
    """
    project = _hook_project(tmp_path, name)
    (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"
    return project


@pytest_skip_no_sh
@pytest.mark.parametrize("switch", ["HOOKS_ENABLED", "TRW_HOOKS_ENABLED"])
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8_hook_env_kill_switch_cannot_disarm_an_enrolled_control(tmp_path: Path, hook: str, switch: str) -> None:
    """`echo 'export HOOKS_ENABLED=false' >> .trw/runtime/hook-env.sh` was a one-line
    total disarm of both enrolled control points, in a file git never shows.

    HOOKS_ENABLED is a convenience switch for advisory/ceremony hooks; enrollment
    is the opt-in for the SECURITY control, so the switch must not reach it.
    """
    project = (
        _stale_hook_project(tmp_path, f"n8-{switch}-{hook}")
        if hook == _PRE_HOOK
        else _hook_project(tmp_path, f"n8-{switch}-{hook}")
    )
    assert _run_hook(project, hook).returncode == 2, "the baseline must actually enforce"

    _write_hook_env(project, f"export {switch}=false\n")
    result = _run_hook(project, hook)
    assert result.returncode == 2, f"{switch}=false disarmed an enrolled control point\n{result.stderr}"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8_process_environment_kill_switch_cannot_disarm_an_enrolled_control(tmp_path: Path, hook: str) -> None:
    """The same switch arriving as an exported env var rather than via hook-env.sh."""
    project = (
        _stale_hook_project(tmp_path, f"n8-env-{hook}")
        if hook == _PRE_HOOK
        else _hook_project(tmp_path, f"n8-env-{hook}")
    )
    assert _run_hook(project, hook, extra_env={"HOOKS_ENABLED": "false"}).returncode == 2


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8_path_wipe_via_hook_env_cannot_disarm_an_enrolled_control(tmp_path: Path, hook: str) -> None:
    """The SECOND one-liner in the same gitignored file: `export PATH=/nonexistent`.

    Since finding F1 (2026-07-25) the shared lib runs only in subshells, so this
    particular vector can no longer reach the deciding shell's PATH at all — the
    attack is dead structurally rather than by a branch. The
    `_payload=$(cat) || exit 0` branch it originally exercised is pinned
    separately and directly by
    :func:`test_n8b_an_unreadable_payload_fails_closed_through_its_own_branch`,
    because this test alone stayed green when that branch was reverted (the trap
    independently yields 2), which made it evidence for a wall it was not
    holding up.
    """
    project = (
        _stale_hook_project(tmp_path, f"n8-path-{hook}")
        if hook == _PRE_HOOK
        else _hook_project(tmp_path, f"n8-path-{hook}")
    )
    assert _run_hook(project, hook).returncode == 2, "the baseline must actually enforce"

    _write_hook_env(project, "export PATH=/nonexistent\n")
    result = _run_hook(project, hook)
    assert result.returncode == 2, f"a wiped PATH disarmed an enrolled control point\n{result.stderr}"


def _shadow_bin(tmp_path: Path, name: str, command: str) -> str:
    """A writable PATH entry holding a *command* that always fails."""
    directory = tmp_path / f"{name}-bin"
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / command
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    return str(directory)


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8b_an_unreadable_payload_fails_closed_through_its_own_branch(tmp_path: Path, hook: str) -> None:
    """Pin the BRANCH, not merely the exit code (finding F5, 2026-07-25).

    Shadowing `cat` in a writable PATH directory is the agent-reachable form of
    the original PATH wipe, and it lands squarely on `_payload=$(cat)`. Asserting
    the branch's own message is what makes this test go red if that fail-closed
    branch is reverted — the exit code alone does not, because the trap produces
    2 for this project anyway.
    """
    project = (
        _stale_hook_project(tmp_path, f"n8b-{hook}") if hook == _PRE_HOOK else _hook_project(tmp_path, f"n8b-{hook}")
    )
    path = _shadow_bin(tmp_path, f"n8b-{hook}", "cat") + os.pathsep + os.environ["PATH"]
    result = _run_hook(project, hook, extra_env={"PATH": path})
    assert result.returncode == 2
    assert "the hook payload could not be read" in result.stderr, result.stderr


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8b_an_unreadable_payload_is_still_inert_before_enrollment(tmp_path: Path, hook: str) -> None:
    project = _hook_project(tmp_path, f"n8b-inert-{hook}", enroll=False)
    path = _shadow_bin(tmp_path, f"n8b-inert-{hook}", "cat") + os.pathsep + os.environ["PATH"]
    assert _run_hook(project, hook, extra_env={"PATH": path}).returncode == 0


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8_path_wipe_is_still_a_no_op_before_enrollment(tmp_path: Path, hook: str) -> None:
    """Inert-by-default counterpart: an unenrolled project keeps failing open even
    when the hook cannot read its own payload."""
    project = _hook_project(tmp_path, f"n8-path-inert-{hook}", enroll=False)
    _write_hook_env(project, "export PATH=/nonexistent\n")
    assert _run_hook(project, hook).returncode == 0


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n8_kill_switch_is_still_honoured_before_enrollment(tmp_path: Path, hook: str) -> None:
    """Inert-by-default control: light-mode profiles that never enrolled keep their
    short-circuit. Fail-closed that also fires on bystanders is a different bug."""
    project = _hook_project(tmp_path, f"n8-inert-{hook}", enroll=False)
    _write_hook_env(project, "export HOOKS_ENABLED=false\n")
    assert _run_hook(project, hook).returncode == 0
    assert _run_hook(project, hook, extra_env={"HOOKS_ENABLED": "false"}).returncode == 0


# --- N10: "never enrolled" must be POSITIVELY established, not a fallback ----
#
# `_trw_enrolled` resolved file -> HEAD -> index with both git legs behind
# `command -v git`. `command -v` only proves SOMETHING named git is on PATH, so a
# failure to ANSWER was indistinguishable from a negative answer and fell back to
# 0 — at which point the HOOKS_ENABLED switch became honourable again. Reachable
# with Bash alone: shadow git, rm the marker, write the switch into the gitignored
# hook-env.sh.


def _shadow_git(tmp_path: Path, name: str) -> str:
    """A writable PATH entry holding a ``git`` that always fails.

    ``~/.local/bin`` is on PATH and user-writable on a typical dev box, so this
    needs no privilege and no control of the hook's environment — which is what
    separates it from the unreachable "unset PATH" form.
    """
    directory = tmp_path / f"{name}-bin"
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / "git"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)
    return str(directory)


def _committed_hook_repo(tmp_path: Path, name: str, *, enroll: bool = True) -> Path:
    """An enrolled project with the hooks installed AND everything committed."""
    project = _hook_project(tmp_path, name, enroll=enroll)
    git(project, "init", "-q", "-b", "main")
    git(project, "config", "user.email", "sec013@example.test")
    git(project, "config", "user.name", "sec013")
    git(project, "config", "commit.gpgsign", "false")
    git(project, "add", "-A", "-f")
    git(project, "commit", "-q", "-m", "seed + enroll")
    return project


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n10_shadowed_git_plus_deleted_marker_cannot_disarm(tmp_path: Path, hook: str) -> None:
    """The full three-step Bash-only chain: shadow git, rm marker, flip the switch."""
    project = _committed_hook_repo(tmp_path, f"n10-{hook}")
    (project / _MARKER_REL).unlink()
    _write_hook_env(project, "export HOOKS_ENABLED=false\n")

    result = _run_hook(project, hook, extra_env={"PATH": _shadow_git(tmp_path, hook) + os.pathsep + os.environ["PATH"]})
    assert result.returncode == 2, f"a shadowed git disarmed an enrolled control point\n{result.stderr}"


@pytest_skip_no_git
@pytest_skip_no_sh
def test_n10_positive_control_a_working_git_repo_still_enforces(tmp_path: Path) -> None:
    """Without this, a change that broke the git legs outright would pass above.

    The pre-write guard allows an anchored path by design (FR05 step 8); the
    post-edit hook must still fail closed on its unevaluable falsifier.
    """
    project = _committed_hook_repo(tmp_path, "n10-positive")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert _run_hook(project, _PRE_HOOK).returncode == 0
    assert _run_hook(project, _POST_HOOK).returncode == 2


@pytest_skip_no_git
@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n10_a_real_repo_that_never_enrolled_stays_inert(tmp_path: Path, hook: str) -> None:
    """Bystander control: `.git` present, git working, nothing ever opted in."""
    project = _committed_hook_repo(tmp_path, f"n10-inert-{hook}", enroll=False)
    _write_hook_env(project, "export HOOKS_ENABLED=false\n")
    assert _run_hook(project, hook).returncode == 0


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [_PRE_HOOK, _POST_HOOK])
def test_n10_no_git_directory_at_all_stays_inert(tmp_path: Path, hook: str) -> None:
    """No `.git` is the ONE shape that genuinely means "no durable signal exists",
    and it is checked without git, so shadowing the binary cannot forge it."""
    project = _hook_project(tmp_path, f"n10-nogit-{hook}", enroll=False)
    assert not (project / ".git").exists()
    _write_hook_env(project, "export HOOKS_ENABLED=false\n")
    assert _run_hook(project, hook).returncode == 0


@pytest_skip_no_git
def test_n10_the_python_layer_reads_a_shadowed_git_as_stale_not_never_enrolled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same defect one layer down: `marker_is_tracked` backs `check_enrollment_status`
    AND the ledger's `_ledger_existed`, and both treat False as "inert"."""
    from trw_mcp.security.intent_contract._git_run import git_can_answer

    repo = _enrolled_repo(tmp_path, "n10-python")
    (repo / _MARKER_REL).unlink()
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale"  # baseline: git works

    monkeypatch.setenv("PATH", _shadow_git(tmp_path, "python") + os.pathsep + os.environ["PATH"])
    assert git_can_answer(repo) is False
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale", "a shadowed git must not read as never_enrolled"


@pytest_skip_no_git
def test_n10_the_warning_states_only_the_signal_it_actually_held(tmp_path: Path) -> None:
    """Fail-closed is right here; asserting WHY on evidence we do not have is not.

    Two DIFFERENT signals land on `stale` and they know different things. The
    evidence file is a plain filesystem record that says nothing whatsoever about
    git, so a message derived from it must not send the operator to `git
    checkout` a file git may not have; and the git-history message must not be
    printed for a project whose only signal was the evidence file.
    """
    from trw_mcp.security.intent_contract.enrollment import stale_enrollment_warning

    repo = _enrolled_repo(tmp_path, "n10-message")
    (repo / _MARKER_REL).unlink()

    from_evidence = stale_enrollment_warning(repo, CONTRACT_REL)
    assert EVIDENCE_REL in from_evidence, "it must name the signal it actually used"
    assert "git checkout" not in from_evidence, "the evidence file says nothing about git"
    assert "still tracked by git" not in from_evidence

    # Legacy shape: enrolled before the evidence file existed. git history is then
    # the ONLY second signal, and its wording is kept verbatim.
    (repo / EVIDENCE_REL).unlink()
    tracked = stale_enrollment_warning(repo, CONTRACT_REL)
    assert "still tracked by git" in tracked, "the genuinely-tracked wording must be kept verbatim"
    assert "This project is enrolled" in tracked


@pytest_skip_no_git
def test_the_documented_f2_residual_is_pinned_exactly_where_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trade taken for F2, written down as an executable fact rather than prose.

    Removing the evidence file AND the marker AND keeping git from answering IS a
    disarm. It is pinned here so nobody discovers it by accident and so a future
    change that closes it has to delete this test deliberately. What it costs to
    keep the old ruling instead is the three benign never-enrolled states in
    test_intent_contract_inert_under_broken_git.py, which block every write for
    users who never opted in.
    """
    repo = _enrolled_repo(tmp_path, "f2-residual")

    # Two of the three acts: still armed, because git can still answer.
    (repo / _MARKER_REL).unlink()
    (repo / EVIDENCE_REL).unlink()
    assert check_enrollment_status(repo, CONTRACT_REL) == "stale"

    # The third act completes the disarm.
    monkeypatch.setenv("PATH", _shadow_git(tmp_path, "residual") + os.pathsep + os.environ["PATH"])
    assert check_enrollment_status(repo, CONTRACT_REL) == "never_enrolled"


@pytest_skip_no_git
def test_n10_git_can_answer_is_true_for_both_honest_shapes(tmp_path: Path) -> None:
    """The inert-by-default halves: no repo at all, and a working repo."""
    from trw_mcp.security.intent_contract._git_run import git_can_answer

    bare = tmp_path / "no-repo"
    bare.mkdir()
    assert git_can_answer(bare) is True
    assert git_can_answer(_enrolled_repo(tmp_path, "n10-honest")) is True


# --- N11: an abort we did not decide is not evidence of safety ---------------
#
# The N6 probe only broke lib-trw.sh in ways that stopped it SOURCING. A tampered
# lib that parses fine and merely redefines a function the hook calls
# (`init_hook_timer() { exit 0; }`) sailed past that check: `exit` inside a
# sourced function aborts the hook's own shell, and the blanket EXIT trap turned
# that abort into exit 0. One appended line was a total disarm that survived every
# later invocation — and the very write that installed it was waved through by the
# hook it had just disabled. Found by sweeping for permissive fallbacks, not by a
# probe.


@pytest_skip_no_sh
@pytest.mark.parametrize("function", ["init_hook_timer", "log_hook_execution"])
def test_n11_a_lib_function_that_exits_cannot_disarm_the_post_edit_check(tmp_path: Path, function: str) -> None:
    """The post-edit hook blocks here, so a hijack must not be able to reach 0."""
    project = _hook_project(tmp_path, f"n11-{function}")
    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.write_text(f"{lib.read_text(encoding='utf-8')}\n{function}() {{ exit 0; }}\n", encoding="utf-8")

    result = _run_hook(project, _POST_HOOK)
    assert result.returncode == 2, f"a hijacked {function} disarmed an enrolled control point\n{result.stderr}"


@pytest_skip_no_sh
@pytest.mark.parametrize("function", ["init_hook_timer", "log_hook_execution"])
def test_n11_a_lib_function_that_exits_is_still_inert_before_enrollment(tmp_path: Path, function: str) -> None:
    """Bystander control: the same tamper must not start blocking an opted-out project."""
    project = _hook_project(tmp_path, f"n11-inert-{function}", enroll=False)
    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.write_text(f"{lib.read_text(encoding='utf-8')}\n{function}() {{ exit 0; }}\n", encoding="utf-8")
    assert _run_hook(project, _POST_HOOK).returncode == 0


@pytest_skip_no_sh
def test_n11_the_untampered_allow_path_still_exits_zero(tmp_path: Path) -> None:
    """The control for the whole `_trw_exit_decided` restructure: making an
    undecided exit fail closed must not turn a DECIDED allow into a block."""
    project = _hook_project(tmp_path, "n11-allow")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert _run_hook(project, _PRE_HOOK).returncode == 0


def test_n12_a_hook_fired_from_a_subdirectory_still_judges_the_enrolled_root(tmp_path: Path) -> None:
    """The shell recognizer walks UP to the marker, but the Python control point
    resolved its root from the cwd, so a hook fired from a package subdirectory
    (a sub-agent that ``cd``-ed into it) read the marker relative to that
    subdirectory, called it "missing but tracked" and blocked every write as
    ``stale`` (observed on three agents, 2026-09-04). The recognized root now
    travels to Python as ``TRW_PROJECT_ROOT``; the allow path must stay allowed."""
    project = _hook_project(tmp_path, "n12-subdir")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    subdir = project / "pkg" / "src"
    subdir.mkdir(parents=True)
    result = _run_hook(project, _PRE_HOOK, cwd=subdir)
    assert result.returncode == 0, result.stderr
    assert "stale" not in result.stderr


# --- N4: the ledger must fail closed exactly as the marker does ---------------


def _open_violation(root: Path) -> None:
    record_violation(claim_id="C-1", file_path=PROTECTED, session_id="s", reason="falsifier failed", root=root)
    record_open_violation(root, claim_id="C-1", file_path=PROTECTED, claim_text="t", falsifier="f")
    assert intent_violation_gate_block(root) is not None


def test_n4_deleting_the_marker_and_the_ledger_still_blocks(tmp_path: Path) -> None:
    """`rm marker ledger` returned NO_BLOCK — deleting the evidence is not a fix."""
    _open_violation(tmp_path)
    (tmp_path / OPEN_VIOLATIONS_PATH).unlink()
    ledger_path(tmp_path).unlink()
    assert intent_violation_gate_block(tmp_path) is not None, "a deleted ledger must fail closed"


def test_n4_deleting_the_checkpoint_too_still_blocks(tmp_path: Path) -> None:
    """The co-located checkpoint is one durable signal; git history is the other."""
    repo = init_repo(tmp_path, "n4-git")
    _open_violation(repo)
    git(repo, "add", "-A", "-f")
    git(repo, "commit", "-q", "-m", "record the violation")

    (repo / OPEN_VIOLATIONS_PATH).unlink()
    ledger_path(repo).unlink()
    checkpoint_path(repo).unlink()
    assert intent_violation_gate_block(repo) is not None


def test_n4_corrupt_ledger_fails_closed_like_a_corrupt_marker(tmp_path: Path) -> None:
    """The asymmetry WAS the bug: a corrupt MARKER blocked, a corrupt LEDGER did not."""
    _open_violation(tmp_path)
    (tmp_path / OPEN_VIOLATIONS_PATH).unlink()
    ledger_path(tmp_path).write_text("{ not json\n", encoding="utf-8")
    assert intent_violation_gate_block(tmp_path) is not None


def test_n4_binary_garbage_ledger_fails_closed(tmp_path: Path) -> None:
    """Undecodable bytes are not an empty ledger — and must not crash the gate."""
    _open_violation(tmp_path)
    (tmp_path / OPEN_VIOLATIONS_PATH).unlink()
    ledger_path(tmp_path).write_bytes(b"\xff\xfe\x00garbage")
    assert intent_violation_gate_block(tmp_path) is not None


def test_n4_truncated_ledger_fails_closed(tmp_path: Path) -> None:
    """Tail truncation drops the violation entry; the checkpoint still knows."""
    _open_violation(tmp_path)
    (tmp_path / OPEN_VIOLATIONS_PATH).unlink()
    ledger_path(tmp_path).write_text("", encoding="utf-8")
    assert intent_violation_gate_block(tmp_path) is not None


def test_n4_inert_project_that_never_recorded_an_override_is_not_blocked(tmp_path: Path) -> None:
    """Absent-and-never-existed stays clean: no file, no checkpoint, no history."""
    assert ledger_tamper_reason(tmp_path) is None
    assert intent_violation_gate_block(tmp_path) is None


# --- N5: an override against untrustworthy state must REFUSE ------------------


def test_n5_override_is_refused_when_the_marker_is_corrupt(tmp_path: Path) -> None:
    """A corrupt marker yields no keys, and a no-keys override used to succeed —
    granting an override with NO ledger record at all."""
    record_open_violation(tmp_path, claim_id="C-1", file_path=PROTECTED, claim_text="t", falsifier="f")
    (tmp_path / OPEN_VIOLATIONS_PATH).write_text("{ corrupt", encoding="utf-8")

    assert record_gate_override(tmp_path, session_id="s", reason="just because") is False
    assert not ledger_path(tmp_path).exists(), "a refused override must leave no state behind"
    assert intent_violation_gate_block(tmp_path) is not None, "the block must be re-imposed"


def test_n5_override_is_refused_when_the_ledger_is_untrustworthy(tmp_path: Path) -> None:
    _open_violation(tmp_path)
    ledger_path(tmp_path).write_text("{ not json\n", encoding="utf-8")
    assert record_gate_override(tmp_path, session_id="s", reason="operator override") is False
    assert intent_violation_gate_block(tmp_path) is not None


def test_n5_override_still_succeeds_on_healthy_state(tmp_path: Path) -> None:
    """The refusals must not break the legitimate operator path."""
    _open_violation(tmp_path)
    assert record_gate_override(tmp_path, session_id="s", reason="operator override") is True
    assert intent_violation_gate_block(tmp_path) is None
    assert "C-1" in ledger_path(tmp_path).read_text(encoding="utf-8")


# --- N1 / N2: alias matching -------------------------------------------------


def _claims(anchor: str) -> object:
    return load_contract_bytes(contract_yaml(anchors=anchor).encode("utf-8"))


def test_n1_symlink_alias_to_an_anchored_file_is_enforced(tmp_path: Path) -> None:
    """A symlink has st_nlink == 1, so link-count-only detection never fired."""
    root = make_project(tmp_path, enroll=False)
    alias = root / "sym-alias.py"
    alias.symlink_to(root / PROTECTED)

    contract = _claims(PROTECTED)
    assert enforceable_claims(contract, "sym-alias.py", root, alias)  # type: ignore[arg-type]


def test_n2_hardlink_out_of_a_directory_anchor_is_enforced(tmp_path: Path) -> None:
    """A directory's inode never equals a file's, so the anchor must be WALKED."""
    root = make_project(tmp_path, enroll=False)
    alias = root / "hard-alias.py"
    os.link(root / PROTECTED, alias)

    contract = _claims("protected")
    assert enforceable_claims(contract, "protected/module.py", root, root / PROTECTED)  # type: ignore[arg-type]
    assert enforceable_claims(contract, "hard-alias.py", root, alias)  # type: ignore[arg-type]


def test_n1_symlink_out_of_a_directory_anchor_is_enforced(tmp_path: Path) -> None:
    """Both gaps compose: a symlink alias to a file inside a directory anchor."""
    root = make_project(tmp_path, enroll=False)
    alias = root / "sym-dir-alias.py"
    alias.symlink_to(root / PROTECTED)
    assert enforceable_claims(_claims("protected"), "sym-dir-alias.py", root, alias)  # type: ignore[arg-type]


def test_alias_matching_does_not_over_block_unrelated_files(tmp_path: Path) -> None:
    """Widened identity must not turn every alias in the repo into a protected one."""
    root = make_project(tmp_path, enroll=False)
    (root / "other.py").write_text("x\n", encoding="utf-8")
    sym = root / "other-sym.py"
    sym.symlink_to(root / "other.py")
    hard = root / "other-hard.py"
    os.link(root / "other.py", hard)

    for rel, target in (("other-sym.py", sym), ("other-hard.py", hard)):
        assert not enforceable_claims(_claims(PROTECTED), rel, root, target)  # type: ignore[arg-type]
        assert not enforceable_claims(_claims("protected"), rel, root, target)  # type: ignore[arg-type]


def test_dangling_symlink_alias_does_not_crash_matching(tmp_path: Path) -> None:
    """`stat` on a broken link raises; identity resolution must absorb that."""
    root = make_project(tmp_path, enroll=False)
    alias = root / "dangling.py"
    alias.symlink_to(root / "nope.py")
    assert not enforceable_claims(_claims(PROTECTED), "dangling.py", root, alias)  # type: ignore[arg-type]


# --- N7: the evidence C9 protects must be visible to C9 ----------------------


def test_n7_removing_an_evidence_visibility_rule_is_a_control_plane_finding() -> None:
    """`*.jsonl` hid the ledger AND the approvals file from every git-side check.

    The force-track negations are therefore control-plane state: dropping one
    re-hides the evidence without touching any file C9 already watched.
    """
    assert EVIDENCE_VISIBILITY_RULES, "the force-track rules must not be empty"
    ignored = "\n".join(("*.jsonl", *EVIDENCE_VISIBILITY_RULES)).encode("utf-8")

    def base(path: str) -> bytes | None:
        return ignored if path == EVIDENCE_VISIBILITY_PATH else None

    def stripped(path: str) -> bytes | None:
        return b"*.jsonl\n" if path == EVIDENCE_VISIBILITY_PATH else None

    assert control_plane_findings(base, base) == ()
    findings = control_plane_findings(base, stripped)
    for rule in EVIDENCE_VISIBILITY_RULES:
        assert any(rule in finding for finding in findings), f"{rule} removal went unreported: {findings}"


@pytest_skip_no_git
def test_n7_evidence_files_are_trackable_in_this_repo(tmp_path: Path) -> None:
    """The rules are only worth anything if git actually honours them.

    Asserted against a scratch repo carrying THIS repo's `.trw/.gitignore`, so the
    test proves the file's effect rather than its text.
    """
    source = Path(__file__).resolve().parents[2] / ".trw" / ".gitignore"
    if not source.exists():  # pragma: no cover — standalone checkouts of trw-mcp
        pytest.skip("monorepo .trw/.gitignore is not present in this checkout")

    repo = init_repo(tmp_path, "n7")
    (repo / ".trw" / "contracts").mkdir(parents=True)
    shutil.copy2(source, repo / ".trw" / ".gitignore")
    for rel in (".trw/contracts/intent-override-ledger.jsonl", ".trw/contracts/weaken-edit-approvals.jsonl"):
        (repo / rel).write_text("{}\n", encoding="utf-8")
        checked = subprocess.run(
            ["git", "check-ignore", "-q", rel], cwd=str(repo), check=False, shell=False, capture_output=True
        )
        assert checked.returncode != 0, f"{rel} is still hidden from git"

    # A control: the rules are narrow, not a blanket un-ignore of *.jsonl.
    (repo / ".trw" / "context").mkdir(parents=True)
    (repo / ".trw/context/session-events.jsonl").write_text("{}\n", encoding="utf-8")
    control = subprocess.run(
        ["git", "check-ignore", "-q", ".trw/context/session-events.jsonl"],
        cwd=str(repo),
        check=False,
        shell=False,
        capture_output=True,
    )
    assert control.returncode == 0, "unrelated runtime jsonl must stay ignored"


# --- upgrade hazard: a vendor hook resync must not brick enrolled projects ---


def _bundled_hook_bump(project: Path) -> None:
    """Simulate the vendor's new hook bytes ALREADY installed in the project."""
    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.write_text(lib.read_text(encoding="utf-8") + "\n# v-next: a new bundled hook ships\n", encoding="utf-8")


def _ship_new_hooks(project: Path) -> dict[str, list[str]]:
    """Drive the REAL `trw-mcp update-project` hook seam with new bundled bytes.

    Exercises `_update_hooks`, the function the update chain actually calls, with
    the manifest a real update carries — not just the re-bless helper it wires in.
    """
    from trw_mcp.bootstrap._template_updater import _update_hooks

    installed = project / ".claude" / "hooks"
    staged = project.parent / f"{project.name}-vendor-data"
    shutil.copytree(_HOOK_SRC, staged / "hooks")
    lib = staged / "hooks" / "lib-trw.sh"
    lib.write_text(lib.read_text(encoding="utf-8") + "\n# v-next: a new bundled hook ships\n", encoding="utf-8")

    manifest = {
        source.name: hashlib.sha256((installed / source.name).read_bytes()).hexdigest()
        for source in (staged / "hooks").glob("*.sh")
        if (installed / source.name).exists()
    }
    result: dict[str, list[str]] = {"created": [], "updated": [], "skipped": [], "modified": [], "warnings": []}
    _update_hooks(project, staged, result, dry_run=False, manifest_hashes=manifest)
    return result


def test_vendor_hook_resync_rebless_keeps_an_enrolled_project_working(tmp_path: Path) -> None:
    """Widening `expected_hook_digest` to cover lib-trw.sh created an upgrade hazard:
    the next shipped hook would make every enrolled marker read `stale`, failing
    both control points closed on users who did nothing wrong.

    Driven through the real update seam, so a future refactor that moves the
    re-bless out of the resync path fails here rather than in the field.
    """
    project = _hook_project(tmp_path, "upgrade")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"

    result = _ship_new_hooks(project)
    assert any(path.endswith("lib-trw.sh") for path in result["updated"]), "the update must actually land"
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert any("enrollment.yaml" in path for path in result["updated"])


def test_the_upgrade_hazard_is_real_without_the_rebless(tmp_path: Path) -> None:
    """The negative control: new bundled hook bytes alone DO brick the marker.

    Without this, the test above could pass for the wrong reason (e.g. the digest
    quietly stopped covering the hook files at all).
    """
    project = _hook_project(tmp_path, "upgrade-hazard")
    _bundled_hook_bump(project)
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"


@pytest_skip_no_sh
def test_hooks_still_enforce_after_a_vendor_resync_rebless(tmp_path: Path) -> None:
    """The re-bless must restore ENFORCEMENT, not merely a green status string."""
    project = _hook_project(tmp_path, "upgrade-enforce")
    _ship_new_hooks(project)
    assert check_enrollment_status(project, CONTRACT_REL) == "current"

    # The pre-write hook allows an anchored path by design (FR05 step 8); the
    # post-edit hook must still fail closed on its unevaluable falsifier.
    assert _run_hook(project, _PRE_HOOK).returncode == 0
    assert _run_hook(project, _POST_HOOK).returncode == 2

    (project / ".claude" / "hooks" / "lib-trw.sh").chmod(0o000)
    assert _run_hook(project, _PRE_HOOK).returncode == 2, "N6 must stay closed after a re-bless"


def test_rebless_does_not_launder_a_contract_edit(tmp_path: Path) -> None:
    """The contract is the user-controlled, tamper-sensitive half — it must keep
    failing closed until an operator re-enrolls."""
    project = _hook_project(tmp_path, "upgrade-contract")
    (project / CONTRACT_REL).write_text(contract_yaml(anchors="something/else.py"), encoding="utf-8")
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"

    _ship_new_hooks(project)
    assert check_enrollment_status(project, CONTRACT_REL) == "stale", "a contract edit must survive the re-bless"


def test_rebless_never_enrolls_a_project_that_never_opted_in(tmp_path: Path) -> None:
    """Inert-by-default: the installer must not enroll anyone on their behalf."""
    from trw_mcp.security.intent_contract.enrollment import enrollment_path, refresh_hook_digest

    project = _hook_project(tmp_path, "upgrade-inert", enroll=False)
    assert refresh_hook_digest(project) is False
    _ship_new_hooks(project)
    assert not enrollment_path(project).exists()
    assert check_enrollment_status(project, CONTRACT_REL) == "never_enrolled"


def test_rebless_refuses_a_corrupt_marker(tmp_path: Path) -> None:
    """It must never launder an unparsable marker into a valid one."""
    from trw_mcp.security.intent_contract.enrollment import enrollment_path, refresh_hook_digest

    project = _hook_project(tmp_path, "upgrade-corrupt")
    enrollment_path(project).write_text("{[not yaml", encoding="utf-8")
    assert refresh_hook_digest(project) is False
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"

    enrollment_path(project).write_text("schema_version: 99\n", encoding="utf-8")
    assert refresh_hook_digest(project) is False
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"


def test_hook_file_modes_are_unchanged_by_this_suite() -> None:
    """Guard for the chmod-based cases above: they must not leak to the sources."""
    for name in (_PRE_HOOK, _POST_HOOK, "lib-trw.sh"):
        mode = (_HOOK_SRC / name).stat().st_mode
        assert mode & stat.S_IRUSR, f"{name} lost its read bit"


def test_a_freshly_installed_project_can_see_its_own_override_evidence(tmp_path: Path) -> None:
    """N7 for USER projects, not just this monorepo.

    The monorepo's own `.trw/.gitignore` was fixed when N7 was found, but new
    installs are provisioned from the bundled template — which still carried the
    unqualified `*.jsonl` rule, so every fresh project re-hid its override ledger
    and approval records from the very C9 predicate meant to catch tampering with
    them. Asserting through `git check-ignore` (not a substring match on the
    template) is what makes this test about the deployed behavior.
    """
    import subprocess
    from pathlib import Path as _Path

    template = _Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "gitignore.txt"
    project = tmp_path / "fresh"
    (project / ".trw" / "contracts").mkdir(parents=True)
    (project / ".trw" / ".gitignore").write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    for evidence in ("intent-override-ledger.jsonl", "weaken-edit-approvals.jsonl"):
        rel = f".trw/contracts/{evidence}"
        (project / rel).write_text("{}\n", encoding="utf-8")
        ignored = subprocess.run(["git", "check-ignore", "-q", rel], cwd=project, check=False).returncode == 0
        assert not ignored, f"{rel} is git-ignored in a fresh install — C9 cannot see tampering with it"

    # …while ordinary .jsonl runtime chatter stays ignored, so the negation is narrow.
    (project / ".trw" / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert (
        subprocess.run(["git", "check-ignore", "-q", ".trw/events.jsonl"], cwd=project, check=False).returncode == 0
    ), "the fix must not un-ignore unrelated runtime jsonl"
