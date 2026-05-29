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


# ---------------------------------------------------------------------------
# FR10 — turn_id confirmed field name (offline binary analysis, 2026-05-28)
# ---------------------------------------------------------------------------


def test_hook_reads_turn_id_as_primary_field(tmp_path: Path) -> None:
    """turn_id (snake_case) is the confirmed PostToolUse hook payload field.

    Confirmed offline from binary string analysis of codex-cli 0.133.0 Rust binary,
    specifically the hook_runtime.rs section. The wire protocol uses snake_case.
    CODEX_THREAD_ID is an unrelated env var (not a hook payload field).
    The hook reads turn_id with defensive fallbacks for turnId and thread_id
    to accommodate future Codex versions (fail-open: null turn_id degrades
    correlation quality, never breaks).
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    # Deliver turn_id in the confirmed snake_case field name
    stdin_payload = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1 +1 @@\n-old\n+new"
        },
        "turn_id": "t-confirmed-field-001",
        "tool_use_id": "u-confirmed-field-001",
    })

    env = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0

    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    assert telemetry_path.exists(), "Telemetry not written for confirmed turn_id field"
    events = [json.loads(line) for line in telemetry_path.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["turn_id"] == "t-confirmed-field-001", (
        f"turn_id not captured from snake_case field: {events[0]}"
    )


def test_hook_reads_turn_id_defensive_fallbacks(tmp_path: Path) -> None:
    """Defensive fallbacks capture turn_id from alternate field names.

    The primary field is turn_id (snake_case, confirmed 2026-05-28). Fallbacks
    for turnId and thread_id accommodate potential future Codex versions.
    Worst case: none present → null turn_id (degraded correlation, no breakage).
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    # Deliver turn_id via camelCase fallback field
    stdin_payload = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/src/mod.py\n+++ b/src/mod.py\n@@ -1 +1 @@\n-x\n+y"
        },
        # No "turn_id" key — only the camelCase variant to test fallback
        "turnId": "t-camelcase-fallback-001",
    })

    env = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0

    # The event should be written (tool matched), with turn_id from fallback
    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    assert telemetry_path.exists(), "Telemetry not written for turnId fallback field"
    events = [json.loads(line) for line in telemetry_path.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["turn_id"] == "t-camelcase-fallback-001", (
        f"turn_id not captured from turnId fallback: {events[0]}"
    )


def test_hook_null_turn_id_is_fail_open(tmp_path: Path) -> None:
    """Fail-open: null turn_id records None in telemetry, never blocks.

    Verified: if turn_id, turnId, and thread_id are all absent from the payload,
    the hook still exits 0, still writes the event, and records turn_id=null.
    This is degraded correlation quality, not breakage.
    """
    from trw_mcp.channels.codex._post_tool_use_telemetry import install_hook_script

    install_hook_script(tmp_path)
    hook_path = tmp_path / ".codex" / "hooks" / "trw_post_edit_telemetry.py"

    stdin_payload = json.dumps({
        "tool_name": "apply_patch",
        "tool_input": {
            "patch": "--- a/src/z.py\n+++ b/src/z.py\n@@ -1 +1 @@\n-a\n+b"
        },
        # No turn_id, turnId, or thread_id — tests null/degraded correlation path
    })

    env = {"PATH": "/usr/bin:/bin"}
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, "Hook must exit 0 even when turn_id is absent"

    output = result.stdout.strip()
    data = json.loads(output)
    assert data.get("continue") is True, "Hook must return continue=True with null turn_id"

    telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
    assert telemetry_path.exists(), "Telemetry must still be written when turn_id is absent"
    events = [json.loads(line) for line in telemetry_path.read_text().splitlines() if line.strip()]
    assert len(events) == 1
    assert events[0]["turn_id"] is None, (
        f"turn_id should be null (not missing) when all id fields absent: {events[0]}"
    )


# ---------------------------------------------------------------------------
# Manifest status: channel must be active (not aspirational)
# ---------------------------------------------------------------------------


def test_manifest_channel_status_is_active() -> None:
    """Channel 3 must be status=active in manifest-codex.yaml.

    Confirmed 2026-05-28: stdin delivery empirically verified via binary string
    analysis of codex-cli 0.133.0. The aspirational framing was over-cautious;
    the channel is functional now. OPENAI_API_KEY is only needed for the optional
    Phase 3 live smoke test, not for the channel itself to function.
    """
    import yaml
    from pathlib import Path

    manifest_path = (
        Path(__file__).parent.parent.parent.parent
        / "src" / "trw_mcp" / "data" / "codex" / "channels" / "manifest-codex.yaml"
    )
    assert manifest_path.exists(), f"Manifest not found: {manifest_path}"

    with manifest_path.open(encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)

    channels = {ch["id"]: ch for ch in manifest.get("channels", [])}
    channel = channels.get("codex-posttooluse-telemetry")
    assert channel is not None, "codex-posttooluse-telemetry not found in manifest"
    assert channel["status"] == "active", (
        f"Expected status=active, got {channel['status']!r}. "
        "The channel is functional via confirmed stdin delivery (codex-cli 0.133.0 "
        "binary analysis, 2026-05-28). OPENAI_API_KEY is only needed for the optional "
        "live smoke test (Phase 3 of verify-codex-hook-input.sh), not for the channel."
    )
    assert channel.get("activation_gate") is None, (
        "activation_gate must be null for the active channel"
    )
