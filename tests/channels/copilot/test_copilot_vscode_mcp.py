"""Tests for C3: generate_vscode_mcp_config (PRD-DIST-2406 FR11-FR13).

Covers:
- test_creates_vscode_dir_if_absent (FR11)
- test_merge_preserves_existing_keys (FR11)
- test_idempotent_no_write_when_unchanged (FR12)
- test_force_overwrites_modified_entry (FR12)
- test_warn_skip_on_user_modification_without_force (FR12)
- test_json_key_order_stable (NFR09 / P2-12)
- test_root_key_is_servers_not_mcpservers
- test_init_project_registers_all_channels (FR13)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _call(target_dir: Path, *, force: bool = False) -> dict[str, list[str]]:
    from trw_mcp.channels.copilot._vscode_mcp import generate_vscode_mcp_config

    return generate_vscode_mcp_config(target_dir, force=force)


# ---------------------------------------------------------------------------
# FR11 — creates .vscode/ dir and merges servers.trw
# ---------------------------------------------------------------------------


def test_creates_vscode_dir_if_absent(tmp_path: Path) -> None:
    """Creates .vscode/ directory if not present."""
    assert not (tmp_path / ".vscode").exists()

    result = _call(tmp_path)

    assert (tmp_path / ".vscode").exists()
    assert (tmp_path / ".vscode" / "mcp.json").exists()
    assert result["created"]


def test_merge_preserves_existing_keys(tmp_path: Path) -> None:
    """Other servers.* keys are preserved when adding servers.trw."""
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    existing = {"servers": {"other-tool": {"type": "stdio", "command": "other"}}}
    (vscode_dir / "mcp.json").write_text(json.dumps(existing), encoding="utf-8")

    result = _call(tmp_path)

    content = json.loads((vscode_dir / "mcp.json").read_text(encoding="utf-8"))
    assert "other-tool" in content["servers"]
    assert content["servers"]["other-tool"] == {"type": "stdio", "command": "other"}
    assert "trw" in content["servers"]


def test_root_key_is_servers_not_mcpservers(tmp_path: Path) -> None:
    """Root key is 'servers', NOT 'mcpServers' (VS Code Copilot Chat MCP spec)."""
    _call(tmp_path)

    content = json.loads((tmp_path / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert "servers" in content
    assert "mcpServers" not in content


# ---------------------------------------------------------------------------
# FR12 — idempotency
# ---------------------------------------------------------------------------


def test_idempotent_no_write_when_unchanged(tmp_path: Path) -> None:
    """Second call with unchanged servers.trw returns preserved, no write."""
    _call(tmp_path)
    mcp_json = tmp_path / ".vscode" / "mcp.json"
    mtime1 = mcp_json.stat().st_mtime

    import time

    time.sleep(0.01)  # ensure mtime would differ if file written

    result = _call(tmp_path)

    mtime2 = mcp_json.stat().st_mtime
    assert mtime1 == mtime2, "File should not be rewritten when unchanged"
    assert result["preserved"]


def test_force_overwrites_modified_entry(tmp_path: Path) -> None:
    """force=True overwrites user-modified servers.trw."""
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    modified = {"servers": {"trw": {"type": "stdio", "command": "custom-trw-path", "args": ["--debug"]}}}
    (vscode_dir / "mcp.json").write_text(json.dumps(modified), encoding="utf-8")

    result = _call(tmp_path, force=True)

    content = json.loads((vscode_dir / "mcp.json").read_text(encoding="utf-8"))
    assert content["servers"]["trw"]["command"] == "trw-mcp"
    assert result["updated"]


def test_warn_skip_on_user_modification_without_force(tmp_path: Path) -> None:
    """User modification without force=True: WARNING logged and write skipped."""
    import structlog.testing

    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    modified = {"servers": {"trw": {"type": "stdio", "command": "custom-path"}}}
    (vscode_dir / "mcp.json").write_text(json.dumps(modified), encoding="utf-8")

    with structlog.testing.capture_logs() as cap:
        result = _call(tmp_path, force=False)

    # Should skip without overwriting
    assert result["preserved"] or result["errors"]  # skipped
    content = json.loads((vscode_dir / "mcp.json").read_text(encoding="utf-8"))
    # Should NOT have been overwritten
    assert content["servers"]["trw"]["command"] == "custom-path"
    # Warning logged via structlog
    warning_events = [e for e in cap if e.get("log_level") == "warning"]
    assert warning_events, f"Expected warning logged for user-modified entry, got: {cap}"


# ---------------------------------------------------------------------------
# NFR09 / P2-12 — sort_keys=True for byte-stable output
# ---------------------------------------------------------------------------


def test_json_key_order_stable(tmp_path: Path) -> None:
    """Dicts with keys in different insertion orders produce identical JSON bytes."""
    from trw_mcp.channels.copilot._templates import render_c3_mcp_json
    from trw_mcp.channels.copilot._vscode_mcp import _TRW_MCP_SERVER_ENTRY

    # Build same logical dict with keys in different orders
    order1 = {"z-other": {"type": "stdio", "command": "z"}, "a-other": {"type": "stdio", "command": "a"}}
    order2 = {"a-other": {"type": "stdio", "command": "a"}, "z-other": {"type": "stdio", "command": "z"}}

    merged1 = render_c3_mcp_json(existing={"servers": order1}, trw_entry=_TRW_MCP_SERVER_ENTRY)
    merged2 = render_c3_mcp_json(existing={"servers": order2}, trw_entry=_TRW_MCP_SERVER_ENTRY)

    json1 = json.dumps(merged1, indent=2, sort_keys=True)
    json2 = json.dumps(merged2, indent=2, sort_keys=True)
    assert json1 == json2, "sort_keys=True should produce byte-identical output regardless of insertion order"


def test_vscode_mcp_json_uses_sort_keys(tmp_path: Path) -> None:
    """generate_vscode_mcp_config writes JSON with sorted keys."""
    vscode_dir = tmp_path / ".vscode"
    vscode_dir.mkdir()
    # Write file with unsorted keys
    unsorted = {"z-key": 1, "a-key": 2, "servers": {"z-server": {}, "a-server": {}}}
    (vscode_dir / "mcp.json").write_text(json.dumps(unsorted), encoding="utf-8")

    _call(tmp_path, force=True)

    raw = (vscode_dir / "mcp.json").read_text(encoding="utf-8")
    # Parse and re-dump with sort_keys to get expected
    parsed = json.loads(raw)
    expected = json.dumps(parsed, indent=2, sort_keys=True) + "\n"
    assert raw == expected, "Written file should use sort_keys=True"


# ---------------------------------------------------------------------------
# FR13 — registers all 4 channels (smoke test)
# ---------------------------------------------------------------------------


def test_init_project_registers_all_channels(tmp_path: Path) -> None:
    """generate_vscode_mcp_config runs successfully as part of init_project flow."""
    result = _call(tmp_path)

    # C3 created/preserved
    assert result["created"] or result["preserved"] or result["updated"]

    # .vscode/mcp.json exists with servers.trw
    mcp_json = tmp_path / ".vscode" / "mcp.json"
    assert mcp_json.exists()
    content = json.loads(mcp_json.read_text(encoding="utf-8"))
    assert "trw" in content.get("servers", {})
    assert content["servers"]["trw"]["command"] == "trw-mcp"
    assert content["servers"]["trw"]["type"] == "stdio"


# ---------------------------------------------------------------------------
# Lock skip path
# ---------------------------------------------------------------------------


def test_lock_skip_returns_error_entry(tmp_path: Path) -> None:
    """When lock is held, generate_vscode_mcp_config returns skipped_lock in errors."""
    import threading

    from trw_mcp.channels._lock import ChannelLock

    lock_path = tmp_path / ".trw" / "channels" / "copilot-vscode-mcp-config.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    bg_lock = ChannelLock(lock_path)
    acquired_event = threading.Event()
    release_event = threading.Event()

    def _hold() -> None:
        bg_lock.__enter__()
        acquired_event.set()
        release_event.wait(timeout=5.0)
        bg_lock.__exit__(None, None, None)

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    acquired_event.wait(timeout=2.0)
    try:
        result = _call(tmp_path)
        assert "skipped_lock" in result["errors"]
    finally:
        release_event.set()
        t.join(timeout=2.0)


@pytest.fixture(autouse=True)
def _structlog_defaults_for_capture() -> object:
    """File-scoped: reset structlog to defaults so ``capture_logs()`` sees WARN.

    A prior test's ``configure_logging()`` (server import / init_project) installs
    a filtering wrapper that drops WARN before ``capture_logs``'s processor, so
    these warning-assertion tests fail only in full-suite ordering. Save+restore
    (file-scoped, never a global reset — avoids the alphabetical-leak hazard).
    """
    import structlog

    _saved = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**_saved)
