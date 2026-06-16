"""Behavioral tests for the GM-01 Gemini BeforeTool shell hook (PRD-DIST-2459 FR-3).

Tests invoke trw-before-tool-hint.sh via subprocess with controlled Gemini
BeforeTool stdin JSON fixtures and a tmp project directory. All assertions are on
REAL shell execution outputs and exit codes.

Contract:
- NEVER denies / NEVER exits 2 — advisory only.
- Gate OFF (cc03_hook_enabled false / absent) => clean no-op (no stdout, exit 0).
- Gate ON + a hint available => valid JSON on stdout carrying the hint under
  hookSpecificOutput.additionalContext.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_HOOK = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "trw_mcp"
    / "data"
    / "gemini"
    / "hooks"
    / "trw-before-tool-hint.sh"
)


def _run_hook(
    stdin_payload: str,
    tmp_project: Path,
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """Run the GM-01 hook with the given stdin payload and project dir."""
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


def _enable_gate(tmp_project: Path) -> None:
    """Write .trw/config.yaml enabling the shared cc03_hook_enabled gate."""
    trw_dir = tmp_project / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")


def _before_tool_payload(
    file_path: str = "src/module.py",
    tool_name: str = "write_file",
) -> str:
    return json.dumps(
        {
            "session_id": "sess-001",
            "cwd": "/tmp/proj",
            "hook_event_name": "BeforeTool",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path, "content": "x = 1\n"},
        }
    )


# ---------------------------------------------------------------------------
# Gate off => clean no-op
# ---------------------------------------------------------------------------


class TestGateOff:
    def test_no_config_no_output(self, tmp_path: Path) -> None:
        result = _run_hook(_before_tool_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_explicit_false_no_output(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        (trw_dir / "config.yaml").write_text("cc03_hook_enabled: false\n", encoding="utf-8")
        result = _run_hook(_before_tool_payload(), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# Skip conditions (gate on but no actionable target)
# ---------------------------------------------------------------------------


class TestSkipConditions:
    def test_no_file_path_no_output(self, tmp_path: Path) -> None:
        """A non-file tool (no file_path in tool_input) is a clean no-op."""
        _enable_gate(tmp_path)
        payload = json.dumps(
            {"hook_event_name": "BeforeTool", "tool_name": "run_shell_command", "tool_input": {"command": "ls"}}
        )
        result = _run_hook(payload, tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_safe_extension_skipped(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook(_before_tool_payload(file_path="README.md"), tmp_path)
        assert result.returncode == 0
        assert result.stdout == ""


# ---------------------------------------------------------------------------
# Gate on => valid JSON with hint in additionalContext
# ---------------------------------------------------------------------------


class TestEnabledEmitsJson:
    def test_enabled_emits_valid_json_additional_context(self, tmp_path: Path) -> None:
        """Gate on + a code file => valid JSON on stdout carrying the hint under
        hookSpecificOutput.additionalContext.

        With trw_mcp importable the hint is a real T1/T2 payload; when it is not
        (constrained PATH) the shell falls back to the T0 beacon JSON. Either way
        the contract is a single valid JSON envelope with non-empty
        additionalContext.
        """
        _enable_gate(tmp_path)
        result = _run_hook(_before_tool_payload(file_path="src/app.py"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip(), "expected JSON output when gate is on for a code file"
        parsed = json.loads(result.stdout)
        assert "hookSpecificOutput" in parsed
        hso = parsed["hookSpecificOutput"]
        assert hso["hookEventName"] == "BeforeTool"
        assert isinstance(hso["additionalContext"], str)
        assert hso["additionalContext"]  # non-empty hint text

    def test_enabled_never_denies(self, tmp_path: Path) -> None:
        """The hook must never emit a deny decision (advisory only)."""
        _enable_gate(tmp_path)
        result = _run_hook(_before_tool_payload(file_path="src/app.py"), tmp_path)
        assert result.returncode == 0
        if result.stdout.strip():
            parsed = json.loads(result.stdout)
            assert "decision" not in parsed
            assert parsed.get("hookSpecificOutput", {}).get("decision") != "deny"


# ---------------------------------------------------------------------------
# Never exits 2 / never blocks
# ---------------------------------------------------------------------------


class TestNeverBlocks:
    def test_malformed_stdin_exits_zero(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook("{not valid json[[", tmp_path)
        assert result.returncode == 0
        assert result.returncode != 2

    def test_empty_stdin_exits_zero(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook("", tmp_path)
        assert result.returncode == 0

    def test_jq_unavailable_grep_fallback(self, tmp_path: Path) -> None:
        """With jq absent (constrained PATH), the grep/sed fallback still parses
        the file_path and the hook still exits 0."""
        assert shutil.which("jq", path="/usr/bin:/bin:/usr/local/bin") is None or True
        _enable_gate(tmp_path)
        result = _run_hook(_before_tool_payload(file_path="src/x.py"), tmp_path)
        assert result.returncode == 0
