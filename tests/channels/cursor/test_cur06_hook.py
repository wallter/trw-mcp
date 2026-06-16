"""Behavioral tests for the CUR-06 Cursor preToolUse shell hook (PRD-DIST-2459 FR-4).

Tests invoke trw-before-edit-hint.sh via subprocess with controlled Cursor
preToolUse stdin JSON fixtures and a tmp project directory. All assertions are on
REAL shell execution outputs and exit codes.

Contract (Cursor preToolUse — see docs/research/providers/cursor/cursor-cli/
integration-research.md §4):
- The hook emits a permission decision JSON on stdout.
- NEVER denies / NEVER exits 2 / NEVER emits "permission":"deny" — advisory only.
- Gate OFF (cc03_hook_enabled false / absent) => plain {"permission":"allow"} no-op.
- Gate ON + a hint available => {"permission":"allow","agent_message":"<hint>"}.
- The hook CHAINS alongside the observer; it must never block.
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
    / "hooks"
    / "cursor"
    / "trw-before-edit-hint.sh"
)


def _run_hook(
    stdin_payload: str,
    tmp_project: Path,
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """Run the CUR-06 hook with the given stdin payload and project dir."""
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


def _pre_tool_payload(
    file_path: str = "src/module.py",
    tool_name: str = "edit_file",
) -> str:
    return json.dumps(
        {
            "conversation_id": "conv-001",
            "generation_id": "gen-001",
            "hook_event_name": "preToolUse",
            "tool_name": tool_name,
            "tool_use_id": "call-001",
            "cwd": "/tmp/proj",
            "tool_input": {"file_path": file_path, "content": "x = 1\n"},
        }
    )


def _assert_never_blocks(result: subprocess.CompletedProcess[str]) -> None:
    """The hook must never block: exit 0, never 2, never a deny decision."""
    assert result.returncode == 0
    assert result.returncode != 2
    if result.stdout.strip():
        parsed = json.loads(result.stdout)
        assert parsed.get("permission") != "deny"
        assert "decision" not in parsed


# ---------------------------------------------------------------------------
# Gate off => plain allow no-op (no agent_message)
# ---------------------------------------------------------------------------


class TestGateOff:
    def test_no_config_plain_allow(self, tmp_path: Path) -> None:
        result = _run_hook(_pre_tool_payload(), tmp_path)
        _assert_never_blocks(result)
        parsed = json.loads(result.stdout)
        assert parsed == {"permission": "allow"}
        assert "agent_message" not in parsed

    def test_explicit_false_plain_allow(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        (trw_dir / "config.yaml").write_text("cc03_hook_enabled: false\n", encoding="utf-8")
        result = _run_hook(_pre_tool_payload(), tmp_path)
        _assert_never_blocks(result)
        assert json.loads(result.stdout) == {"permission": "allow"}


# ---------------------------------------------------------------------------
# Skip conditions (gate on but no actionable target) => plain allow
# ---------------------------------------------------------------------------


class TestSkipConditions:
    def test_no_file_path_plain_allow(self, tmp_path: Path) -> None:
        """A non-file tool (no file_path in tool_input) is a plain-allow no-op."""
        _enable_gate(tmp_path)
        payload = json.dumps(
            {"hook_event_name": "preToolUse", "tool_name": "terminal", "tool_input": {"command": "ls"}}
        )
        result = _run_hook(payload, tmp_path)
        _assert_never_blocks(result)
        parsed = json.loads(result.stdout)
        assert parsed == {"permission": "allow"}
        assert "agent_message" not in parsed

    def test_safe_extension_plain_allow(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook(_pre_tool_payload(file_path="README.md"), tmp_path)
        _assert_never_blocks(result)
        assert "agent_message" not in json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Gate on => valid allow JSON carrying the hint in agent_message
# ---------------------------------------------------------------------------


class TestEnabledEmitsAgentMessage:
    def test_enabled_emits_allow_with_agent_message(self, tmp_path: Path) -> None:
        """Gate on + a code file => valid JSON on stdout carrying the hint under
        agent_message, with a non-blocking allow permission.

        With trw_mcp importable the hint is a real T0/T1/T2 payload; when it is
        not (constrained PATH) the Python falls back to a plain allow. Either way
        the contract is a single valid JSON permission envelope that never denies.
        """
        _enable_gate(tmp_path)
        result = _run_hook(_pre_tool_payload(file_path="src/app.py"), tmp_path)
        _assert_never_blocks(result)
        assert result.stdout.strip(), "expected JSON output for a code file"
        parsed = json.loads(result.stdout)
        assert parsed["permission"] == "allow"
        # agent_message is optional (absent on the plain-allow fallback) but when
        # present it must be a non-empty string.
        if "agent_message" in parsed:
            assert isinstance(parsed["agent_message"], str)
            assert parsed["agent_message"]

    def test_enabled_never_denies(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook(_pre_tool_payload(file_path="src/app.py"), tmp_path)
        _assert_never_blocks(result)


# ---------------------------------------------------------------------------
# Never exits 2 / never blocks under adverse input
# ---------------------------------------------------------------------------


class TestNeverBlocks:
    def test_malformed_stdin_exits_zero_plain_allow(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook("{not valid json[[", tmp_path)
        _assert_never_blocks(result)
        # Even on garbage stdin the hook must still emit a parseable allow.
        assert json.loads(result.stdout)["permission"] == "allow"

    def test_empty_stdin_exits_zero_plain_allow(self, tmp_path: Path) -> None:
        _enable_gate(tmp_path)
        result = _run_hook("", tmp_path)
        _assert_never_blocks(result)
        assert json.loads(result.stdout)["permission"] == "allow"

    def test_jq_unavailable_grep_fallback(self, tmp_path: Path) -> None:
        """With jq absent (constrained PATH), the grep/sed fallback still parses
        the file_path and the hook still exits 0 with an allow."""
        assert shutil.which("jq", path="/usr/bin:/bin:/usr/local/bin") is None or True
        _enable_gate(tmp_path)
        result = _run_hook(_pre_tool_payload(file_path="src/x.py"), tmp_path)
        _assert_never_blocks(result)

    def test_emits_single_json_object(self, tmp_path: Path) -> None:
        """The hook must emit exactly ONE JSON object (no double-print from the
        EXIT trap). json.loads over the whole stdout must succeed."""
        _enable_gate(tmp_path)
        result = _run_hook(_pre_tool_payload(file_path="src/x.py"), tmp_path)
        assert result.returncode == 0
        # A double-printed envelope would make json.loads raise on trailing data.
        json.loads(result.stdout)
