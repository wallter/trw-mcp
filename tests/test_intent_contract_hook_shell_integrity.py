"""PRD-SEC-013 review round 3 (2026-07-25): the DECIDING shell's own integrity.

Two findings, both reproduced against the real shipped hooks before a line was
changed:

* **F1** — ``lib-trw.sh`` was sourced into the deciding shell AFTER
  ``_trw_enrolled`` was computed and BEFORE it was consumed. The previous round
  hardened against a lib that ABORTS (chmod 000, a syntax error, ``exit 0``, a
  function that exits); it did nothing about a lib that parses cleanly and simply
  WRITES the hook's variables or SHADOWS the command that runs the check. One
  appended line each — ``_trw_enrolled=0``, ``_trw_exit_decided=1``,
  ``timeout() { return 0; }`` — turned an enrolled control point into exit 0.
  There was no runtime integrity check to catch it: bundle-hashes.json is
  install-time, and the enrollment digest that DOES cover lib-trw.sh is read by
  Python that runs downstream of the exit the tampered lib caused.

* **F4** — both hook headers claimed every unexpected exit "including … signal"
  maps to 2. It did not: a signal-killed shell reports 128+signum (143 for TERM,
  137 for KILL) and only exit 2 blocks.

Every test here has a bystander counterpart. A hook that starts blocking an
unenrolled project is not a fix, it is a different bug.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests._intent_contract_hooks import (
    CONTRACT_REL,
    POST_HOOK,
    PRE_HOOK,
    contract_yaml,
    hook_project,
    poison_lib,
    pytest_skip_no_sh,
    run_hook,
)
from trw_mcp.security.intent_contract.enrollment import check_enrollment_status

#: Each of these parses cleanly, sources cleanly, and does NOT abort. That is the
#: whole point: the previous round's defence was "the lib must run to completion",
#: and all three do.
_QUIET_TAMPERS = [
    ("sets-enrolled-0", "\n_trw_enrolled=0\n"),
    ("presets-decided", "\n_trw_exit_decided=1\ninit_hook_timer() { exit 0; }\n"),
    ("shadows-timeout", "\ntimeout() { return 0; }\n"),
    ("shadows-decide", "\n_trw_decide() { _trw_exit_decided=1; _trw_decided_code=0; }\n"),
]


def _blocking_post_project(tmp_path: Path, name: str) -> Path:
    """An enrolled project whose post-edit falsifier cannot pass -> baseline is 2."""
    project = hook_project(tmp_path, name)
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert run_hook(project, POST_HOOK).returncode == 2, "the baseline must actually enforce"
    return project


def _blocking_pre_project(tmp_path: Path, name: str) -> Path:
    """Staleness is the one condition that makes the METADATA-ONLY pre-write guard block."""
    project = hook_project(tmp_path, name)
    (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"
    assert run_hook(project, PRE_HOOK).returncode == 2, "the baseline must actually enforce"
    return project


# --- F1: the lib must not be able to reach the decision ----------------------


@pytest_skip_no_sh
@pytest.mark.parametrize(("label", "body"), _QUIET_TAMPERS)
def test_f1_a_lib_that_writes_the_hooks_decision_state_cannot_disarm_post_edit(
    tmp_path: Path, label: str, body: str
) -> None:
    project = _blocking_post_project(tmp_path, f"f1-post-{label}")
    poison_lib(project, body)
    result = run_hook(project, POST_HOOK)
    assert result.returncode == 2, f"appending `{body.strip()}` disarmed an enrolled control point\n{result.stderr}"


@pytest_skip_no_sh
@pytest.mark.parametrize(("label", "body"), _QUIET_TAMPERS)
def test_f1_a_lib_that_writes_the_hooks_decision_state_cannot_disarm_pre_write(
    tmp_path: Path, label: str, body: str
) -> None:
    project = _blocking_pre_project(tmp_path, f"f1-pre-{label}")
    poison_lib(project, body)
    result = run_hook(project, PRE_HOOK)
    assert result.returncode == 2, f"appending `{body.strip()}` disarmed an enrolled control point\n{result.stderr}"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
@pytest.mark.parametrize(("label", "body"), _QUIET_TAMPERS)
def test_f1_the_same_tampers_stay_inert_before_enrollment(tmp_path: Path, hook: str, label: str, body: str) -> None:
    """Bystander control: none of these may start BLOCKING an opted-out project."""
    project = hook_project(tmp_path, f"f1-inert-{label}-{hook}", enroll=False)
    poison_lib(project, body)
    assert run_hook(project, hook).returncode == 0


_SOURCE_LIB = '. "$_hook_dir/lib-trw.sh"'


def _subshell_depths(source: str, needle: str) -> list[int]:
    """Paren-nesting depth at each occurrence of *needle*, quote- and comment-aware.

    Depth 0 means the statement runs in the shell that decides. ``$(`` counts as a
    subshell too, which is correct: a command substitution forks.
    """
    depths: list[int] = []
    depth = 0
    quote: str | None = None
    for line in source.splitlines():
        if line.lstrip().startswith("#"):
            continue
        index = 0
        while index < len(line):
            char = line[index]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "'\"":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif line.startswith(needle, index):
                depths.append(depth)
                index += len(needle)
                continue
            index += 1
    return depths


@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f1_the_hook_never_sources_the_shared_lib_into_its_own_shell(hook: str) -> None:
    """The structural property behind every case above, asserted directly.

    A lib that runs only in subshells cannot write a variable, define a function,
    or exit the shell that decides. Pinning the STRUCTURE as well as the symptoms
    is what stops a FIFTH symptom shipping: this fails the moment any
    `. lib-trw.sh` reappears at depth 0, which is exactly where the pre-fix
    `case ok:*)` branch put it.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / hook).read_text(
        encoding="utf-8"
    )
    depths = _subshell_depths(source, _SOURCE_LIB)
    assert depths, f"{hook} must still USE the lib — an empty result would make this vacuous"
    assert all(depth > 0 for depth in depths), f"{hook} sources lib-trw.sh at shell depth {depths}"


def test_the_subshell_depth_scanner_actually_distinguishes_the_two_shapes() -> None:
    """Non-vacuous control for the scanner itself: it must FAIL the pre-fix shape."""
    pre_fix = 'case "$p" in\n  ok:*)\n    ' + _SOURCE_LIB + " >/dev/null 2>&1 || true\n    ;;\nesac\n"
    assert _subshell_depths(pre_fix, _SOURCE_LIB) == [0]
    assert _subshell_depths("x=$( ( " + _SOURCE_LIB + " ) )\n", _SOURCE_LIB) == [2]
    assert _subshell_depths("# a comment mentioning " + _SOURCE_LIB + "\n", _SOURCE_LIB) == []


# --- F4: a catchable signal is an undecided exit ------------------------------


def _spawn(project: Path, hook: str) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["TRW_INTENT_PRE_WRITE_BUDGET_SECONDS"] = "60"
    env["TRW_INTENT_POST_EDIT_BUDGET_SECONDS"] = "120"
    return subprocess.Popen(
        ["sh", str(project / ".claude" / "hooks" / hook)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=project,
        env=env,
    )


def _sigterm_during_an_undecided_run(project: Path, hook: str) -> int:
    """SIGTERM the hook while it waits on stdin — i.e. before it has decided."""
    process = _spawn(project, hook)
    try:
        time.sleep(0.4)  # let the preamble finish and block on `cat`
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)
    finally:
        if process.poll() is None:  # pragma: no cover — defensive
            process.kill()
            process.communicate(timeout=30)
    return process.returncode


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f4_a_sigterm_before_the_decision_blocks_an_enrolled_control(tmp_path: Path, hook: str) -> None:
    """Measured before the fix: 143, which does not block. The header claimed 2."""
    project = hook_project(tmp_path, f"f4-{hook}")
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert _sigterm_during_an_undecided_run(project, hook) == 2


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f4_a_sigterm_stays_inert_before_enrollment(tmp_path: Path, hook: str) -> None:
    """Bystander control: killing a hook in an opted-out project must not block."""
    project = hook_project(tmp_path, f"f4-inert-{hook}", enroll=False)
    assert _sigterm_during_an_undecided_run(project, hook) == 0


@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_f4_the_header_does_not_claim_a_protection_that_cannot_exist(hook: str) -> None:
    """SIGKILL is untrappable, so a header promising "any signal" is false.

    The previous headers said every unexpected exit "including … signal" maps to
    2. This pins the corrected claim — catchable signals only, with the
    uncatchable case named — so the wording cannot silently drift back.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / hook).read_text(
        encoding="utf-8"
    )
    header = source.split("set -e", 1)[0]
    assert "CATCHABLE termination signal" in header
    assert "SIGKILL cannot" in header, "the header must name what it does NOT cover"
