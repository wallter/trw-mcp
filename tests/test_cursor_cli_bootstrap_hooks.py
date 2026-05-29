"""Unit tests for cursor-cli hook bootstrap generators (PRD-CORE-137)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _read_hooks_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cursor" / "hooks.json").read_text())


_HOOKS_DATA_DIR = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "cursor"


def _run_hook(script_name: str, stdin_payload: str) -> subprocess.CompletedProcess[str]:
    """Run a cursor hook script with a JSON stdin payload and return result."""
    script = _HOOKS_DATA_DIR / script_name
    return subprocess.run(
        ["bash", str(script)],
        input=stdin_payload,
        capture_output=True,
        text=True,
    )


class TestCliHooksFiveEvents:
    """test_cli_hooks_subset_five_events."""

    def test_exactly_five_events(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        assert len(hooks["hooks"]) == 5

    def test_all_five_expected_events(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        expected = {
            "beforeShellExecution",
            "afterShellExecution",
            "beforeMCPExecution",
            "afterMCPExecution",
            "stop",
        }
        assert set(hooks["hooks"].keys()) == expected


class TestCliHooksFailClosed:
    """test_cli_hooks_fail_closed_security."""

    def test_before_shell_fail_closed_true(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["beforeShellExecution"]
        assert any(h.get("failClosed") is True for h in handlers)

    def test_before_mcp_fail_closed_true(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["beforeMCPExecution"]
        assert any(h.get("failClosed") is True for h in handlers)

    def test_after_shell_fail_closed_false(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["afterShellExecution"]
        assert all(h.get("failClosed") is False for h in handlers)

    def test_after_mcp_fail_closed_false(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["afterMCPExecution"]
        assert all(h.get("failClosed") is False for h in handlers)

    def test_stop_fail_closed_false(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        handlers = hooks["hooks"]["stop"]
        assert all(h.get("failClosed") is False for h in handlers)


class TestCliHooksNoIdeEvents:
    """test_cli_hooks_no_ide_events: IDE-only events absent from CLI hooks."""

    @pytest.mark.parametrize(
        "ide_event",
        [
            "beforeTabFileRead",
            "afterTabFileEdit",
            "subagentStart",
            "beforeSubmitPrompt",
        ],
    )
    def test_ide_event_absent(self, tmp_path: Path, ide_event: str) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        generate_cursor_cli_hooks(tmp_path)
        hooks = _read_hooks_json(tmp_path)
        assert ide_event not in hooks["hooks"], f"IDE-only event should not be present: {ide_event}"


class TestCliHooksPreservesIdeEvents:
    """test_cli_hooks_preserves_ide_events_when_dual."""

    def test_preserves_seeded_ide_events(self, tmp_path: Path) -> None:
        """If IDE already wrote 8 events, CLI pass preserves all of them."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        ide_hooks: dict = {
            "version": 1,
            "hooks": {
                "beforeTabFileRead": [{"command": "user-before-tab.sh", "type": "command"}],
                "afterTabFileEdit": [{"command": "user-after-tab.sh", "type": "command"}],
                "subagentStart": [{"command": "user-subagent.sh", "type": "command"}],
                "beforeSubmitPrompt": [{"command": "user-before-submit.sh", "type": "command"}],
                "afterAgentResponse": [{"command": "user-after-response.sh", "type": "command"}],
                "afterAgentThought": [{"command": "user-after-thought.sh", "type": "command"}],
                "preToolUse": [{"command": "user-pre-tool.sh", "type": "command"}],
                "postToolUse": [{"command": "user-post-tool.sh", "type": "command"}],
            },
        }
        (cursor_dir / "hooks.json").write_text(json.dumps(ide_hooks))

        generate_cursor_cli_hooks(tmp_path)
        merged = _read_hooks_json(tmp_path)
        assert "beforeShellExecution" in merged["hooks"]
        assert "afterMCPExecution" in merged["hooks"]
        assert "stop" in merged["hooks"]
        assert "beforeTabFileRead" in merged["hooks"]
        assert "afterTabFileEdit" in merged["hooks"]
        assert "subagentStart" in merged["hooks"]


class TestHookScriptSyntax:
    """Verify CLI hook scripts pass bash -n syntax check."""

    @pytest.mark.parametrize(
        "script_name",
        ["trw-after-mcp.sh", "trw-before-shell.sh", "trw-after-shell.sh"],
    )
    def test_bash_syntax(self, script_name: str) -> None:
        script = _HOOKS_DATA_DIR / script_name
        assert script.is_file(), f"Hook script missing: {script}"
        result = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"bash -n failed on {script_name}:\n{result.stderr}"


class TestHookScriptFunctional:
    """Functional tests: hook scripts emit valid JSON on stdout for representative inputs."""

    def test_after_mcp_emits_valid_json(self) -> None:
        """trw-after-mcp.sh emits valid JSON on stdout for a normal MCP payload."""
        payload = json.dumps({"tool_name": "trw_session_start", "result": "ok"})
        result = _run_hook("trw-after-mcp.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert isinstance(output, dict)

    def test_after_shell_emits_valid_json(self) -> None:
        """trw-after-shell.sh emits valid JSON on stdout for a normal shell payload."""
        payload = json.dumps({"command": "git status", "exit_code": 0, "duration_ms": 42})
        result = _run_hook("trw-after-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert isinstance(output, dict)

    def test_before_shell_allows_safe_command(self) -> None:
        """trw-before-shell.sh emits permission=allow for a safe command."""
        payload = json.dumps({"command": "git status"})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "allow"

    def test_before_shell_denies_secret_leak(self) -> None:
        """trw-before-shell.sh emits permission=deny when a secret token is found."""
        payload = json.dumps({"command": "echo API_KEY=sk-secret123"})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "deny"
        assert "user_message" in output

    def test_before_shell_allows_env_var_reference(self) -> None:
        """trw-before-shell.sh does NOT deny $API_KEY variable references (not a leak)."""
        payload = json.dumps({"command": "curl -H $API_KEY https://example.com"})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "allow", (
            "Variable reference $API_KEY should not trigger deny (no value after =)"
        )

    def test_before_shell_allows_empty_command(self) -> None:
        """trw-before-shell.sh allows empty/missing command field without crashing."""
        payload = json.dumps({})
        result = _run_hook("trw-before-shell.sh", payload)
        assert result.returncode == 0, f"Script exited non-zero:\n{result.stderr}"
        output = json.loads(result.stdout.strip())
        assert output.get("permission") == "allow"
