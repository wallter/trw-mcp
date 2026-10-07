"""Feedback #159/#160: the UserPromptSubmit hook's work runs inside one wall-clock budget.

The 500 ms scoring deadline starts only after interpreter start, imports and the store read, so a cold
interpreter, a slow daemon or a slow no-jq JSON parse could run the hook past the client's timeout and lose
the whole hook output. The bound is in the PYTHON: the hook computes an absolute deadline (epoch ms) once and
hands it to every Python it starts (``TRW_HOOK_DEADLINE_MS``); each arms a daemon watchdog thread, before its
heavy imports, that exits 0 at the deadline without writing anything partial. The pure-shell steps are fast
and stay unbounded. One shell backstop covers a Python that never reaches its watchdog (a hung interpreter
start): it waits on that ONE child and kills only it. There is no process supervisor and no tree kill.

These tests drive the REAL hook script with stub interpreters that outlive each budget.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT
from tests._timing import assert_budget

if MONOREPO_ROOT is None:
    pytest.skip("monorepo-only invariant (repo-root hook copies absent)", allow_module_level=True)

from tests._auto_recall_hook_harness import (
    _HOOK_PATHS,
    _MATCHING_PROMPT,
    _MATCHING_SUMMARY,
    _copy_hook_to_temp,
    _run_hook,
    _write_learnings,
    diagnostic,
    fixture_store_python,
)

_BUNDLED_HOOK = _HOOK_PATHS[-1]
_SETTINGS = _BUNDLED_HOOK.parents[1] / "settings.json"
_LIB = _BUNDLED_HOOK.parent / "lib-trw.sh"
#: What one hook run costs beyond its budget on a quiet host: two reads of the clock, the shell backstop's
#: 100 ms grace and its 120 ms check interval, the phase line, logging. Far under the 60 s a stub sleeps.
_SLACK_S = 0.9

timing = pytest.mark.requires_local_timing


def _stub(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:  # trw-fail-silent-allow: the signal-0 probe's whole answer is "no such process"
        return False
    return True


def _wait_dead(pids: list[int], within_s: float = 3.0) -> bool:
    end = time.monotonic() + within_s
    while time.monotonic() < end:
        if not any(_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return False


def _pids(*files: Path) -> list[int]:
    return [int(f.read_text(encoding="utf-8").split()[0]) for f in files if f.is_file()]


def _tmpdir(tmp_path: Path) -> Path:
    d = tmp_path / "hook-tmp"
    d.mkdir(exist_ok=True)
    return d


def _timed(tmp_path: Path, **kwargs: object) -> tuple[object, float]:
    started = time.monotonic()
    run = _run_hook(tmp_path, _BUNDLED_HOOK, **kwargs)  # type: ignore[arg-type]
    return run, time.monotonic() - started


# --------------------------------------------------------------------------
# The in-process watchdog
# --------------------------------------------------------------------------


def _watchdog_code() -> str:
    out = subprocess.run(
        ["sh", "-c", f'. "{_LIB}"; printf "%s" "$_TRW_PY_WATCHDOG"'], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip(), "lib-trw.sh defines no _TRW_PY_WATCHDOG"
    return out.stdout


@pytest.mark.unit
@timing
def test_the_watchdog_exits_zero_at_the_deadline_leaving_nothing_partial() -> None:
    deadline_ms = int(time.time() * 1000) + 300
    code = _watchdog_code() + "\nimport sys, time\nsys.stdout.write('partial')\ntime.sleep(30)\n"
    started = time.monotonic()
    done = subprocess.run(
        [shutil.which("python3") or "python3", "-I", "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "TRW_HOOK_DEADLINE_MS": str(deadline_ms)},
        timeout=10,
        check=False,
    )
    assert done.returncode == 0
    assert time.monotonic() - started < 3
    assert done.stdout == ""  # buffered output is dropped, never written half


@pytest.mark.unit
@pytest.mark.parametrize("value", ["", "abc", "-5", "1e9"])
def test_the_watchdog_stays_disarmed_without_a_usable_deadline(value: str) -> None:
    code = _watchdog_code() + "\nprint('ran to completion')\n"
    done = subprocess.run(
        [shutil.which("python3") or "python3", "-I", "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "TRW_HOOK_DEADLINE_MS": value},
        timeout=10,
        check=False,
    )
    assert done.stdout.strip() == "ran to completion"


@pytest.mark.unit
@timing
def test_a_store_read_that_outlives_the_deadline_is_cut_in_process(tmp_path: Path) -> None:
    """The real recall module, started through the shipped boot code, with a store read that never returns."""
    run, elapsed = _timed(
        tmp_path / "r",
        prompt=_MATCHING_PROMPT,
        phase="implement",
        learnings=[{"learning_id": "L-1", "status": "active", "summary": _MATCHING_SUMMARY}],
        env_overrides={
            "TRW_TEST_READ_ROWS_SLEEP": "30",
            "TRW_AUTO_RECALL_DEADLINE_MS": "500",
            "TMPDIR": str(_tmpdir(tmp_path)),
        },
    )
    assert run.returncode == 0  # type: ignore[attr-defined]
    assert elapsed < 0.5 + _SLACK_S, f"a 500 ms recall budget took {elapsed:.2f}s"
    assert "TRW [IMPLEMENT]" in run.stdout  # type: ignore[attr-defined]
    assert _MATCHING_SUMMARY not in run.stdout  # type: ignore[attr-defined]
    record = diagnostic(run.project_root)  # type: ignore[attr-defined]
    assert record["decision"] == "deadline"
    assert record["injected"] == "0"


# --------------------------------------------------------------------------
# The shell backstop (a Python that never reaches its watchdog)
# --------------------------------------------------------------------------


@pytest.mark.unit
@timing
def test_a_hung_interpreter_is_killed_at_the_deadline_and_the_hook_still_emits(tmp_path: Path) -> None:
    tmp = _tmpdir(tmp_path)
    pid_file = tmp_path / "slow.pid"
    stub = _stub(tmp_path / "slow-python", f"echo $$ > '{pid_file}'\nexec sleep 60\n")
    run, elapsed = _timed(
        tmp_path / "r",
        prompt=_MATCHING_PROMPT,
        phase="implement",
        env_overrides={"TRW_PYTHON": str(stub), "TRW_AUTO_RECALL_DEADLINE_MS": "400", "TMPDIR": str(tmp)},
    )
    assert run.returncode == 0  # type: ignore[attr-defined]
    assert elapsed < 0.4 + _SLACK_S, f"a 400 ms recall budget took {elapsed:.2f}s"
    assert "TRW [IMPLEMENT]" in run.stdout  # type: ignore[attr-defined]
    record = diagnostic(run.project_root)  # type: ignore[attr-defined]
    assert record["decision"] == "deadline"
    assert record["injected"] == "0"
    assert _wait_dead(_pids(pid_file))
    assert list(tmp.iterdir()) == []  # no capture file (the raw prompt included) is left behind


_NOW = "perl -MTime::HiRes=time -e 'printf \"%.3f\\n\", time'"


@pytest.mark.unit
@timing
def test_one_recall_budget_is_shared_by_every_interpreter_candidate(tmp_path: Path) -> None:
    """Candidate 1 starts, burns 0.5 s and cannot start the module (exit 1); candidate 2 hangs, heartbeating.
    With ONE 1 s budget candidate 2 is cut about 0.5 s after it began, so its last heartbeat lies ~1.0 s after
    candidate 1 began; a budget per candidate would let it run a further full second (~1.5 s). Every figure is
    a timestamp the stubs wrote themselves, so host setup cost and load do not enter."""
    started = tmp_path / "cand1.start"
    heartbeat = tmp_path / "cand2.beat"
    marker = tmp_path / "third-candidate-ran"
    slow_fail = _stub(tmp_path / "fail-python", f"{_NOW} > '{started}'\nsleep 0.5\nexit 1\n")
    root = tmp_path / "r"
    venv = root / "bundled" / ".venv" / "bin"
    venv.mkdir(parents=True)
    _stub(venv / "python", f"while :; do {_NOW} > '{heartbeat}'; sleep 0.05; done\n")
    _stub(venv / "python3", f"touch '{marker}'\nexit 0\n")
    run, _elapsed = _timed(
        root,
        prompt=_MATCHING_PROMPT,
        phase="implement",
        env_overrides={
            "TRW_PYTHON": str(slow_fail),
            "TRW_AUTO_RECALL_DEADLINE_MS": "1000",
            "TMPDIR": str(_tmpdir(tmp_path)),
        },
    )
    assert run.returncode == 0  # type: ignore[attr-defined]
    ran = float(heartbeat.read_text(encoding="utf-8")) - float(started.read_text(encoding="utf-8"))
    assert 0.5 <= ran <= 1.0 + 0.25, f"candidate 2 was last alive {ran:.2f}s after candidate 1 began"
    assert not marker.exists(), "a candidate started after the recall budget was spent"
    assert diagnostic(run.project_root)["decision"] == "deadline"  # type: ignore[attr-defined]


@pytest.mark.unit
@timing
@pytest.mark.parametrize(
    ("value", "extra", "low_s", "high_s"),
    [
        ("08", {}, 0.0, 1.0),  # leading zero: decimal 8 ms, not a shell arithmetic error
        ("0400", {}, 0.3, 1.4),  # decimal 400, never octal 256
        ("000", {}, 1.8, 3.2),  # zero is not a budget: the 2000 ms default applies
        ("99999999999999999999", {"TRW_HOOK_BUDGET_MS": "2500"}, 0.3, 3.0),  # capped, then bounded by the hook budget
        ("abc", {"TRW_HOOK_BUDGET_MS": "2500"}, 0.3, 3.0),  # unparsable: the default
    ],
)
def test_budget_values_are_normalised_to_decimal_and_capped(
    tmp_path: Path, value: str, extra: dict[str, str], low_s: float, high_s: float
) -> None:
    stub = _stub(tmp_path / "slow-python", "exec sleep 60\n")
    run, elapsed = _timed(
        tmp_path / "r",
        prompt=_MATCHING_PROMPT,
        phase="implement",
        env_overrides={
            "TRW_PYTHON": str(stub),
            "TRW_AUTO_RECALL_DEADLINE_MS": value,
            "TMPDIR": str(_tmpdir(tmp_path)),
            **extra,
        },
    )
    assert run.returncode == 0  # type: ignore[attr-defined]
    assert low_s <= elapsed <= high_s, (value, elapsed)
    assert diagnostic(run.project_root)["decision"] == "deadline"  # type: ignore[attr-defined]


def _run_fast_interpreter(tmp_path: Path) -> tuple[object, float]:
    return _timed(
        tmp_path / "r",
        prompt=_MATCHING_PROMPT,
        phase="implement",
        learnings=[{"learning_id": "L-1", "status": "active", "summary": _MATCHING_SUMMARY}],
        env_overrides={"TRW_AUTO_RECALL_DEADLINE_MS": "20000"},
    )


@pytest.mark.unit
def test_a_fast_interpreter_still_injects_under_a_long_budget(tmp_path: Path) -> None:
    run, _elapsed = _run_fast_interpreter(tmp_path)
    assert _MATCHING_SUMMARY in run.stdout  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.requires_local_timing
def test_a_fast_interpreter_is_not_delayed_by_the_budget(tmp_path: Path) -> None:
    """A budget is a ceiling, never a wait: a recall that finishes at once ends the hook at once."""
    _run, elapsed = _run_fast_interpreter(tmp_path)
    assert_budget("ups_fast_interpreter_long_budget", elapsed, 4.0, "s")


def _no_jq_path(tmp_path: Path, python3: Path) -> str:
    bin_dir = tmp_path / "bin-slow-json"
    bin_dir.mkdir()
    for name in ("sh", "grep", "head", "sed", "tr", "cat", "dirname", "mkdir", "rm", "mktemp", "sleep", "date", "perl"):
        found = shutil.which(name)
        if found:
            (bin_dir / name).symlink_to(found)
    (bin_dir / "python3").symlink_to(python3)
    return str(bin_dir)


@pytest.mark.unit
@timing
def test_a_stalled_no_jq_json_parse_cannot_outrun_the_hook_budget(tmp_path: Path) -> None:
    """The JSON fallback starts python3 before recall ever runs. A parser that never reaches its watchdog is
    killed by the backstop at the hook deadline, and every later parse is refused outright, not re-run."""
    pid_file = tmp_path / "json.pid"
    slow = _stub(tmp_path / "slow-python3", f"echo $$ >> '{pid_file}'\nexec sleep 60\n")
    tmp = _tmpdir(tmp_path)
    run, elapsed = _timed(
        tmp_path / "r",
        prompt=_MATCHING_PROMPT,
        phase="implement",
        path_override=_no_jq_path(tmp_path, slow),
        env_overrides={"TRW_HOOK_BUDGET_MS": "1500", "TMPDIR": str(tmp)},
    )
    assert run.returncode == 0  # type: ignore[attr-defined]
    assert elapsed < 1.5 + _SLACK_S, f"the hook ran {elapsed:.2f}s against a 1.5 s budget"
    started = pid_file.read_text(encoding="utf-8").split()
    assert len(started) == 1, f"a parse started after the budget was spent: {started}"
    assert _wait_dead([int(p) for p in started])
    assert list(tmp.iterdir()) == []


# --------------------------------------------------------------------------
# Cancellation and cleanup
# --------------------------------------------------------------------------


def _start_hook(tmp_path: Path, stub: Path, tmp: Path) -> subprocess.Popen[str]:
    project_root, hook_path, _rows = _copy_hook_to_temp(tmp_path, _BUNDLED_HOOK)
    env = os.environ.copy()
    env.update(
        {
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": "implement",
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
            "TRW_PYTHON": str(stub),
            "TMPDIR": str(tmp),
        }
    )
    proc = subprocess.Popen(
        ["sh", str(hook_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=project_root,
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.write(json.dumps({"prompt": _MATCHING_PROMPT}))
    proc.stdin.close()
    return proc


@pytest.mark.unit
@timing
@pytest.mark.parametrize(
    ("sig", "again"),
    [(signal.SIGTERM, False), (signal.SIGINT, False), (signal.SIGHUP, False), (signal.SIGTERM, True)],
)
def test_a_cancelled_hook_removes_its_files_and_its_interpreter(
    tmp_path: Path, sig: signal.Signals, again: bool
) -> None:
    """A second signal arriving during cleanup must not interrupt it."""
    pid_file = tmp_path / "main.pid"
    stub = _stub(tmp_path / "hung-python", f"echo $$ > '{pid_file}'\nexec sleep 60\n")
    tmp = _tmpdir(tmp_path)
    proc = _start_hook(tmp_path, stub, tmp)
    end = time.monotonic() + 8
    while not _pids(pid_file) and time.monotonic() < end:
        time.sleep(0.02)
    assert _pids(pid_file), "the recall interpreter never started"
    proc.send_signal(sig)
    if again:
        proc.send_signal(sig)
    started = time.monotonic()
    proc.wait(timeout=5)
    assert time.monotonic() - started < 1.0
    assert proc.returncode == 0
    assert _wait_dead(_pids(pid_file)), "a cancelled hook orphaned its interpreter"
    assert list(tmp.iterdir()) == [], "a cancelled hook left its capture files (the raw prompt among them)"


# --------------------------------------------------------------------------
# Dedup state at the output boundary, and mixed module versions
# --------------------------------------------------------------------------


def _injected_ids(project_root: Path) -> str:
    path = project_root / ".trw" / "context" / "injected_learning_ids.txt"
    return path.read_text(encoding="utf-8") if path.is_file() else ""


_RECORD = (
    "event=AutoRecall keywords=1 scanned=1 top_score=1.000 top_id=L-1 threshold=0.350"
    " injected=1 decision=fired elapsed_ms=1"
)
#: What a RELEASED (older) trw-mcp recall module does with the third positional, its dedup file: reads it as the
#: history of ids already delivered, injects L-1 unless it is in that history, and appends it. It ignores any
#: further argument. Invoked as `python -c <boot> <module> <root> - <dedup>`.
_OLD_MODULE = f"""shift 3
if grep -qx 'L-1' "$3" 2>/dev/null; then
  echo "event=AutoRecall keywords=1 scanned=1 top_score=0.000 top_id=none threshold=0.350 injected=0 decision=deduped elapsed_ms=1" >&2
  exit 0
fi
printf 'L-1\\n' >> "$3"
printf 'recalled text'
echo "{_RECORD}" >&2
"""


def _run_with_stdout_closed(tmp_path: Path, stub: Path | None, history: str = "") -> tuple[Path, Path]:
    """Run the hook with the client's end of stdout already closed. The phase line is cached, so the recall
    text is the first thing the hook writes: its output never reaches the client."""
    project_root, hook_path, rows_file = _copy_hook_to_temp(tmp_path, _BUNDLED_HOOK)
    _write_learnings(rows_file, [{"learning_id": "L-1", "status": "active", "summary": _MATCHING_SUMMARY}])
    (project_root / ".trw" / "context" / "last_ups_phase").write_text("implement", encoding="utf-8")
    if history:
        (project_root / ".trw" / "context" / "injected_learning_ids.txt").write_text(history, encoding="utf-8")
    tmp = _tmpdir(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": "implement",
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
            "TRW_PYTHON": str(stub or fixture_store_python(project_root)),
            "TMPDIR": str(tmp),
        }
    )
    proc = subprocess.Popen(
        ["sh", str(hook_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        cwd=project_root,
        env=env,
    )
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdout.close()  # the reader is gone before the hook has anything to write
    proc.stdin.write(json.dumps({"prompt": _MATCHING_PROMPT}))
    proc.stdin.close()
    proc.wait(timeout=20)
    return project_root, tmp


@pytest.mark.unit
def test_dedup_record_is_written_once_the_text_is_emitted(tmp_path: Path) -> None:
    run = _run_hook(
        tmp_path,
        _BUNDLED_HOOK,
        prompt=_MATCHING_PROMPT,
        phase="implement",
        learnings=[{"learning_id": "L-1", "status": "active", "summary": _MATCHING_SUMMARY}],
    )
    assert _MATCHING_SUMMARY in run.stdout
    assert "L-1" in _injected_ids(run.project_root)


@pytest.mark.unit
def test_a_hook_whose_output_never_reaches_the_client_does_not_mark_the_learning_delivered(tmp_path: Path) -> None:
    project_root, tmp = _run_with_stdout_closed(tmp_path, None)
    assert _injected_ids(project_root) == ""
    assert list(tmp.iterdir()) == []


@pytest.mark.unit
def test_an_older_module_never_changes_the_real_dedup_file_when_the_output_is_lost(tmp_path: Path) -> None:
    """The older module appends to the dedup path it is handed. That is a scratch copy of the real history, so
    with the client's pipe closed the real file is exactly what it was."""
    stub = _stub(tmp_path / "old-module-python", _OLD_MODULE)
    project_root, _tmp = _run_with_stdout_closed(tmp_path, stub, history="L-0\n")
    assert _injected_ids(project_root) == "L-0\n"


@pytest.mark.unit
def test_an_older_module_keeps_duplicate_suppression_across_successive_prompts(tmp_path: Path) -> None:
    """The scratch file is seeded with the real history, so the second prompt's older module sees what the first
    one emitted and does not inject it again; the real file ends with each id once, old history intact."""
    stub = _stub(tmp_path / "old-module-python", _OLD_MODULE)
    env = {"TRW_PYTHON": str(stub)}
    first = _run_hook(tmp_path, _BUNDLED_HOOK, prompt=_MATCHING_PROMPT, phase="implement", env_overrides=env)
    assert "recalled text" in first.stdout
    assert _injected_ids(first.project_root).split() == ["L-1"]
    second = _run_hook(tmp_path, _BUNDLED_HOOK, prompt=_MATCHING_PROMPT, phase="implement", env_overrides=env)
    assert "recalled text" not in second.stdout
    assert _injected_ids(second.project_root).split() == ["L-1"]


@pytest.mark.unit
def test_a_prior_history_survives_when_the_older_module_adds_to_it(tmp_path: Path) -> None:
    stub = _stub(tmp_path / "old-module-python", _OLD_MODULE)
    project_root, hook_path, _rows = _copy_hook_to_temp(tmp_path, _BUNDLED_HOOK)
    ids = project_root / ".trw" / "context" / "injected_learning_ids.txt"
    ids.write_text("L-0\n", encoding="utf-8")
    run = _run_hook(
        tmp_path, _BUNDLED_HOOK, prompt=_MATCHING_PROMPT, phase="implement", env_overrides={"TRW_PYTHON": str(stub)}
    )
    assert "recalled text" in run.stdout
    assert _injected_ids(run.project_root).split() == ["L-0", "L-1"]


@pytest.mark.unit
@timing
def test_a_run_killed_at_the_deadline_does_not_mark_its_learning_delivered(tmp_path: Path) -> None:
    stub = _stub(
        tmp_path / "scored-then-stalled",
        'shift 3\nprintf "L-1\\n" >> "$3"\nprintf "never emitted"\nexec sleep 60\n',
    )
    run = _run_hook(
        tmp_path,
        _BUNDLED_HOOK,
        prompt=_MATCHING_PROMPT,
        phase="implement",
        env_overrides={"TRW_PYTHON": str(stub), "TRW_AUTO_RECALL_DEADLINE_MS": "400"},
    )
    assert run.returncode == 0
    assert "never emitted" not in run.stdout
    assert _injected_ids(run.project_root) == ""


@pytest.mark.unit
def test_the_module_reads_its_history_from_the_dedup_argument_and_appends_to_it(tmp_path: Path) -> None:
    from trw_mcp.state import _auto_recall_hook as hook

    dedup = tmp_path / "scratch.txt"
    dedup.write_text("L-1\n", encoding="utf-8")
    rows = [
        hook.Candidate("L-1", "active", _MATCHING_SUMMARY, ()),
        hook.Candidate("L-2", "active", _MATCHING_SUMMARY + " again", ()),
    ]
    status = hook.main(
        [str(tmp_path), _MATCHING_PROMPT, str(dedup), "3", "100", "0.35", "10000"],
        read_rows=lambda _root, _cap: rows,
    )
    assert status == 0
    assert dedup.read_text(encoding="utf-8").split() == ["L-1", "L-2"]  # L-1 was suppressed, L-2 appended


# --------------------------------------------------------------------------
# Config parsing and the bundled client timeout
# --------------------------------------------------------------------------


def _args_seen_by_recall(tmp_path: Path, config_yaml: str) -> list[str]:
    """The positionals after `-c <boot> <module> <root> -`: dedup, max_results, max_tokens, min_score, scan_cap."""
    seen = tmp_path / "args.txt"
    stub = _stub(tmp_path / "args-python", f"shift 3\nprintf '%s\\n' \"$@\" > '{seen}'\n")
    _run_hook(
        tmp_path / "r",
        _BUNDLED_HOOK,
        prompt=_MATCHING_PROMPT,
        phase="implement",
        config_yaml=config_yaml,
        env_overrides={"TRW_PYTHON": str(stub)},
    )
    lines = seen.read_text(encoding="utf-8").splitlines() if seen.is_file() else []
    return lines[2:]  # drop <root> and "-"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("config", "max_results", "max_tokens", "min_score"),
    [
        ("auto_recall_max_tokens: 1 # auto_recall_max_results: 1\n", "3", "1", "0.35"),
        ("# auto_recall_max_results: 9\nauto_recall_max_results: 2\n", "2", "100", "0.35"),
        ('  auto_recall_max_results: "4"\nauto_recall_max_results: 7\n', "4", "100", "0.35"),
        ("auto_recall_min_score: '0.5'   # tuned\nauto_recall_max_tokens: 50\n", "3", "50", "0.5"),
        ("other_auto_recall_max_results: 8\nauto_recall_max_results:\n", "3", "100", "0.35"),
    ],
)
def test_config_is_read_by_key_with_comments_ignored(
    tmp_path: Path, config: str, max_results: str, max_tokens: str, min_score: str
) -> None:
    args = _args_seen_by_recall(tmp_path, config)
    assert args[1:4] == [max_results, max_tokens, min_score], args


@pytest.mark.unit
def test_first_match_wins_and_quotes_are_stripped_for_the_enabled_switch(tmp_path: Path) -> None:
    run = _run_hook(
        tmp_path,
        _BUNDLED_HOOK,
        prompt=_MATCHING_PROMPT,
        phase="implement",
        learnings=[{"learning_id": "L-1", "status": "active", "summary": _MATCHING_SUMMARY}],
        config_yaml='auto_recall_enabled: "false"\nauto_recall_enabled: true\n',
    )
    assert _MATCHING_SUMMARY not in run.stdout


@pytest.mark.unit
def test_the_bundled_client_timeout_covers_the_whole_hook_budget_with_room_to_spare() -> None:
    groups = json.loads(_SETTINGS.read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"]
    timeouts = [h["timeout"] for g in groups for h in g["hooks"] if "user-prompt-submit.sh" in h.get("command", "")]
    hook_text = _BUNDLED_HOOK.read_text(encoding="utf-8")
    default_ms = int(hook_text.split("_ups_default_budget_ms=", 1)[1].split()[0].strip('"'))
    assert timeouts
    for timeout_s in timeouts:
        assert default_ms + 1500 <= timeout_s * 1000, (timeout_s, default_ms)


# --------------------------------------------------------------------------
# Cleanup installed before the directory exists; whole-second sleep hosts
# --------------------------------------------------------------------------


@pytest.mark.unit
def test_a_hook_whose_temp_dir_cannot_be_made_still_runs_and_cleans_up_nothing(tmp_path: Path) -> None:
    """Cleanup runs on exit with the directory name still empty and must not touch anything."""
    sentinel = tmp_path / "keep"
    sentinel.write_text("x", encoding="utf-8")
    run = _run_hook(
        tmp_path / "r",
        _BUNDLED_HOOK,
        prompt=_MATCHING_PROMPT,
        phase="implement",
        env_overrides={"TMPDIR": str(tmp_path / "no-such-dir")},
    )
    assert run.returncode == 0
    assert "TRW [IMPLEMENT]" in run.stdout
    assert sentinel.read_text(encoding="utf-8") == "x"
    assert diagnostic(run.project_root)["decision"] == "no_workdir"


def _whole_second_sleep_path(tmp_path: Path) -> str:
    """A PATH whose `sleep` takes whole seconds only, like a host without fractional sleep."""
    bin_dir = tmp_path / "bin-int-sleep"
    bin_dir.mkdir()
    for name in ("sh", "grep", "head", "sed", "tr", "cat", "dirname", "mkdir", "rm", "mktemp", "date", "perl", "jq"):
        found = shutil.which(name)
        if found:
            (bin_dir / name).symlink_to(found)
    real_sleep = shutil.which("sleep")
    _stub(bin_dir / "sleep", f'case "$1" in *[!0-9]*) exit 1 ;; esac\nexec "{real_sleep}" "$@"\n')
    return str(bin_dir)


@pytest.mark.unit
@timing
def test_the_backstop_reads_the_clock_on_every_tick_when_sleep_takes_whole_seconds(tmp_path: Path) -> None:
    """Reading it every 10th tick would mean ten seconds between reads here, far past a 400 ms budget."""
    stub = _stub(tmp_path / "slow-python", "exec sleep 60\n")
    run, elapsed = _timed(
        tmp_path / "r",
        prompt=_MATCHING_PROMPT,
        phase="implement",
        path_override=_whole_second_sleep_path(tmp_path),
        env_overrides={"TRW_PYTHON": str(stub), "TRW_AUTO_RECALL_DEADLINE_MS": "400", "TMPDIR": str(_tmpdir(tmp_path))},
    )
    assert run.returncode == 0  # type: ignore[attr-defined]
    assert elapsed < 0.4 + 1.0 + _SLACK_S, f"a 400 ms budget took {elapsed:.2f}s with whole-second sleeps"
    assert diagnostic(run.project_root)["decision"] == "deadline"  # type: ignore[attr-defined]
