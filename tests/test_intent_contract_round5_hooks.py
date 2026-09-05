"""PRD-SEC-013 review round 5 (2026-07-25) — the SHELL half of seven bypasses.

Three findings, all reproduced against the real shipped hooks before a line moved:

* **F-A** — round 4 stopped the shared lib writing the hook's variables, but left
  it running SYNCHRONOUSLY in the foreground at two sites with no wall-clock
  bound on either. ``trap '' TERM HUP INT QUIT`` plus ``sleep 120``, appended to
  ``lib-trw.sh``, meant an enrolled, violating project could never reach ``exit
  2`` no matter how the client killed it — and the telemetry site was worse in
  kind, because it stranded a BLOCK the hook had already decided.
* **F-C** — the EXIT trap was installed while ``_trw_enrolled=0``, the signal
  trap after it, and enrollment recognition after both. A SIGTERM in that window
  produced a silent ``exit 0`` from an enrolled, violating project: 12/12 trials
  at each of 0.2/0.4/0.6/0.8 ms. The existing F4 test signals at 400 ms, so the
  window was entirely untested.
* **F-G** — ``_trw_root`` came from ``git rev-parse --show-toplevel`` whenever
  ``CLAUDE_PROJECT_DIR`` was unset, so a stub printing an empty directory sent
  both ``[ -f ]`` tests to the wrong tree and the hook went inert.

Every fail-closed test here is paired with a bystander: a hook that starts
blocking a project nobody opted into is not a fix, it is the round-4 regression
again.
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
    INTENT_LIB,
    POST_HOOK,
    PRE_HOOK,
    contract_yaml,
    hook_project,
    poison_lib,
    pytest_skip_no_sh,
    run_hook,
)
from trw_mcp.security.intent_contract.enrollment import check_enrollment_status

_HOOK_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"

#: A lib that parses, sources, does not abort, and REFUSES TO RETURN — and that
#: ignores the signal a client uses to enforce its own hook timeout.
_UNKILLABLE_LIB = "\ntrap '' TERM HUP INT QUIT\nsleep 120\n"

#: The same, but hidden inside the one function the hook still calls, which runs
#: AFTER the block has been decided and BEFORE the process can carry it out.
_UNKILLABLE_TELEMETRY = "\nlog_hook_execution() { trap '' TERM HUP INT QUIT; sleep 120; }\n"


def _blocking_post_project(tmp_path: Path, name: str) -> Path:
    """Enrolled, with a falsifier that cannot pass -> the baseline exit is 2."""
    project = hook_project(tmp_path, name)
    assert check_enrollment_status(project, CONTRACT_REL) == "current"
    assert run_hook(project, POST_HOOK).returncode == 2, "the baseline must actually enforce"
    return project


def _blocking_pre_project(tmp_path: Path, name: str) -> Path:
    """Staleness is the one condition that makes the METADATA-ONLY guard block."""
    project = hook_project(tmp_path, name)
    (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    assert check_enrollment_status(project, CONTRACT_REL) == "stale"
    assert run_hook(project, PRE_HOOK).returncode == 2, "the baseline must actually enforce"
    return project


def _blocking_project(tmp_path: Path, name: str, hook: str) -> Path:
    return _blocking_pre_project(tmp_path, name) if hook == PRE_HOOK else _blocking_post_project(tmp_path, name)


# --- F-A: no lib invocation may sit between the decision and the exit ---------


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
@pytest.mark.parametrize(
    ("label", "body"), [("probe-site", _UNKILLABLE_LIB), ("telemetry-site", _UNKILLABLE_TELEMETRY)]
)
def test_fa_a_lib_that_refuses_to_return_cannot_stop_the_hook_blocking(
    tmp_path: Path, hook: str, label: str, body: str
) -> None:
    """The hook must reach 2 ON ITS OWN, well inside the client's hook timeout.

    Timing is the assertion, not decoration: pre-fix this hung for the lib's full
    120 s, which no registered timeout (3 s pre-write, 10 s post-edit) survives —
    the client kills it and reports 143, and only 2 blocks.
    """
    project = _blocking_project(tmp_path, f"fa-{label}-{hook}", hook)
    poison_lib(project, body)

    started = time.monotonic()
    try:
        result = run_hook(project, hook, timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - this IS the pre-fix behaviour
        pytest.fail(f"a tampered lib held the {hook} decision open past 30s")
    elapsed = time.monotonic() - started

    assert result.returncode == 2, f"a lib that never returns disarmed an enrolled control\n{result.stderr}"
    assert elapsed < 20, f"the decision took {elapsed:.1f}s — the lib is still on the foreground path"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
@pytest.mark.parametrize(
    ("label", "body"), [("probe-site", _UNKILLABLE_LIB), ("telemetry-site", _UNKILLABLE_TELEMETRY)]
)
def test_fa_the_same_lib_stays_inert_before_enrollment(tmp_path: Path, hook: str, label: str, body: str) -> None:
    """Bystander: an opted-out project must neither block nor hang."""
    project = hook_project(tmp_path, f"fa-inert-{label}-{hook}", enroll=False)
    poison_lib(project, body)
    try:
        result = run_hook(project, hook, timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - pre-fix behaviour
        pytest.fail(f"a tampered lib hung the {hook} in a project that never opted in")
    assert result.returncode == 0


#: The files that make up the deciding shell. PRD-CORE-250-FR05 moved the shared
#: routines out of the two hooks and into the library both of them source, so a
#: structural property of "the deciding shell" now has to be read across all
#: three — scanning only the hooks would silently stop measuring anything.
_CONTROL_POINT_FILES = (PRE_HOOK, POST_HOOK, INTENT_LIB)


def _control_point_source() -> str:
    return "\n".join((_HOOK_SRC / name).read_text(encoding="utf-8") for name in _CONTROL_POINT_FILES)


def test_fa_the_only_lib_invocation_left_is_detached() -> None:
    """Companion to the paren-depth scan, which passes while this is broken.

    ``_subshell_depths`` proves the lib cannot WRITE the deciding shell. It says
    nothing about whether the deciding shell WAITS for it, which is the whole of
    F-A. Two structural facts carry that: the lib appears exactly once outside
    comments across the whole control point, and the group containing it is
    backgrounded.
    """
    source = _control_point_source()
    live = [
        line
        for line in source.splitlines()
        if '. "$_hook_dir/lib-trw.sh"' in line and not line.lstrip().startswith("#")
    ]
    assert len(live) == 1, f"the control point invokes the shared lib {len(live)} times outside comments: {live}"

    body = source.split("_trw_telemetry() {", 1)[1].split("\n}", 1)[0]
    assert '. "$_hook_dir/lib-trw.sh"' in body, "the one invocation must be the telemetry one"
    closing = [line for line in body.splitlines() if line.strip().startswith(")")]
    assert closing and closing[-1].rstrip().endswith("&"), (
        f"the control point runs the shared lib in a FOREGROUND subshell: {closing[-1] if closing else '<no group>'}"
    )
    assert "</dev/null" in closing[-1], "a detached job must not inherit the client's stdin"


# --- F-C: the pre-trap window, and the decide-ordering it exposed -------------


def _sigterm_at(project: Path, hook: str, delay_seconds: float) -> int:
    """Spawn the real hook and SIGTERM only the PID we just created."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["TRW_INTENT_PRE_WRITE_BUDGET_SECONDS"] = "60"
    env["TRW_INTENT_POST_EDIT_BUDGET_SECONDS"] = "120"
    process = subprocess.Popen(
        ["sh", str(project / ".claude" / "hooks" / hook)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=project,
        env=env,
        start_new_session=True,
        shell=False,
    )
    try:
        assert process.stdin is not None
        process.stdin.write('{"tool_name": "Edit", "tool_input": {"file_path": "protected/module.py"}}')
        process.stdin.close()
        if delay_seconds:
            time.sleep(delay_seconds)
        try:
            os.kill(process.pid, signal.SIGTERM)
        except ProcessLookupError:  # pragma: no cover - the hook already finished
            pass
        return process.wait(timeout=60)
    finally:
        if process.poll() is None:  # pragma: no cover - defensive
            process.kill()
            process.wait(timeout=30)


#: The band the probe measured as a silent allow, plus the 1 ms edge where it was
#: mixed. 0 ms is deliberately EXCLUDED and covered by its own test below.
_WINDOW_MS = [0.2, 0.4, 0.6, 0.8, 1.0]


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fc_a_sigterm_inside_the_preamble_never_silently_allows(tmp_path: Path, hook: str) -> None:
    """Sub-millisecond SIGTERMs must never produce exit 0 from an enrolled control.

    ``exit 0`` is the only outcome asserted against, because it is the only one
    that is a *silent* allow: the trap ran, decided nothing was wrong, and said
    so. 143 in the same window is the untrapped default action and is covered by
    the test below.
    """
    project = _blocking_project(tmp_path, f"fc-{hook}", hook)
    observed = [_sigterm_at(project, hook, milliseconds / 1000.0) for milliseconds in _WINDOW_MS for _ in range(4)]

    assert 0 not in observed, f"a SIGTERM in the preamble converted a block into a silent allow: {observed}"
    assert 2 in observed, "the sweep never reached the armed path — it would pass vacuously"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fc_the_same_sweep_stays_inert_before_enrollment(tmp_path: Path, hook: str) -> None:
    """Bystander: the window fix must not start blocking an opted-out project."""
    project = hook_project(tmp_path, f"fc-inert-{hook}", enroll=False)
    observed = [_sigterm_at(project, hook, milliseconds / 1000.0) for milliseconds in _WINDOW_MS for _ in range(4)]
    assert 2 not in observed, f"an opted-out project was blocked by a signal: {observed}"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fc_the_residual_before_any_trap_is_a_kill_not_a_decision(tmp_path: Path, hook: str) -> None:
    """State the one window that is NOT closed, so nobody reads silence as cover.

    A signal delivered before the shell has executed its first builtin cannot be
    trapped, so the process dies of SIGTERM (-15 / 143). That is not a block, and
    it is not the fail-open trap deciding to allow either — it is the client
    killing a hook microseconds after spawning it. Pinned so the distinction
    survives, and so a regression that reintroduces a DECIDED 0 here is visible.
    """
    project = _blocking_project(tmp_path, f"fc-zero-{hook}", hook)
    observed = {_sigterm_at(project, hook, 0.0) for _ in range(4)}
    assert observed <= {-signal.SIGTERM, 2}, (
        f"an immediate SIGTERM produced something other than a kill or a block: {observed}"
    )


def test_fc_decide_publishes_the_code_before_the_flag() -> None:
    """Latent half of F-C, pinned structurally because it is unreachable by timing.

    ``_trw_decide`` set ``_trw_exit_decided=1`` BEFORE ``_trw_decided_code``, so a
    signal landing between the two lines read "decided" next to the initial 0 and
    exited 0 — a block converted into an allow. The probe measured 0 hits in 150
    timed runs at shipped speed, which is exactly why a behavioural test would be
    vacuous and the source order is the thing worth pinning.

    The function moved into the shared library (PRD-CORE-250-FR05), so the scan
    covers the whole control point; the split below fails loudly if it moves
    again rather than passing on an empty body.
    """
    source = _control_point_source()
    assert source.count("_trw_decide() {") == 1, "the decide routine must exist exactly once in the control point"
    body = source.split("_trw_decide() {", 1)[1].split("}", 1)[0]
    assert body.index("_trw_decided_code=") < body.index("_trw_exit_decided=1"), (
        f"the control point publishes the decided flag before the code it carries:{body}"
    )


def test_every_inline_decision_assignment_publishes_the_code_first() -> None:
    """The same ordering rule where it is written OUT rather than called.

    The hooks' pre-library bootstrap cannot call ``_trw_decide`` — the function
    does not exist yet — so it assigns the pair directly. That is the same latent
    F-C shape and needs the same guarantee, which a scan for the function body
    alone would not give.

    Adjacency is the assertion, not order-of-appearance: a scan that only asked
    "did some `_trw_decided_code=` line come earlier in the file" is satisfied by
    the initialiser at the top and passes with the two lines swapped (measured).
    """
    for name in (PRE_HOOK, POST_HOOK):
        numbered = [
            (number, line.strip())
            for number, line in enumerate((_HOOK_SRC / name).read_text(encoding="utf-8").splitlines(), start=1)
            if line.strip().startswith(("_trw_decided_code=", "_trw_exit_decided="))
        ]
        publishes = [(number, text) for number, text in numbered if text == "_trw_exit_decided=1"]
        assert publishes, f"{name} has no inline decision assignment — the scan has rotted"
        by_line = dict(numbered)
        for number, _text in publishes:
            previous = by_line.get(number - 1, "")
            assert previous.startswith("_trw_decided_code="), (
                f"{name}:{number} publishes _trw_exit_decided=1 without assigning the code on the line before it "
                f"(found {previous!r})"
            )


# --- F-G: git must not be able to move the root out from under recognition ----


@pytest.fixture
def toplevel_spoof(tmp_path: Path) -> Path:
    """A ``git`` whose ``--show-toplevel`` names an empty directory elsewhere."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / ".trw").mkdir(parents=True)
    stub_dir = tmp_path / "spoofbin"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text(
        f'#!/bin/sh\nif [ "$1" = "rev-parse" ]; then printf "%s\\n" "{elsewhere}"; exit 0; fi\nexit 1\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub_dir


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fg_a_git_stub_that_lies_about_the_toplevel_cannot_disarm_the_hook(
    tmp_path: Path, hook: str, toplevel_spoof: Path
) -> None:
    """``run_hook`` unsets CLAUDE_PROJECT_DIR, which is exactly the reachable case."""
    project = _blocking_project(tmp_path, f"fg-{hook}", hook)
    result = run_hook(project, hook, extra_env={"PATH": f"{toplevel_spoof}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 2, f"a spoofed --show-toplevel disarmed an enrolled control\n{result.stderr}"


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fg_the_same_spoof_stays_inert_before_enrollment(tmp_path: Path, hook: str, toplevel_spoof: Path) -> None:
    """Bystander: walking extra candidate roots must not arm an opted-out project."""
    project = hook_project(tmp_path, f"fg-inert-{hook}", enroll=False)
    result = run_hook(project, hook, extra_env={"PATH": f"{toplevel_spoof}{os.pathsep}{os.environ['PATH']}"})
    assert result.returncode == 0, f"an opted-out project was armed by the root walk\n{result.stderr}"


#: Everything the hooks invoke BEFORE they look for python3. A PATH holding
#: exactly these isolates the shell preamble: the `command -v python3` branch is
#: decided in shell, so the verdict cannot be Python's answer in disguise.
_PRE_PYTHON_TOOLS = ("sh", "cat", "date", "dirname", "git")


@pytest.fixture
def path_without_python3(tmp_path: Path) -> str:
    import shutil

    bin_dir = tmp_path / "nopythonbin"
    bin_dir.mkdir()
    for tool in _PRE_PYTHON_TOOLS:
        located = shutil.which(tool)
        if located:
            (bin_dir / tool).symlink_to(located)
    assert shutil.which("python3", path=str(bin_dir)) is None
    return str(bin_dir)


def _run_from(project: Path, hook: str, cwd: Path, path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(project / ".claude" / "hooks" / hook)],
        input='{"tool_name": "Edit", "tool_input": {"file_path": "protected/module.py"}}',
        text=True,
        capture_output=True,
        cwd=cwd,
        env={"PATH": path, "HOME": os.environ.get("HOME", "/tmp")},
        timeout=60,
        check=False,
        shell=False,
    )


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fg_shell_recognition_reaches_a_marker_in_an_ancestor_directory(
    tmp_path: Path, hook: str, path_without_python3: str
) -> None:
    """The walk is what makes the spoof harmless, so prove it actually walks.

    Deliberately measured through the no-python3 branch: that branch is decided
    in SHELL, so a green result cannot be the Python layer answering instead
    (which is how the round-4 shell tests came to prove less than they claimed).
    Python's own root resolution does NOT follow this walk, which is why the
    end-to-end subdirectory case is not what is asserted here.
    """
    project = _blocking_project(tmp_path, f"fg-walk-{hook}", hook)
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)

    result = _run_from(project, hook, nested, path_without_python3)
    assert result.returncode == 2, f"shell recognition did not reach the marker from a subdirectory\n{result.stderr}"
    assert "no interpreter with trw_mcp installed" in result.stderr, (
        "the measurement did not go through the shell-only branch"
    )


@pytest_skip_no_sh
@pytest.mark.parametrize("hook", [PRE_HOOK, POST_HOOK])
def test_fg_the_ancestor_walk_does_not_arm_an_unenrolled_subdirectory(
    tmp_path: Path, hook: str, path_without_python3: str
) -> None:
    """Bystander for the walk, through the same shell-only branch."""
    project = hook_project(tmp_path, f"fg-walk-inert-{hook}", enroll=False)
    nested = project / "src" / "deep"
    nested.mkdir(parents=True)

    result = _run_from(project, hook, nested, path_without_python3)
    assert result.returncode == 0, f"the shell preamble blocked a bystander on its own\n{result.stderr}"
