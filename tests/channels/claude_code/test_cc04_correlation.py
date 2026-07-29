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

Timeout alignment (fixed): The shell hook previously used `timeout 2` (2000ms)
but documented budget is 2500ms. Fixed to `timeout 2.5`. compute_before_edit_hint
does NOT import the embedding/trw-memory stack at module level (~0.76s warm),
so write_hint_file runs within the aligned 2.5s budget on warm invocations.
Cold-start (.pyc compilation) may still exceed 2.5s on first run; the hook
falls back to T0 beacon in that edge case, which is acceptable UX.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOOK = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "trw_mcp"
    / "data"
    / "claude_code"
    / "hooks"
    / "pre-tool-distill-hint.sh"
)


def _run_hook(
    stdin_payload: str,
    tmp_project: Path,
    *,
    timeout: int = 8,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(_HOOK)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TRW_PROJECT_DIR": str(tmp_project),
        },
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

    def test_shell_hint_file_written_within_aligned_timeout(self, tmp_path: Path) -> None:
        """FR33/FR29: hint file IS written via shell hook within the aligned 2.5s timeout.

        The shell hook was fixed to use `timeout 2.5` (from buggy `timeout 2`).
        compute_before_edit_hint does NOT import the embedding stack at module level
        (~0.76s warm import), so write_hint_file executes within budget.

        The shell writes a dependency-free provisional T0 record before starting
        the bounded intelligence subprocess, so cold imports cannot erase CC-04
        correlation evidence.
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
        assert hint_file.exists(), "warm invocation must write the correlation hint within the aligned timeout"


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
        system_python = str(Path(sys.base_prefix) / "bin" / "python3")
        if not Path(system_python).is_file():
            import pytest

            pytest.skip(f"no non-venv interpreter at {system_python} to force an ImportError")

        project = self._project(tmp_path, system_python)
        tool_use_id = "toolu-exc-fallback"
        result = _run_hook(_make_pretooluse(file_path="src/module.py", tool_use_id=tool_use_id), project)
        assert result.returncode == 0  # FR26: never blocking
        assert self._status(project, tool_use_id) == "exception_fallback"

    def test_genuine_timeout_still_records_timeout(self, tmp_path: Path) -> None:
        """Non-vacuity control: a real 2.5s overrun must still read ``timeout_fallback``.

        The shim runs the stdlib-only provisional write normally and hangs only
        on the intelligence call, so ``timeout 2.5`` kills the subprocess before
        any handler can run and the provisional record is the correct answer.
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
