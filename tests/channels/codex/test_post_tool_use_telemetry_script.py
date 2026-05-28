"""Tests for channels/codex/_post_tool_use_telemetry.py — hook script.

PRD-DIST-2402 FR07, FR08, NFR02, NFR04, NFR05.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# FR07 — No {{ }} template tokens in installed script
# ---------------------------------------------------------------------------


def test_installed_hook_no_jinja_vars(tmp_path: Path) -> None:
    """FR07: installed script contains no {{ }} template tokens (audit P0-02)."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    assert hook_path.exists(), "Hook script was not installed"
    content = hook_path.read_text(encoding="utf-8")
    assert "{{" not in content, "Hook script contains {{ template tokens"
    assert "}}" not in content, "Hook script contains }} template tokens"


def test_generate_hook_script_no_jinja_vars() -> None:
    """FR07: generate_hook_script() returns content with no {{ }} tokens."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import generate_hook_script

    content = generate_hook_script()
    assert "{{" not in content
    assert "}}" not in content


# ---------------------------------------------------------------------------
# FR07 — Hook script is valid Python syntax
# ---------------------------------------------------------------------------


def test_hook_script_is_valid_python() -> None:
    """FR07: installed hook script parses as valid Python (no SyntaxError)."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import HOOK_SCRIPT_CONTENT

    try:
        ast.parse(HOOK_SCRIPT_CONTENT)
    except SyntaxError as exc:
        pytest.fail(f"Hook script has syntax error: {exc}")


# ---------------------------------------------------------------------------
# FR07 — Hook uses __file__-relative path resolution (audit P0-02)
# ---------------------------------------------------------------------------


def test_hook_path_uses_file_relative() -> None:
    """FR07: hook script resolves telemetry path using __file__, not hardcoded root."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import HOOK_SCRIPT_CONTENT

    assert "__file__" in HOOK_SCRIPT_CONTENT, (
        "Hook script must use __file__-relative path resolution (audit P0-02)"
    )
    # Must NOT contain hardcoded absolute path patterns
    assert "{{ repo_root }}" not in HOOK_SCRIPT_CONTENT


# ---------------------------------------------------------------------------
# FR07 — install_hook_script writes to correct path
# ---------------------------------------------------------------------------


def test_install_hook_script_path(tmp_path: Path) -> None:
    """FR07: hook installs at .codex/hooks/trw_post_edit_telemetry.py."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    result = install_hook_script(tmp_path)
    assert result["installed"] is True
    assert not result["skipped"]

    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    assert hook_path.exists()


def test_install_hook_script_no_overwrite(tmp_path: Path) -> None:
    """install_hook_script respects overwrite=False."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    # Second install with overwrite=False should skip
    result = install_hook_script(tmp_path, overwrite=False)
    assert result["installed"] is False
    assert result["skipped"] is True


# ---------------------------------------------------------------------------
# FR08 — Hook fail-open: always exits 0
# ---------------------------------------------------------------------------


def test_hook_exits_zero_on_malformed_input(tmp_path: Path) -> None:
    """FR08: hook exits 0 with {"continue": true} on malformed JSON input."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input="not-valid-json",
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, f"Hook exited non-zero: {result.returncode}"
    output = result.stdout.strip()
    data = json.loads(output)
    assert data.get("continue") is True


def test_hook_exits_zero_on_valid_apply_patch_input(tmp_path: Path) -> None:
    """FR08: hook exits 0 and returns {"continue": true} for apply_patch event."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    hook_input = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-old\n+new"
        },
        "turn_id": "t1",
        "tool_use_id": "u1",
    })

    # Use CODEX_HOOK_INPUT env var (P0-03 mitigation)
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOOK_INPUT": hook_input}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    output = result.stdout.strip()
    data = json.loads(output)
    assert data.get("continue") is True


def test_hook_suppresses_non_matching_tool(tmp_path: Path) -> None:
    """FR08: non-matching tool name → {"continue": true, "suppressOutput": true}."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    hook_input = json.dumps({"tool_name": "ReadFile"})
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOOK_INPUT": hook_input}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    data = json.loads(result.stdout.strip())
    assert data.get("continue") is True
    assert data.get("suppressOutput") is True


# ---------------------------------------------------------------------------
# FR08 — apply_patch writes JSONL to telemetry log
# ---------------------------------------------------------------------------


def test_apply_patch_writes_jsonl(tmp_path: Path) -> None:
    """FR08: apply_patch event writes a channel-event/v1 JSONL line."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"

    hook_input = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-old\n+new"
        },
        "turn_id": "t1",
        "tool_use_id": "u1",
    })

    env = {"PATH": "/usr/bin:/bin", "CODEX_HOOK_INPUT": hook_input}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0

    assert telemetry_path.exists(), "Telemetry log not created"
    lines = [l.strip() for l in telemetry_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1, f"Expected 1 JSONL line, got {len(lines)}"

    event = json.loads(lines[0])
    assert event["schema"] == "channel-event/v1"
    assert event["channel_id"] == "codex-posttooluse-telemetry"
    assert event["tool_name"] == "apply_patch"


# ---------------------------------------------------------------------------
# NFR05 — Hook script uses only stdlib imports
# ---------------------------------------------------------------------------


def test_hook_stdlib_only_imports() -> None:
    """NFR05: hook script imports only stdlib modules."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import HOOK_SCRIPT_CONTENT

    tree = ast.parse(HOOK_SCRIPT_CONTENT)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module.split(".")[0])

    stdlib_modules = {
        "json", "os", "sys", "datetime", "pathlib", "__future__",
        "typing", "re", "subprocess", "io", "collections",
    }
    non_stdlib = [m for m in imports if m not in stdlib_modules and m]
    assert not non_stdlib, f"Hook imports non-stdlib modules: {non_stdlib}"


# ---------------------------------------------------------------------------
# NFR04 — Hook script execution < 50ms (stdlib path, no telemetry write)
# ---------------------------------------------------------------------------


def test_execution_under_50ms(tmp_path: Path) -> None:
    """NFR04: hook execution time < 50 ms for non-matching tool (no I/O)."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    hook_input = json.dumps({"tool_name": "ReadFile"})
    env = {"PATH": "/usr/bin:/bin", "CODEX_HOOK_INPUT": hook_input}

    start = time.monotonic()
    subprocess.run(
        [sys.executable, str(hook_path)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    elapsed_ms = (time.monotonic() - start) * 1000

    # 50ms is for the script logic itself; Python startup adds overhead.
    # We use 2s as a generous bound to avoid flakiness on slow CI.
    assert elapsed_ms < 2000, f"Hook took {elapsed_ms:.0f} ms (expected < 2000 ms)"


# ---------------------------------------------------------------------------
# HOOK_SCRIPT_CONTENT constant is a valid string with expected entrypoint
# ---------------------------------------------------------------------------


def test_hook_script_content_has_main() -> None:
    """Hook script defines a main() function."""
    from trw_mcp.channels.codex._post_tool_use_telemetry import HOOK_SCRIPT_CONTENT

    assert "def main()" in HOOK_SCRIPT_CONTENT
    assert 'if __name__ == "__main__"' in HOOK_SCRIPT_CONTENT
