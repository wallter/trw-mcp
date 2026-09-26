"""PRD-SEC/RC8: hook state writes must never follow a symlink.

The bug (found by adversarial review ahead of the trw-mcp 7.0.0 release): a
crafted checkout can ship a `.trw/context/*` state path -- or `.trw/context`
itself -- as a symlink to an arbitrary file/directory the user can write. The
pre-fix hooks read/wrote those paths with plain `cat`/`>`/`>>`, which follow
a symlink at both the leaf and in the parent chain, so a normal prompt
submission (or file edit, for the distill-hint libraries) truncated or wrote
attacker-chosen content into whatever the symlink pointed at -- no race
required, since the checkout is crafted ahead of time.

These tests run the REAL shipped scripts (never a reimplementation) against a
symlinked destination and assert the file OUTSIDE the checkout is untouched.
Every one of them fails against the pre-fix code (plain `>`/`>>`/`cat`) and
passes against `_trw_safe_write`/`_trw_safe_read` (hooks/lib-trw.sh), which
refuse to write through a symlinked leaf or a symlinked ancestor directory up
to `.trw`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data"
_HOOKS = _DATA / "hooks"
_USER_PROMPT_SUBMIT = _HOOKS / "user-prompt-submit.sh"
_CLAUDE_LIB = _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh"
_CURSOR_LIB = _HOOKS / "cursor" / "lib-distill-hint.sh"
_LIB_TRW = _HOOKS / "lib-trw.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")

_SENTINEL = "SECRET_DO_NOT_TOUCH_" + "x" * 40


def _base_env(project_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project_root)
    env.pop("TRW_SESSION_ID", None)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env


def _run_user_prompt_submit(project_root: Path, *, session_id: str = "sym1") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(_USER_PROMPT_SUBMIT)],
        input=json.dumps({"prompt": "hello", "session_id": session_id}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=_base_env(project_root),
        timeout=10,
        check=False,
    )


def test_symlinked_none_phase_prompt_count_leaves_outside_file_untouched(tmp_path: Path) -> None:
    """The exact bug reported: a crafted checkout symlinks the "none" phase
    counter at a target file. Pre-fix, the hook's read-modify-write (treat
    nonnumeric content as 0, increment, `>` write back) truncates and
    overwrites whatever the symlink points at with a bare digit.
    """
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)

    outside_target = tmp_path / "outside_secret.txt"
    outside_target.write_text(_SENTINEL, encoding="utf-8")

    (project_root / ".trw" / "context" / "none_phase_prompt_count").symlink_to(outside_target)

    result = _run_user_prompt_submit(project_root)

    assert result.returncode == 0
    assert outside_target.read_text(encoding="utf-8") == _SENTINEL, (
        "the hook wrote through the none_phase_prompt_count symlink into a file outside the checkout"
    )


def test_symlinked_last_ups_phase_leaves_outside_file_untouched(tmp_path: Path) -> None:
    """The FR01 phase-cache write (`printf '%s' "$_phase" > "$_phase_cache"`)
    has the identical leaf-symlink shape as none_phase_prompt_count.
    """
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    # A pinned run with a resolvable phase != "none" is not required: any
    # phase write attempt exercises the same write call.
    (project_root / ".trw" / "learnings" / "entries").mkdir(parents=True)

    outside_target = tmp_path / "outside_phase.txt"
    outside_target.write_text(_SENTINEL, encoding="utf-8")

    (project_root / ".trw" / "context" / "last_ups_phase").symlink_to(outside_target)

    result = _run_user_prompt_submit(project_root)

    assert result.returncode == 0
    assert outside_target.read_text(encoding="utf-8") == _SENTINEL, (
        "the hook wrote through the last_ups_phase symlink into a file outside the checkout"
    )


def test_symlinked_context_directory_leaves_outside_directory_untouched(tmp_path: Path) -> None:
    """Requirement 3: a symlinked `.trw/context` directory itself (not just a
    leaf file inside it) must not let any hook state write land outside the
    checkout.
    """
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)
    (project_root / ".trw").mkdir()

    outside_dir = tmp_path / "outside_context_dir"
    outside_dir.mkdir()
    (outside_dir / "none_phase_prompt_count").write_text(_SENTINEL, encoding="utf-8")

    (project_root / ".trw" / "context").symlink_to(outside_dir, target_is_directory=True)

    result = _run_user_prompt_submit(project_root)

    assert result.returncode == 0
    # No new file must appear in the symlink TARGET beyond the one seeded
    # above, and the seeded file's content must be untouched.
    assert (outside_dir / "none_phase_prompt_count").read_text(encoding="utf-8") == _SENTINEL
    assert {p.name for p in outside_dir.iterdir()} == {"none_phase_prompt_count"}, (
        "a hook created new state files INSIDE the symlink target directory instead of refusing to write"
    )


@pytest.mark.parametrize(
    "lib_path",
    [_CLAUDE_LIB, _CURSOR_LIB],
    ids=["claude_code", "cursor"],
)
def test_distill_hint_dedup_record_symlink_leaves_outside_file_untouched(tmp_path: Path, lib_path: Path) -> None:
    """The other named bug: the CC-03/CUR-06 session-scoped hint dedup record
    (`_distill_hint_already_seen`) computed a hash and wrote it with a plain
    `>` -- if the checkout ships the per-file `.hash` record's PARENT
    DIRECTORY as a symlink, the hash write lands inside the symlink target.
    Copilot's lib has no content-dedup limb (only the debounce timestamp,
    covered indirectly by the shared `_trw_safe_write` unit above), so it is
    not parametrized here.
    """
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True)

    outside_dir = tmp_path / "outside_hint_seen"
    outside_dir.mkdir()
    seed = outside_dir / "planted.txt"
    seed.write_text(_SENTINEL, encoding="utf-8")

    # Every one of the three libs uses a differently-named subdirectory under
    # .trw/context for this record; the driver below reads the directory
    # right out of the lib source so this test does not hardcode (and
    # silently drift from) that name.
    lib_source = lib_path.read_text(encoding="utf-8")
    marker = ".trw/context/"
    start = lib_source.index(marker) + len(marker)
    end = lib_source.index('"', start)
    hint_seen_dirname = lib_source[start:end]
    assert hint_seen_dirname, f"could not locate the hint-seen directory name in {lib_path}"

    (project_root / ".trw" / "context").mkdir(parents=True)
    (project_root / ".trw" / "context" / hint_seen_dirname).symlink_to(outside_dir, target_is_directory=True)

    driver = project_root / "driver.sh"
    driver.write_text(
        f'. "{lib_path}"\n_distill_hint_already_seen "{project_root}" "src/a.py" "some hint text"\nexit $?\n',
        encoding="utf-8",
    )
    result = subprocess.run(["sh", str(driver)], capture_output=True, text=True, timeout=5, check=False)

    assert result.returncode in (0, 1)  # the helper's own true/false, not a crash
    assert seed.read_text(encoding="utf-8") == _SENTINEL
    assert {p.name for p in outside_dir.iterdir()} == {"planted.txt"}, (
        "the dedup helper created a hash record INSIDE the symlinked hint-seen directory's target"
    )


def _append_driver(project_root: Path, dest: Path, line: str, count: int) -> list[str]:
    script = (
        f'. "{_LIB_TRW}"\n'
        f"i=0; while [ $i -lt {count} ]; do\n"
        f'  printf \'%s %s\\n\' "{line}" "$i" | _trw_safe_write "{dest}" append; rc=$?\n'
        "  i=$((i + 1))\n"
        "done\n"
        "exit $rc\n"
    )
    return ["sh", "-c", script]


def test_concurrent_appends_keep_every_line(tmp_path: Path) -> None:
    """Parallel tool calls run their hooks at once; every appended event must
    survive. A read-copy-rename append (rc8's first writer) let the last
    rename discard what the others had appended; O_APPEND keeps them all.
    """
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    dest = project_root / ".trw" / "context" / "session-events.jsonl"
    writers = [
        subprocess.Popen(_append_driver(project_root, dest, f"w{n}", 40), env=_base_env(project_root)) for n in range(8)
    ]
    assert all(w.wait(timeout=60) == 0 for w in writers)

    lines = dest.read_text(encoding="utf-8").splitlines()
    assert sorted(lines) == sorted(f"w{n} {i}" for n in range(8) for i in range(40))


def test_append_refuses_a_symlinked_leaf(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    outside_target = tmp_path / "outside_events.txt"
    outside_target.write_text(_SENTINEL, encoding="utf-8")
    dest = project_root / ".trw" / "context" / "session-events.jsonl"
    dest.symlink_to(outside_target)

    result = subprocess.run(
        _append_driver(project_root, dest, "evt", 1), env=_base_env(project_root), timeout=10, check=False
    )

    assert result.returncode == 1
    assert outside_target.read_text(encoding="utf-8") == _SENTINEL
    assert dest.is_symlink()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_append_refuses_a_fifo_instead_of_blocking(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    dest = project_root / ".trw" / "context" / "session-events.jsonl"
    os.mkfifo(dest)

    result = subprocess.run(
        _append_driver(project_root, dest, "evt", 1), env=_base_env(project_root), timeout=10, check=False
    )

    assert result.returncode == 1


def test_replace_refuses_a_leaf_symlink_to_a_directory(tmp_path: Path) -> None:
    """`mv -f tmp dest` moves INTO a directory that dest names through a symlink."""
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (project_root / ".trw" / "context" / "none_phase_prompt_count").symlink_to(outside_dir, target_is_directory=True)

    result = _run_user_prompt_submit(project_root)

    assert result.returncode == 0
    assert list(outside_dir.iterdir()) == [], "a hook moved its state file into the symlinked directory's target"


def test_append_refuses_a_hard_linked_leaf(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    outside_target = tmp_path / "outside_events.txt"
    outside_target.write_text(_SENTINEL, encoding="utf-8")
    dest = project_root / ".trw" / "context" / "session-events.jsonl"
    os.link(outside_target, dest)

    result = subprocess.run(
        _append_driver(project_root, dest, "evt", 1), env=_base_env(project_root), timeout=10, check=False
    )

    assert result.returncode == 1
    assert outside_target.read_text(encoding="utf-8") == _SENTINEL


@pytest.mark.parametrize(
    ("hook", "payload"),
    [
        ("trw-before-shell.sh", {"command": "ls"}),
        ("trw-after-shell.sh", {"command": "ls", "exit_code": 0, "duration": 1}),
        ("trw-after-mcp.sh", {"tool_name": "trw_status"}),
    ],
)
def test_cursor_logger_refuses_a_symlinked_log_leaf(tmp_path: Path, hook: str, payload: dict[str, object]) -> None:
    project_root = tmp_path / "project"
    (project_root / ".trw" / "logs").mkdir(parents=True)
    outside_target = tmp_path / "outside_log.txt"
    outside_target.write_text(_SENTINEL, encoding="utf-8")
    (project_root / ".trw" / "logs" / "cursor-hooks.jsonl").symlink_to(outside_target)
    env = _base_env(project_root)
    env["CURSOR_PROJECT_DIR"] = str(project_root)

    subprocess.run(
        ["bash", str(_HOOKS / "cursor" / hook)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=env,
        timeout=10,
        check=False,
    )

    assert outside_target.read_text(encoding="utf-8") == _SENTINEL
