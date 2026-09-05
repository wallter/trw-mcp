"""PRD-CORE-250-FR05 — the two intent hooks share one library and duplicate nothing.

Measured at HEAD on 2026-09-03 by ``difflib`` over the two files: 232 of 254/255
lines identical, with contiguous identical blocks of 106 and 85 lines and four
whole function bodies (``_trw_recognize_fs``, ``_trw_decide``, ``_trw_on_signal``,
``_trw_telemetry``) declared twice. Both hooks are registered in the shipped
``settings.json``, so a signal-trap or timeout fix applied to one and missed on
the other was a live divergence, not a style complaint.

The extraction is behavior-preserving; its proof is the 19-case exit-code and
stderr baseline captured before and after (see the FR05 entry in the PRD's
completion evidence) plus the unchanged
``test_intent_contract_hook_shell_integrity.py`` /
``test_intent_contract_hook_scripts.py`` suites. What these tests add is the
INVARIANT — the drift cannot come back, and the library obeys the discipline
``lib-ide-adapter.sh`` violated.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_HOOKS = Path(__file__).resolve().parents[2] / "src" / "trw_mcp" / "data" / "hooks"
_PRE = _HOOKS / "pre-tool-intent-guard.sh"
_POST = _HOOKS / "post-tool-intent-check.sh"
_LIB = _HOOKS / "lib-intent-guard.sh"

#: `name() {` at column 0, POSIX function-definition form.
_DEF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{\s*$")


def _function_bodies(path: Path) -> dict[str, str]:
    """Map function name -> normalized body, for shell functions at column 0.

    Bodies are collected to the first line that is exactly ``}`` at column 0,
    which is the shape every function in these files uses. Comments and blank
    lines are dropped so a re-worded comment cannot make two identical bodies
    look different — the point is the CODE.
    """
    bodies: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = _DEF.match(lines[index])
        if match is None:
            index += 1
            continue
        body: list[str] = []
        index += 1
        while index < len(lines) and lines[index] != "}":
            stripped = lines[index].strip()
            if stripped and not stripped.startswith("#"):
                body.append(stripped)
            index += 1
        bodies[match.group(1)] = "\n".join(body)
        index += 1
    return bodies


def test_no_duplicated_function_bodies() -> None:
    """The FR05 acceptance criterion: the intersection is empty."""
    pre, post = _function_bodies(_PRE), _function_bodies(_POST)
    shared_names = sorted(set(pre) & set(post))
    assert shared_names == [], f"function(s) declared in BOTH hooks: {shared_names}"

    shared_bodies = sorted(set(pre.values()) & set(post.values()))
    assert shared_bodies == [], f"{len(shared_bodies)} identical function body/bodies remain across the two hooks"


def test_the_extraction_is_not_vacuous() -> None:
    """Both hooks must still HAVE the routines — via the library, not a copy.

    Emptying both files would satisfy the assertion above, so this pins the other
    side: the four named routines exist exactly once, in the library, and both
    hooks reach them by sourcing it.
    """
    library = _function_bodies(_LIB)
    for name in ("_trw_recognize_fs", "_trw_decide", "_trw_on_signal", "_trw_telemetry"):
        assert name in library, f"{name} is not in the shared library"

    for hook in (_PRE, _POST):
        text = hook.read_text(encoding="utf-8")
        assert "lib-intent-guard.sh" in text, f"{hook.name} does not source the shared library"
        assert "_trw_guard_main" in text, f"{hook.name} does not run the shared control flow"


def _code_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_the_duplication_the_fr_measured_is_gone() -> None:
    """The headline metric: identical CODE lines shared by the two hooks, 232 -> 27.

    Not 0, and pretending otherwise would be the dishonest version of this test.
    What remains is the bootstrap preamble every hook must carry in its own file
    because it runs BEFORE the library exists in the shell: the fail-safe
    variable initialisation, the no-walk enrollment probe, the EXIT trap, and the
    guarded source. Those cannot live in the library they exist to survive the
    absence of.

    The ceiling is a ratchet: it may shrink, never grow. Anything a maintainer
    could fix in one file and forget in the other is a FUNCTION, and the
    intersection of function bodies is asserted to be empty above.
    """
    shared = set(_code_lines(_PRE)) & set(_code_lines(_POST))
    assert len(shared) <= 27, f"{len(shared)} identical code lines remain (was 232): {sorted(shared)}"


def test_the_shared_bootstrap_preamble_is_byte_identical() -> None:
    """The residual duplication cannot DRIFT, which is what US-003 is about.

    A shared block that the two files carry separately is only safe while the two
    copies stay the same. Once the event-specific nouns are normalized away, the
    bootstrap block must match byte for byte, so a fix applied to one half and
    forgotten on the other fails here instead of shipping.
    """

    def bootstrap(path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        start = text.index('_trw_hook_dir_probe="$0"')
        end = text.index("# --- FILESYSTEM enrollment recognition")
        block = text[start:end]
        return block.replace("must_not_happen falsifier", "must_not_happen <noun>").replace(
            "must_not_happen guard", "must_not_happen <noun>"
        )

    assert bootstrap(_PRE) == bootstrap(_POST), "the two hooks' bootstrap preambles have drifted apart"


def test_the_library_obeys_library_discipline() -> None:
    """The three rules lib-ide-adapter.sh broke, which is why FR04 deleted it.

    It declared ``#!/usr/bin/env bash`` and ``set -euo pipefail`` at top level
    while every bundled hook is invoked as ``sh <path>``, so sourcing it would
    impose ``errexit``/``nounset`` on the caller — directly contradicting its own
    header promise that "sourcing side-effects must not abort the calling
    script".
    """
    lines = _LIB.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"

    for number, line in enumerate(lines, start=1):
        if line.startswith((" ", "\t")) or line.strip().startswith("#"):
            continue
        assert not line.startswith("set -"), f"{_LIB.name}:{number} sets a shell option at top level: {line!r}"
        assert not line.startswith("exit"), f"{_LIB.name}:{number} exits outside a function: {line!r}"

    code = "\n".join(_code_lines(_LIB))
    assert "[[" not in code, "bashism: [[ ]] is not POSIX sh"
    assert not re.search(r"\blocal\s", code), "bashism: `local` is not POSIX sh"


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash unavailable")
@pytest.mark.parametrize("name", ["lib-intent-guard.sh", "pre-tool-intent-guard.sh", "post-tool-intent-check.sh"])
def test_posix_sh_parses_every_changed_file(name: str) -> None:
    """NFR04: a strict POSIX shell, not bash in sh-mode, must parse these."""
    result = subprocess.run(["dash", "-n", str(_HOOKS / name)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("dash") is None, reason="dash unavailable")
def test_sourcing_the_library_cannot_abort_or_alter_the_caller(tmp_path: Path) -> None:
    """The discipline rules above, proven by execution rather than by grep.

    A caller that has NOT set ``errexit`` must still not have it after sourcing,
    and a failing command after the source must not end the shell.
    """
    probe = tmp_path / "probe.sh"
    probe.write_text(
        f'. "{_LIB}"\ncase "$-" in *e*) echo ERREXIT_LEAKED ;; esac\n'
        f'case "$-" in *u*) echo NOUNSET_LEAKED ;; esac\nfalse\necho SURVIVED\n',
        encoding="utf-8",
    )
    result = subprocess.run(["dash", str(probe)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SURVIVED", result.stdout


def test_the_library_is_covered_by_the_enrollment_digest() -> None:
    """It is sourced into the DECIDING shell, so it is control-plane.

    ``chmod 000`` on a support file was a git-invisible total disarm once already
    (probe finding N6). Being in ``HOOK_SUPPORT_FILES`` is what makes a content
    or mode change to this file read as ``stale`` rather than as nothing.
    """
    from trw_mcp.security.intent_contract._control_plane import HOOK_SUPPORT_FILES

    assert "lib-intent-guard.sh" in HOOK_SUPPORT_FILES


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.parametrize("break_it", ["absent", "chmod-000", "syntax-error"])
@pytest.mark.parametrize("hook", ["pre-tool-intent-guard.sh", "post-tool-intent-check.sh"])
def test_an_unusable_library_fails_closed_when_enrolled_and_inert_when_not(
    tmp_path: Path, hook: str, break_it: str
) -> None:
    """The regression this extraction could have shipped, in both directions.

    Adding a sourced file to the deciding shell adds a way to disarm it. ``chmod
    000 .claude/hooks/lib-trw.sh`` was a git-invisible total disarm once already
    (probe finding N6), and a `.` of a missing or unparseable file ABORTS a
    non-interactive shell outright — measured: with the guard written as
    ``if ! . lib``, an UNENROLLED project exited 2, which is the opposite failure.

    So both directions are asserted. An enrolled project blocks; a project that
    never opted in stays silent. A test that only checked the first would pass a
    hook that blocked everybody.
    """
    from tests._intent_contract_hooks import hook_project, run_hook

    project = hook_project(tmp_path, f"{break_it}-{hook}", enroll=True)
    library = project / ".claude" / "hooks" / "lib-intent-guard.sh"
    if break_it == "absent":
        library.unlink()
    elif break_it == "chmod-000":
        library.chmod(0o000)
    else:
        library.write_text("if [ ; then\n", encoding="utf-8")

    try:
        blocked = run_hook(project, hook)
    finally:
        if library.exists():
            library.chmod(0o644)
    assert blocked.returncode == 2, f"an enrolled control point was disarmed by a {break_it} library: {blocked.stderr}"

    bystander = hook_project(tmp_path, f"bystander-{break_it}-{hook}", enroll=False)
    library = bystander / ".claude" / "hooks" / "lib-intent-guard.sh"
    if break_it == "absent":
        library.unlink()
    elif break_it == "chmod-000":
        library.chmod(0o000)
    else:
        library.write_text("if [ ; then\n", encoding="utf-8")

    try:
        result = run_hook(bystander, hook)
    finally:
        if library.exists():
            library.chmod(0o644)
    assert result.returncode == 0, f"a {break_it} library started blocking a project that never opted in"


# ---------------------------------------------------------------------------
# PRD-CORE-254 — the glob-sidecar fast path
#
# Every test here drives a REAL shipped hook as an ``sh`` subprocess against a
# REAL enrollment, and counts PYTHON SPAWNS rather than asserting on a log line:
# the claim under test is "no interpreter was started", and only the interpreter
# itself can testify to that. ``TRW_PYTHON`` is the resolver's first candidate,
# so a stub there sees every spawn the hook makes and then ``exec``s the real
# interpreter — the deferred cases keep their true exit codes instead of being
# replaced by a fake.
# ---------------------------------------------------------------------------

_FAST_PATH_ALLOWED = "0:allowed-fast-path"


def _counting_interpreter(tmp_path: Path) -> tuple[Path, Path]:
    """A ``TRW_PYTHON`` shim that records each spawn and then runs the real one."""
    import sys

    log = tmp_path / "python-spawns.log"
    stub = tmp_path / "counting-python.sh"
    stub.write_text(
        f'#!/bin/sh\nprintf "spawn\\n" >> "{log}"\nexec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub, log


def _spawns(log: Path) -> int:
    return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


def _fast_path_project(tmp_path: Path, name: str) -> Path:
    """An enrolled project with the real hooks, a fresh sidecar, and a spare file."""
    from tests._intent_contract_hooks import hook_project

    project = hook_project(tmp_path, name, enroll=True)
    (project / "unrelated").mkdir(exist_ok=True)
    (project / "unrelated" / "notes.py").write_text("x = 1\n", encoding="utf-8")
    return project


def _hook_log(project: Path) -> str:
    log = project / ".trw" / "context" / "hook-executions.log"
    for _ in range(50):  # telemetry is DETACHED — give the background subshell a moment
        if log.exists() and log.read_text(encoding="utf-8").strip():
            return log.read_text(encoding="utf-8")
        time.sleep(0.02)
    return ""


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
def test_fast_path_skips_python_on_no_match(tmp_path: Path) -> None:
    """FR02: a non-anchored path exits 0 with ZERO interpreters started."""
    from tests._intent_contract_hooks import run_hook

    project = _fast_path_project(tmp_path, "fast-pre")
    stub, log = _counting_interpreter(tmp_path)

    result = run_hook(
        project,
        "pre-tool-intent-guard.sh",
        extra_env={"TRW_PYTHON": str(stub)},
        tool_input={"file_path": "unrelated/notes.py", "old_string": 'a\\nb "q"', "new_string": "c"},
    )

    assert result.returncode == 0, result.stderr
    assert _spawns(log) == 0, "the fast path still spawned an interpreter"
    assert _FAST_PATH_ALLOWED in _hook_log(project)


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
def test_post_edit_fast_path_independent_of_pre_write(tmp_path: Path) -> None:
    """FR03: the post-edit hook decides for itself, with no pre-write call at all.

    Deliberately the ONLY hook invocation in this test process: the answer comes
    from the post-edit payload's own path against the same static anchor set, so
    no state written by the pre-write leg is involved (there is none to write).
    """
    from tests._intent_contract_hooks import run_hook

    project = _fast_path_project(tmp_path, "fast-post")
    stub, log = _counting_interpreter(tmp_path)

    result = run_hook(
        project,
        "post-tool-intent-check.sh",
        extra_env={"TRW_PYTHON": str(stub)},
        tool_input={"file_path": "unrelated/notes.py"},
    )

    assert result.returncode == 0, result.stderr
    assert _spawns(log) == 0, "the post-edit fast path still spawned an interpreter"
    assert _FAST_PATH_ALLOWED in _hook_log(project)


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
@pytest.mark.parametrize("hook", ["pre-tool-intent-guard.sh", "post-tool-intent-check.sh"])
def test_an_anchored_path_still_reaches_python(tmp_path: Path, hook: str) -> None:
    """FR02/NFR02: the matching path is untouched — exactly one Python invocation.

    Without this, every assertion above is satisfied by a hook that allows
    everything.
    """
    from tests._intent_contract_hooks import PROTECTED, run_hook

    project = _fast_path_project(tmp_path, f"anchored-{hook}")
    stub, log = _counting_interpreter(tmp_path)

    run_hook(project, hook, extra_env={"TRW_PYTHON": str(stub)}, tool_input={"file_path": PROTECTED})

    assert _spawns(log) == 1, "an anchored path must still be decided by the Python entry point"


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
@pytest.mark.parametrize("shape", ["dotdot", "absolute-outside", "symlink", "hardlink"])
def test_fast_path_defers_on_dotdot_and_symlink_paths(tmp_path: Path, shape: str) -> None:
    """FR04: an ambiguous path SHAPE is never decided by the shell.

    ``hardlink`` is here because ``_anchors.py`` falls back to ``(st_dev,
    st_ino)`` identity whenever the target is multi-linked, so a second name for
    an anchored file matches a claim even though its own path matches no anchor
    (probe finding N2). A fast path that answered it from the glob set alone
    would re-open that bypass.
    """
    from tests._intent_contract_hooks import PROTECTED, run_hook

    project = _fast_path_project(tmp_path, f"defer-{shape}")
    stub, log = _counting_interpreter(tmp_path)

    if shape == "dotdot":
        raw = "unrelated/../unrelated/notes.py"
    elif shape == "absolute-outside":
        raw = str(tmp_path / "elsewhere.py")
    elif shape == "symlink":
        (project / "unrelated" / "link.py").symlink_to(project / PROTECTED)
        raw = "unrelated/link.py"
    else:
        os.link(project / PROTECTED, project / "unrelated" / "alias.py")
        raw = "unrelated/alias.py"

    run_hook(project, "pre-tool-intent-guard.sh", extra_env={"TRW_PYTHON": str(stub)}, tool_input={"file_path": raw})

    assert _spawns(log) == 1, f"the {shape} shape was decided by the fast path instead of deferring"


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
@pytest.mark.parametrize(
    ("label", "tool", "tool_input"),
    [
        ("space", "Edit", {"file_path": "unrelated/my notes.py"}),
        ("quote", "Edit", {"file_path": 'unrelated/no"tes.py'}),
        ("backslash", "Edit", {"file_path": "unrelated\\notes.py"}),
        ("newline", "Edit", {"file_path": "unrelated/no\ntes.py"}),
        ("non-ascii", "Edit", {"file_path": "unrelated/notés.py"}),
        ("empty", "Edit", {"file_path": ""}),
        ("absent", "Edit", {"content": "no path at all"}),
        ("non-string", "Edit", {"file_path": ["unrelated/notes.py"]}),
        ("multi-edit", "MultiEdit", {"file_path": "unrelated/notes.py", "edits": [{"old_string": "a"}]}),
        ("notebook-edit", "NotebookEdit", {"notebook_path": "unrelated/nb.ipynb"}),
    ],
)
def test_fast_path_defers_on_every_unsafe_extraction_shape(
    tmp_path: Path, label: str, tool: str, tool_input: dict[str, object]
) -> None:
    """FR02 extraction gate: only a clean scalar path is eligible for a fast ALLOW.

    Each of these resolves to a path that is NOT anchored, so a fast path that
    accepted it would exit 0 with zero spawns; the assertion is that Python ran
    anyway.
    """
    from tests._intent_contract_hooks import run_hook

    project = _fast_path_project(tmp_path, f"unsafe-{label}")
    stub, log = _counting_interpreter(tmp_path)

    run_hook(
        project,
        "pre-tool-intent-guard.sh",
        extra_env={"TRW_PYTHON": str(stub)},
        tool=tool,
        tool_input=tool_input,
    )

    assert _spawns(log) == 1, f"the {label} payload was decided by the fast path instead of deferring"


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
def test_fast_path_defers_when_jq_is_absent(tmp_path: Path) -> None:
    """FR02: no jq, no fast path — there is deliberately no second parser.

    The PATH is rebuilt from scratch with everything the hook needs and nothing
    else, and the guard below proves the harness is non-vacuous: a stub PATH that
    accidentally still resolved ``jq`` would make this test pass for the wrong
    reason.
    """
    from tests._intent_contract_hooks import run_hook

    project = _fast_path_project(tmp_path, "no-jq")
    stub, log = _counting_interpreter(tmp_path)
    bindir = tmp_path / "nojq-bin"
    bindir.mkdir()
    for tool in ("sh", "cat", "date", "dirname", "ls", "git", "timeout", "mkdir", "tail", "mv", "rm", "wc"):
        found = shutil.which(tool)
        if found:
            (bindir / tool).symlink_to(found)
    assert shutil.which("jq", path=str(bindir)) is None, "the no-jq harness still resolves jq"

    result = run_hook(
        project,
        "pre-tool-intent-guard.sh",
        extra_env={"TRW_PYTHON": str(stub), "PATH": str(bindir)},
        tool_input={"file_path": "unrelated/notes.py"},
    )

    assert result.returncode == 0, result.stderr
    assert _spawns(log) == 1, "a jq-less shell took a fast-path shortcut it cannot justify"


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
@pytest.mark.parametrize(
    "tamper",
    [
        "missing",
        "empty",
        "digest-mismatch",
        "malformed-line",
        "appended-after-digest",
        "patterns-emptied",
        "guarded-artifact-newer",
        "guarded-artifact-deleted",
        "guarded-absent-artifact-appeared",
        "contract-edited-without-reenrol",
    ],
)
def test_tampered_sidecar_never_self_allows(tmp_path: Path, tamper: str) -> None:
    """NFR03/FR05: every damaged, stale or forged sidecar defers to Python.

    ``patterns-emptied`` and ``contract-edited-without-reenrol`` are the two that
    matter most. The first strips the anchor lines while leaving the digest line
    correct — the shape a naive digest-only binding would accept. The second adds
    a NEW anchor to the contract without re-enrolling: the marker goes stale, so
    Python BLOCKS every write, and a fast path that trusted its own unchanged
    digest line would have converted that block into an allow.
    """
    from tests._intent_contract_hooks import CONTRACT_REL, run_hook
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path

    project = _fast_path_project(tmp_path, f"tamper-{tamper}")
    stub, log = _counting_interpreter(tmp_path)
    sidecar = glob_sidecar_path(project)
    lines = sidecar.read_text(encoding="utf-8").splitlines()

    if tamper == "missing":
        sidecar.unlink()
    elif tamper == "empty":
        sidecar.write_text("", encoding="utf-8")
    elif tamper == "digest-mismatch":
        lines[-1] = "sha256:" + "0" * 64
        sidecar.write_text("\n".join(lines) + "\n", encoding="utf-8")
    elif tamper == "malformed-line":
        sidecar.write_text("\n".join([*lines[:-1], "protected/*", lines[-1]]) + "\n", encoding="utf-8")
    elif tamper == "appended-after-digest":
        sidecar.write_text("\n".join([*lines, "p unrelated/*"]) + "\n", encoding="utf-8")
    elif tamper == "patterns-emptied":
        kept = [line for line in lines if not line.startswith("p ")]
        sidecar.write_text("\n".join(kept) + "\n", encoding="utf-8")
    elif tamper == "guarded-artifact-newer":
        time.sleep(0.02)
        (project / ".claude" / "hooks" / "lib-trw.sh").touch()
    elif tamper == "guarded-artifact-deleted":
        (project / ".claude" / "hooks" / "lib-trw.sh").unlink()
    elif tamper == "guarded-absent-artifact-appeared":
        (project / ".pre-commit-config.yaml").write_text("repos: []\n", encoding="utf-8")
    else:
        (project / CONTRACT_REL).write_text(
            (project / CONTRACT_REL)
            .read_text(encoding="utf-8")
            .replace('anchors: ["protected/module.py"]', 'anchors: ["protected/module.py", "unrelated/notes.py"]'),
            encoding="utf-8",
        )

    target = "unrelated/notes.py"
    if tamper == "patterns-emptied":
        target = "protected/module.py"  # the path the stripped patterns used to cover

    run_hook(
        project,
        "pre-tool-intent-guard.sh",
        extra_env={"TRW_PYTHON": str(stub)},
        tool_input={"file_path": target},
    )

    assert _spawns(log) == 1, f"the {tamper} sidecar produced a fast ALLOW instead of deferring"


def test_the_shell_and_the_typed_config_name_the_same_sidecar() -> None:
    """The two layers must agree about WHERE the sidecar is, or it is never read.

    The hooks resolve the default spelling with builtins (no YAML parse in sh),
    so this literal is the contract between them. A rename on either side would
    otherwise disable the fast path silently — everything would still pass, just
    slowly, which is the failure mode nobody notices.
    """
    from trw_mcp.models.config._sub_models import IntentContractConfig

    text = _LIB.read_text(encoding="utf-8")
    assert f'_trw_globs="{IntentContractConfig().glob_sidecar_path}"' in text


def test_the_shell_pins_the_live_enrollment_schema_version() -> None:
    """The marker parser matches on the literal schema line the writer emits."""
    from trw_mcp.security.intent_contract.enrollment import ENROLLMENT_SCHEMA_VERSION

    text = _LIB.read_text(encoding="utf-8")
    assert f'"schema_version: {ENROLLMENT_SCHEMA_VERSION}") _sc_schema=1' in text
