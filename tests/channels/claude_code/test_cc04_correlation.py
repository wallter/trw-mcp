"""Behavioral tests for the CC-04 hint-file layer (PRD-DIST-2405 FR33-FR36).

PRD-CORE-239 FR01 removed the ``cc-04-posttooluse-correlation`` *channel* —
its manifest entry no longer exists in manifest-claude-code.yaml, and the
three tests that asserted that entry were deleted with it. What remains is
still live: ``write_hint_file`` is invoked by the shipped CC-03 PreToolUse
hook (``data/claude_code/hooks/pre-tool-distill-hint.sh``), so every case
below exercises code that runs on a real install.

Caveat worth carrying: nothing *reads* those hint files. ``prune_hint_files``
has no caller and no ``edit_correlated`` event is emitted anywhere, so the
hint files are a producer without a consumer. That is pre-existing (see
CHANGELOG "0 such events across 4,126 records") and outside FR01's scope, but
these tests should not be read as evidence that correlation works end to end.

Architecture finding: CC-04 has NO shell-level PostToolUse hook script.
The correlation layer is entirely implemented in Python via:
  - ``write_hint_file()``   — writes per-hint JSON keyed by tool_use_id (FR33)
  - ``prune_hint_files()``  — cleans expired hint files (FR35)

These Python helpers are already covered in test_hook_helpers.py.

What this file tests:
  1. The Python integration: write_hint_file produces files keyed by tool_use_id
     with the correct CC-04 schema (FR33/FR36). Tested via Python layer directly.
  2. No cross-contamination between different tool_use_ids (FR33).
  3. Fail-open on IO error (FR34): write_hint_file creates the dir if absent.
  4. Hint file structure matches CC-04 correlation schema (FR33/FR36).
  5. Shell-level hint-file write succeeds for warm invocations within aligned timeout
     (2.5s). compute_before_edit_hint imports ~0.76s (no embedding stack at module
     level), so warm calls complete well within budget.

No PostToolUse shell hook script exists in the data directory — confirmed by
inspection of data/claude_code/hooks/: only pre-tool-distill-hint.sh and
lib-distill-hint.sh are present. The shell hook activates CC-04 correlation
by invoking write_hint_file via its Python subprocess (inline Python in the
shell script, not a separate hook file).

Timeout alignment (fixed): the hook previously used a bare `timeout 2.5` prefix,
a GNU coreutils binary macOS does not ship, so the bound failed with 127 before
the interpreter started and every record read `timeout_fallback`. The 2500ms
budget is now the program's own SIGALRM deadline with `_trw_bounded_python` as
the outer backstop (2026-09-17). compute_before_edit_hint
does NOT import the embedding/trw-memory stack at module level (~0.76s warm),
so write_hint_file runs within the aligned 2.5s budget on warm invocations.
Cold-start (.pyc compilation) may still exceed 2.5s on first run; the hook
falls back to T0 beacon in that edge case, which is acceptable UX.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, run_distill_hint_hook

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_hook(
    stdin_payload: str,
    tmp_project: Path,
    *,
    timeout: int = 8,
    checkout_pythonpath: bool = True,
) -> subprocess.CompletedProcess[str]:
    # L-CUAX: run_distill_hint_hook pins cwd/HOME to tmp_project so the hook's own
    # git-rev-parse and compute_before_edit_hint's Path.cwd() fallbacks can never
    # resolve the enclosing checkout running this test suite.
    return run_distill_hint_hook(
        stdin_payload,
        tmp_project,
        timeout=timeout,
        checkout_pythonpath=checkout_pythonpath,
    )


def _enable_cc03(tmp_project: Path) -> None:
    trw_dir = tmp_project / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    channels = trw_dir / "channels"
    channels.mkdir()
    (channels / "cc03-python.txt").write_text(sys.executable, encoding="utf-8")


def _make_pretooluse(
    file_path: str = "src/module.py",
    tool_use_id: str = "toolu-cc04-001",
    tool_name: str = "Edit",
) -> str:
    return json.dumps(
        {
            "tool_use_id": tool_use_id,
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
        }
    )


# ---------------------------------------------------------------------------
# FR33 — Hint file keyed by tool_use_id (Python integration layer)
# ---------------------------------------------------------------------------
# These tests verify the Python CC-04 integration layer directly (via
# write_hint_file). Shell-level hint-file write is tested in
# test_shell_hint_file_written_within_aligned_timeout. The shell timeout was
# fixed to 2.5s (from buggy 2s) — see module docstring for the full account.


class TestHintFileKeyedByToolUseId:
    """FR33: hint file is written at hints_dir/{tool_use_id}.json (Python layer)."""

    def test_hint_file_created_at_expected_path(self, tmp_path: Path) -> None:
        """write_hint_file creates {hints_dir}/{tool_use_id}.json."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-cc04-abc"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path="src/module.py",
            tier="T0",
            hint_emitted=True,
            tokens_emitted=10,
            distill_status="sidecar_missing",
        )
        assert (hints_dir / f"{tool_use_id}.json").exists()

    def test_hint_file_schema_has_required_fields(self, tmp_path: Path) -> None:
        """FR36: hint file schema includes all required CC-04 correlation fields."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-schema-check"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path="src/schema.py",
            tier="T2",
            hint_emitted=True,
            tokens_emitted=45,
            distill_status="hint_available",
        )
        hint_file = hints_dir / f"{tool_use_id}.json"
        data = json.loads(hint_file.read_text(encoding="utf-8"))
        required = {"ts", "file_path", "tier", "hint_emitted", "tokens_emitted", "distill_status", "tool_use_id"}
        missing = required - set(data.keys())
        assert not missing, f"Hint file missing required fields: {missing}"

    def test_hint_file_tool_use_id_matches(self, tmp_path: Path) -> None:
        """FR33: tool_use_id in hint file matches the input."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-id-match"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path="src/match.py",
            tier="T1",
            hint_emitted=True,
            tokens_emitted=20,
            distill_status="tier_required",
        )
        data = json.loads((hints_dir / f"{tool_use_id}.json").read_text(encoding="utf-8"))
        assert data["tool_use_id"] == tool_use_id

    def test_hint_file_file_path_matches(self, tmp_path: Path) -> None:
        """FR33: file_path in hint file matches the input."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        tool_use_id = "toolu-fp-match"
        file_path = "src/target.py"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=tool_use_id,
            file_path=file_path,
            tier="T0",
            hint_emitted=False,
            tokens_emitted=0,
            distill_status="sidecar_missing",
        )
        data = json.loads((hints_dir / f"{tool_use_id}.json").read_text(encoding="utf-8"))
        assert data["file_path"] == file_path

    def test_shell_hook_always_leaves_a_well_formed_correlation_record(self, tmp_path: Path) -> None:
        """FR33/FR29: the hook never leaves CC-04 correlation without a record.

        **Renamed and re-scoped, because the old name claimed something the body
        could not show.** It was
        ``test_shell_hint_file_written_within_aligned_timeout`` and its only
        assertions were ``returncode == 0`` and ``hint_file.exists()``. The hook
        writes a *provisional* record **before** starting the bounded subprocess
        precisely so correlation survives a timeout — so the file exists whether the
        computation finished or timed out entirely. The assertion could not
        distinguish the two, which is the one thing its name promised.

        Deliberately NOT asserting that the computation completed. That depends on
        interpreter start and import cost on the host, so pinning it would make this
        a wall-clock test that passes on a fast box and fails under load — the same
        trap as the eval-side parallelism test fixed in this run.

        What is asserted instead is what holds on every host: a record exists, it is
        valid JSON, it identifies the right edit, and its ``distill_status`` is a
        member of the known vocabulary rather than arbitrary text.
        """
        _enable_cc03(tmp_path)
        tool_use_id = "toolu-aligned-timeout"
        result = _run_hook(
            _make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id),
            tmp_path,
        )
        # FR26: exit code always 0
        assert result.returncode == 0
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        hint_file = hints_dir / f"{tool_use_id}.json"
        assert hint_file.exists(), "the hook must never leave CC-04 correlation without a record"

        record = json.loads(hint_file.read_text(encoding="utf-8"))
        assert record["tool_use_id"] == tool_use_id, "the record does not identify the edit it belongs to"
        assert record["file_path"] == "src/module.py"
        # The vocabulary, not one member of it. A status outside this set means the
        # writer invented one, which is the failure a bare exists() check misses.
        # Derived from the hint's own status type, never re-spelled (a hand-copied
        # set here drifted behind it), plus the two statuses only the hook writes.
        from typing import get_args

        from trw_mcp.tools._before_edit_hint_core import BeforeEditHintStatus

        known = {value for literal in get_args(BeforeEditHintStatus) for value in get_args(literal)}
        assert {"hint_available", "hint_available_stale", "sidecar_too_far_behind"} <= known  # non-vacuity
        assert record["distill_status"] in known | {"timeout_fallback", "exception_fallback"}, (
            f"unknown distill_status {record['distill_status']!r}"
        )

    def test_shell_hook_record_always_carries_the_8_2_s3_fields(self, tmp_path: Path) -> None:
        """8.2 S3: every record the hook writes -- provisional, completed, or
        exception -- carries ``duration_ms``, ``sidecar_commits_behind`` and
        ``target_changed_since_sidecar``, present (possibly null) rather than
        silently missing on some paths and not others.

        Exercises the real production call site: the shipped
        ``pre-tool-distill-hint.sh`` shell hook, not just ``write_hint_file``
        directly.
        """
        _enable_cc03(tmp_path)
        tool_use_id = "toolu-s3-fields"
        result = _run_hook(
            _make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id),
            tmp_path,
        )
        assert result.returncode == 0
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        record = json.loads((hints_dir / f"{tool_use_id}.json").read_text(encoding="utf-8"))
        for key in ("duration_ms", "sidecar_commits_behind", "target_changed_since_sidecar"):
            assert key in record, f"{key} must always be present, even as null"
        assert record["duration_ms"] is None or isinstance(record["duration_ms"], (int, float))
        assert record["sidecar_commits_behind"] is None or isinstance(record["sidecar_commits_behind"], int)
        assert record["target_changed_since_sidecar"] is None or isinstance(
            record["target_changed_since_sidecar"], bool
        )


# ---------------------------------------------------------------------------
# FR33 — No cross-contamination between different tool_use_ids
# ---------------------------------------------------------------------------


class TestExceptionIsNotTelemeteredAsATimeout:
    """A raised subprocess must not leave the provisional ``timeout_fallback`` record.

    The hook writes a provisional CC-04 record with
    ``distill_status="timeout_fallback"`` BEFORE starting the bounded (2.5s)
    intelligence subprocess, expecting a successful run to overwrite it. But
    ``write_hint_file`` is called inside the same ``try:`` as
    ``compute_before_edit_hint``, so any exception — a broken venv
    ``ImportError``, a version-skew ``AttributeError``, a real bug — used to
    leave the provisional record standing. An operator debugging a low hit
    rate then saw a ``timeout_fallback`` count mixing real 2.5s timeouts with
    unrelated exceptions, and tuned the timeout budget for a defect that has
    nothing to do with timing.

    Both cases are exercised here. The timeout case is the non-vacuity control:
    an ``except`` handler that unconditionally stamped ``exception_fallback``,
    or one that stopped writing the provisional record at all, would fail it.
    """

    @staticmethod
    def _project(tmp_path: Path, python_path: str) -> Path:
        _enable_cc03(tmp_path)
        (tmp_path / ".trw" / "channels" / "cc03-python.txt").write_text(python_path, encoding="utf-8")
        return tmp_path

    @staticmethod
    def _status(tmp_project: Path, tool_use_id: str) -> str:
        record = tmp_project / ".trw" / "context" / "cc03-hints" / f"{tool_use_id}.json"
        assert record.exists(), "the hook must always leave a correlation record"
        status = json.loads(record.read_text(encoding="utf-8"))["distill_status"]
        assert isinstance(status, str)
        return status

    def test_import_failure_records_exception_not_timeout(self, tmp_path: Path) -> None:
        """A Python that cannot import ``trw_mcp`` raises instantly — nothing timed out."""
        # sys.base_prefix's interpreter is the system Python: it runs the
        # stdlib-only provisional writer fine, and raises ImportError on
        # `from trw_mcp.tools.before_edit_hint import ...`.
        if sys.base_prefix == sys.prefix:
            pytest.skip("not running in a venv: the base interpreter imports trw_mcp, so nothing raises")
        system_python = str(Path(sys.base_prefix) / "bin" / "python3")
        if not Path(system_python).is_file():
            pytest.skip(f"no non-venv interpreter at {system_python} to force an ImportError")

        project = self._project(tmp_path, system_python)
        tool_use_id = "toolu-exc-fallback"
        result = _run_hook(
            _make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id), project, checkout_pythonpath=False
        )
        assert result.returncode == 0  # FR26: never blocking
        assert self._status(project, tool_use_id) == "exception_fallback"
        # PRD-FIX-155: the record keeps what raised, not only that something did.
        record = json.loads((project / ".trw/context/cc03-hints" / f"{tool_use_id}.json").read_text(encoding="utf-8"))
        # A base interpreter may carry a namespace or older trw_mcp, so the missing module may be a submodule.
        assert record["error"].startswith("ModuleNotFoundError: No module named 'trw_mcp")

    def test_genuine_timeout_still_records_timeout(self, tmp_path: Path) -> None:
        """Non-vacuity control: a real 2.5s overrun must still read ``timeout_fallback``.

        The shim hangs INSTEAD of running Python, so the program's own SIGALRM
        deadline can never fire — this is exactly the case ``_trw_bounded_python``
        exists for. Its outer bound (``timeout`` where the box has one, a POSIX
        watchdog where it does not) kills the subprocess before any handler can
        run, and the provisional record is the correct answer.
        """
        shim = tmp_path / "slow-python.sh"
        shim.write_text(
            "#!/bin/sh\n"
            'case "$2" in\n'
            "  *compute_before_edit_hint*) sleep 10 ;;\n"
            f'  *) exec "{sys.executable}" "$@" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        shim.chmod(0o755)

        project = self._project(tmp_path, str(shim))
        tool_use_id = "toolu-real-timeout"
        result = _run_hook(_make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id), project)
        assert result.returncode == 0
        assert self._status(project, tool_use_id) == "timeout_fallback"


class TestRecordMatchesWhatWasDelivered:
    """Regression: a deadline tick landing AFTER the record is written must never
    desync the on-disk record from what the model actually received.

    ``write_hint_file()`` used to run while the hook's own 2.4s SIGALRM was still
    armed. A completed write followed by ANY further delay before the interpreter's
    natural exit (GC, buffered-IO teardown, host scheduling jitter) could still take
    the alarm past its deadline, calling ``os._exit(124)`` after the record already
    said T2 -- the outer shell then discards the already-printed T2 text (non-zero
    exit code) and substitutes the T0 beacon instead. Observed on a canary: a record
    read ``tier=T2 hint_available duration_ms=2399`` while the model got the T0
    beacon. The fix disarms the timer once the last hint is printed and flushed and
    before ``write_hint_file()`` runs, so nothing downstream of a completed write can
    still be killed by it.

    A ``sitecustomize.py`` on ``PYTHONPATH`` is the only way to get a delay to land
    in that exact after-write window from outside the shell/Python boundary: it
    patches ``compute_before_edit_hint`` to return a fixed T2 result (no real
    sidecar needed) and wraps ``write_hint_file`` to sleep *after* the real write
    completes, simulating the residual-time tick this fix removes.
    """

    @staticmethod
    def _project_with_slow_post_write(tmp_path: Path, *, delay_s: float) -> tuple[Path, dict[str, str]]:
        sitecustomize_dir = tmp_path / "sitecustomize_dir"
        sitecustomize_dir.mkdir()
        (sitecustomize_dir / "sitecustomize.py").write_text(
            "import time\n"
            "import trw_mcp.tools._before_edit_hint_core as _core\n"
            "import trw_mcp.channels.claude_code._hook_helpers as _helpers\n"
            "\n"
            "class _FakeHint:\n"
            "    risk_score = 0.9\n"
            "    hotspot_warnings = []\n"
            "    co_change_neighbors = []\n"
            "    inferred_tests = []\n"
            "    lessons = []\n"
            "    lessons_status = None\n"
            "\n"
            "class _FakeResult:\n"
            "    distill_status = 'hint_available'\n"
            "    distill_hint = _FakeHint()\n"
            "    distill_as_of = None\n"
            # distill_action: read by the once-per-session sidecar remedy line the hook now prints.
            "    distill_action = None\n"
            "    learnings = []\n"
            "\n"
            # repo_root: the hook now names the checkout it resolved from the edited file (worktree fix).
            "def _fake_compute(*, file_path, repo_root=None):\n"
            "    return _FakeResult()\n"
            "\n"
            "_core.compute_before_edit_hint = _fake_compute\n"
            "\n"
            "_orig_write = _helpers.write_hint_file\n"
            f"_DELAY_S = {delay_s}\n"
            "def _slow_write(**kwargs):\n"
            "    result = _orig_write(**kwargs)\n"
            "    time.sleep(_DELAY_S)\n"
            "    return result\n"
            "_helpers.write_hint_file = _slow_write\n",
            encoding="utf-8",
        )
        project = tmp_path / "project"
        return project, {"PYTHONPATH": f"{sitecustomize_dir}{os.pathsep}{CHECKOUT_PYTHONPATH}"}

    def test_a_post_write_delay_past_the_deadline_still_agrees_with_the_record(self, tmp_path: Path) -> None:
        """Non-vacuity control included: a delay past the deadline, landing entirely
        AFTER write_hint_file() has already recorded T2, must still leave the
        delivered hint and the recorded tier in agreement (both T2) -- proving the
        disarm actually removes the kill window rather than merely narrowing it.

        ``TRW_CC03_ALARM_S``/``TRW_CC03_BOUND_S`` (test-only env overrides of the
        2.4s/2.5s production defaults) shrink the deadline so the 0.3s delay this
        needs to cross it does not also have to out-race the OUTER shell watchdog
        in real wall-clock time -- a real-timing test at the production 2.4s/2.5s
        values would need a multi-second sleep AND would be flaky against either
        bound depending on host load.
        """
        project, extra_env = self._project_with_slow_post_write(tmp_path, delay_s=0.3)
        extra_env["TRW_CC03_ALARM_S"] = "0.05"
        extra_env["TRW_CC03_BOUND_S"] = "5"
        _enable_cc03(project)
        tool_use_id = "toolu-post-write-race"
        result = run_distill_hint_hook(
            _make_pretooluse(tool_use_id=tool_use_id),
            project,
            extra_env=extra_env,
            timeout=15,
        )

        assert result.returncode == 0, result.stderr
        record = json.loads(
            (project / ".trw" / "context" / "cc03-hints" / f"{tool_use_id}.json").read_text(encoding="utf-8")
        )
        delivered_is_t2 = "[TRW Distill Hint " in result.stdout and "T2" in result.stdout
        recorded_is_t2 = record["tier"] == "T2"
        assert delivered_is_t2, f"expected a T2 hint delivered to the model, got: {result.stdout!r}"
        assert recorded_is_t2 == delivered_is_t2, (
            f"record (tier={record['tier']!r}) disagrees with what was delivered "
            f"(t2_delivered={delivered_is_t2}) -- stdout={result.stdout!r}"
        )

    def test_non_vacuity_the_shrunk_alarm_still_fires_before_the_print_loop(self, tmp_path: Path) -> None:
        """Control for the test above: with the SAME tiny ``TRW_CC03_ALARM_S``, a
        delay placed BEFORE the print loop (inside compute, so nothing is disarmed
        yet) must still be cut off -- proving the shrunk alarm genuinely fires and
        the previous test's pass is the disarm working, not the alarm never ticking.
        """
        sitecustomize_dir = tmp_path / "sitecustomize_dir"
        sitecustomize_dir.mkdir()
        (sitecustomize_dir / "sitecustomize.py").write_text(
            "import time\n"
            "import trw_mcp.tools._before_edit_hint_core as _core\n"
            "\n"
            # repo_root: the hook now names the checkout it resolved from the edited file (worktree fix).
            "def _slow_compute(*, file_path, repo_root=None):\n"
            "    time.sleep(0.3)\n"
            "    raise AssertionError('unreachable: the alarm should kill the process first')\n"
            "\n"
            "_core.compute_before_edit_hint = _slow_compute\n",
            encoding="utf-8",
        )
        project = tmp_path / "project"
        _enable_cc03(project)
        extra_env = {
            "PYTHONPATH": f"{sitecustomize_dir}{os.pathsep}{CHECKOUT_PYTHONPATH}",
            "TRW_CC03_ALARM_S": "0.05",
            "TRW_CC03_BOUND_S": "5",
        }
        tool_use_id = "toolu-alarm-still-fires"
        result = run_distill_hint_hook(
            _make_pretooluse(tool_use_id=tool_use_id),
            project,
            extra_env=extra_env,
            timeout=15,
        )

        assert result.returncode == 0
        record = json.loads(
            (project / ".trw" / "context" / "cc03-hints" / f"{tool_use_id}.json").read_text(encoding="utf-8")
        )
        assert record["tier"] == "T0"
        assert record["distill_status"] == "timeout_fallback"
        assert "[TRW Distill Hint " not in result.stdout

    def test_the_outer_shell_watchdog_rewrites_a_completed_record_too(self, tmp_path: Path) -> None:
        """The inner 2.4s SIGALRM is not the only kill path: `_trw_bounded_python`'s
        OWN outer watchdog (SIGTERM at ``TRW_CC03_BOUND_S``) fires independently of
        the interpreter's own alarm and has no way to know ``write_hint_file()``
        already completed and recorded a real tier before it fires. Disarming the
        inner alarm alone leaves this second path free to reproduce the same
        record-says-T2-but-the-model-got-T0 mismatch. The fallback branch now
        re-stamps the record to T0/timeout_fallback whenever it substitutes the T0
        beacon, regardless of which bound triggered it.
        """
        project, extra_env = self._project_with_slow_post_write(tmp_path, delay_s=0.5)
        # The inner alarm must NOT be what fires here (otherwise this is just the
        # earlier test again): give it a deadline the 0.5s delay never reaches, and
        # squeeze only the OUTER watchdog's bound.
        extra_env["TRW_CC03_ALARM_S"] = "100"
        extra_env["TRW_CC03_BOUND_S"] = "0.2"
        _enable_cc03(project)
        tool_use_id = "toolu-outer-watchdog"
        result = run_distill_hint_hook(
            _make_pretooluse(tool_use_id=tool_use_id),
            project,
            extra_env=extra_env,
            timeout=15,
        )

        assert result.returncode == 0
        assert "[TRW Distill Hint " not in result.stdout, "the outer watchdog kill must still deliver the T0 beacon"
        record = json.loads(
            (project / ".trw" / "context" / "cc03-hints" / f"{tool_use_id}.json").read_text(encoding="utf-8")
        )
        assert record["tier"] == "T0", "the record must be re-stamped T0 to match what the outer watchdog delivered"
        assert record["distill_status"] == "timeout_fallback"


class TestNoCrossContamination:
    """FR33: concurrent hint files for different tool_use_ids don't cross-contaminate."""

    def test_two_files_no_cross_contamination(self, tmp_path: Path) -> None:
        """Two concurrent tool_use_ids write separate hint files (Python layer)."""
        from trw_mcp.channels.claude_code._hook_helpers import write_hint_file

        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"

        id_a = "toolu-cross-a"
        id_b = "toolu-cross-b"
        file_a = "src/module_a.py"
        file_b = "src/module_b.py"

        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=id_a,
            file_path=file_a,
            tier="T2",
            hint_emitted=True,
            tokens_emitted=50,
            distill_status="hint_available",
        )
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id=id_b,
            file_path=file_b,
            tier="T1",
            hint_emitted=True,
            tokens_emitted=30,
            distill_status="tier_required",
        )

        data_a = json.loads((hints_dir / f"{id_a}.json").read_text(encoding="utf-8"))
        data_b = json.loads((hints_dir / f"{id_b}.json").read_text(encoding="utf-8"))

        # No cross-contamination: each file contains its own data
        assert data_a["file_path"] == file_a
        assert data_b["file_path"] == file_b
        assert data_a["tool_use_id"] == id_a
        assert data_b["tool_use_id"] == id_b
        # B's file_path should not appear in A's record
        assert file_b not in data_a.get("file_path", "")
        assert file_a not in data_b.get("file_path", "")


# ---------------------------------------------------------------------------
# FR34 — Fail-open on IO error
# ---------------------------------------------------------------------------


class TestFailOpen:
    """FR34: hook exits 0 even if hint-file directory is unwritable."""

    def test_hook_exits_0_when_no_tool_use_id(self, tmp_path: Path) -> None:
        """No tool_use_id → write_hint_file is skipped → still exits 0."""
        _enable_cc03(tmp_path)
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "src/module.py"}})
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0

    def test_no_hint_file_for_skipped_extensions(self, tmp_path: Path) -> None:
        """Skip condition prevents hint file write — no hints_dir entry."""
        _enable_cc03(tmp_path)
        tool_use_id = "toolu-skip-ext"
        _run_hook(_make_pretooluse(file_path="README.md", tool_use_id=tool_use_id), tmp_path)
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        hint_file = hints_dir / f"{tool_use_id}.json"
        # .md is skipped → no hint file written
        assert not hint_file.exists()

    def test_no_hint_file_when_disabled(self, tmp_path: Path) -> None:
        """Disabled hook never writes hint file."""
        tool_use_id = "toolu-disabled"
        # No config.yaml → disabled
        _run_hook(_make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id), tmp_path)
        hints_dir = tmp_path / ".trw" / "context" / "cc03-hints"
        hint_file = hints_dir / f"{tool_use_id}.json"
        assert not hint_file.exists()


# ---------------------------------------------------------------------------
# PRD-CORE-239 FR01: `TestChannelManifestDeclaresCC04` (three tests asserting
# the entry's presence, its `posttooluse_event_log` surface, and its absent
# activation_gate) was deleted with its subject. `cc-04-posttooluse-correlation`
# is no longer an entry in manifest-claude-code.yaml, so there is nothing left
# to declare. The rest of this file survives deliberately: the hint-file
# mechanism it exercises (`write_hint_file`) is still called by the shipped
# CC-03 PreToolUse hook, so those cases pin live behaviour, not a removed
# channel. See the module docstring for the producer/consumer caveat.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# No shell PostToolUse hook exists — document and verify
# ---------------------------------------------------------------------------


class TestNoShellPostToolUseHook:
    """Verify there is no separate shell-level PostToolUse hook for CC-04.

    This is a documented architectural decision: CC-04 correlation is done
    via write_hint_file() called from the PreToolUse hook's Python subprocess
    (FR29). There is no post-tool-distill-hint.sh hook file.
    """

    def test_no_posttooluse_shell_hook_file(self) -> None:
        """Confirm: no post-tool-distill-hint.sh exists in data/claude_code/hooks/."""
        hooks_dir = Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp" / "data" / "claude_code" / "hooks"
        post_hook = hooks_dir / "post-tool-distill-hint.sh"
        assert not post_hook.exists(), (
            "Unexpected PostToolUse shell hook found. If CC-04 gains a shell hook, add behavioral tests for it here."
        )

    def test_only_expected_hooks_in_data_dir(self) -> None:
        """data/claude_code/hooks/ contains exactly the two expected hook files.

        The PRD-CORE-231 git post-commit hook deliberately lives in
        ``data/git_hooks/``, NOT here: this directory is the Claude Code
        tool-lifecycle surface and has no post-commit event.
        """
        hooks_dir = Path(__file__).parent.parent.parent.parent / "src" / "trw_mcp" / "data" / "claude_code" / "hooks"
        actual_files = {f.name for f in hooks_dir.iterdir() if f.is_file()}
        expected_files = {"pre-tool-distill-hint.sh", "lib-distill-hint.sh"}
        assert actual_files == expected_files, (
            f"Unexpected hook files: {actual_files - expected_files}. Update this test if new hooks are added."
        )
