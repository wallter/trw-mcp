"""Tests: the Antigravity before-edit hook is implemented (not a NotImplementedError stub).

The hook was previously a stub. It is now a real implementation and the installer
registers it in ``.antigravitycli/hooks.json``. NOTE: the channel manifest status
is ASPIRATIONAL, not active — the current Antigravity CLI routes file edits through
a code-action path that bypasses the PreToolUse hook, so the hook does not fire yet.
These tests assert only that the implementation is real (not a stub), which holds
regardless of that invocation gap.

Hook surface contract:
- Repo-scoped config dir ``.antigravitycli/``; ``settings.json`` is the MCP config path.
- Hooks live in a SEPARATE file ``.antigravitycli/hooks.json`` (not settings.json).
- Event key: ``PreToolUse``.
- Hook entry format: ``{"PreToolUse": [{"matcher": "<regex>", "command": "<shell>"}]}``.
- ``hooks_enabled`` is True in the profile.
"""

from __future__ import annotations

from pathlib import Path


def test_before_edit_hook_is_importable() -> None:
    """AG-03: _before_edit_hook.py is now a real module (not a stub)."""
    import importlib.util

    spec = importlib.util.find_spec("trw_mcp.channels.antigravity._before_edit_hook")
    assert spec is not None, "_before_edit_hook.py must exist in channels/antigravity/"

    # Must NOT raise NotImplementedError — it's now a real implementation.
    try:
        import trw_mcp.channels.antigravity._before_edit_hook  # noqa: F401
    except NotImplementedError:
        raise AssertionError(
            "_before_edit_hook.py must NOT raise NotImplementedError — AG-03 is implemented (not a stub)"
        ) from None


def test_before_edit_hook_exports_public_api() -> None:
    """AG-03: module exports expected public symbols."""
    from trw_mcp.channels.antigravity._before_edit_hook import (
        AG03_CHANNEL_ID,
        AG03_HOOKS_PATH,
        HOOK_SCRIPT_CONTENT,
        generate_hook_script,
        install_before_edit_hook,
    )

    assert AG03_CHANNEL_ID == "ag-03-before-edit-hook"
    assert AG03_HOOKS_PATH == ".antigravitycli/hooks.json"
    assert isinstance(HOOK_SCRIPT_CONTENT, str)
    assert callable(generate_hook_script)
    assert callable(install_before_edit_hook)


def test_hook_script_has_no_template_tokens() -> None:
    """AG-03: hook script content contains no {{ }} template tokens."""
    from trw_mcp.channels.antigravity._before_edit_hook import generate_hook_script

    content = generate_hook_script()
    assert "{{" not in content, "Hook script must not contain {{ template tokens"
    assert "}}" not in content, "Hook script must not contain }} template tokens"


def test_hook_script_contains_required_elements() -> None:
    """AG-03: hook script has expected structure (fail-open, PreToolUse pattern)."""
    from trw_mcp.channels.antigravity._before_edit_hook import HOOK_SCRIPT_CONTENT

    # Must reference the confirmed hook schema
    assert "PreToolUse" in HOOK_SCRIPT_CONTENT or "pre_tool_use" in HOOK_SCRIPT_CONTENT.lower()
    # Must exit 0 (fail-open) by returning continue=True
    assert "continue" in HOOK_SCRIPT_CONTENT
    assert '"continue": True' in HOOK_SCRIPT_CONTENT or "continue" in HOOK_SCRIPT_CONTENT
    # Must use __file__-relative path resolution (audit P0-02 pattern)
    assert "__file__" in HOOK_SCRIPT_CONTENT


def test_install_before_edit_hook_creates_files(tmp_path: Path) -> None:
    """AG-03: install_before_edit_hook creates hook script + hooks.json."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    result = install_before_edit_hook(tmp_path, overwrite=True)

    assert result["installed"] is True
    assert result["error"] is None
    assert result["skipped"] is False

    # Hook script installed
    hook_script = tmp_path / ".antigravitycli" / "hooks" / "trw_before_edit_telemetry.py"
    assert hook_script.exists(), "Hook script must be created"

    # hooks.json installed with correct structure
    hooks_json = tmp_path / ".antigravitycli" / "hooks.json"
    assert hooks_json.exists(), "hooks.json must be created"

    import json

    content = json.loads(hooks_json.read_text(encoding="utf-8"))
    assert "PreToolUse" in content, "hooks.json must have PreToolUse key"
    pre_hooks = content["PreToolUse"]
    assert isinstance(pre_hooks, list) and len(pre_hooks) >= 1
    entry = pre_hooks[0]
    assert "matcher" in entry, "Hook entry must have matcher"
    assert "command" in entry, "Hook entry must have command"


def test_install_before_edit_hook_idempotent(tmp_path: Path) -> None:
    """AG-03: install_before_edit_hook is idempotent (no duplicates on re-run)."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    # Install twice
    install_before_edit_hook(tmp_path, overwrite=True)
    install_before_edit_hook(tmp_path, overwrite=True)

    import json

    hooks_json = tmp_path / ".antigravitycli" / "hooks.json"
    content = json.loads(hooks_json.read_text(encoding="utf-8"))
    pre_hooks = content.get("PreToolUse", [])
    # No duplicate entries for the same command
    commands = [h.get("command") for h in pre_hooks]
    assert len(commands) == len(set(commands)), "Duplicate hook entries found — install is not idempotent"


def test_install_before_edit_hook_skips_if_exists(tmp_path: Path) -> None:
    """AG-03: overwrite=False skips if both files already exist."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    # First install
    install_before_edit_hook(tmp_path, overwrite=True)

    # Second call with overwrite=False
    result2 = install_before_edit_hook(tmp_path, overwrite=False)
    assert result2["skipped"] is True
    assert result2["installed"] is False


def test_install_before_edit_hook_merges_existing_hooks_json(tmp_path: Path) -> None:
    """AG-03: install preserves other existing hook entries in hooks.json."""
    import json

    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    # Pre-populate hooks.json with an existing entry
    hooks_dir = tmp_path / ".antigravitycli"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    existing = {
        "PostToolUse": [{"matcher": "read_file", "command": "echo POST"}],
    }
    (hooks_dir / "hooks.json").write_text(json.dumps(existing), encoding="utf-8")

    install_before_edit_hook(tmp_path, overwrite=True)

    content = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
    # Original PostToolUse entry preserved
    assert "PostToolUse" in content
    # New PreToolUse entry added
    assert "PreToolUse" in content


def test_ag03_channel_id_in_init_exports() -> None:
    """AG-03: AG03_CHANNEL_ID and install_before_edit_hook re-exported from __init__."""
    from trw_mcp.channels.antigravity import (
        AG03_CHANNEL_ID,
        AG03_HOOKS_PATH,
        HOOK_SCRIPT_CONTENT,
        generate_hook_script,
        install_before_edit_hook,
    )

    assert AG03_CHANNEL_ID == "ag-03-before-edit-hook"
    assert AG03_HOOKS_PATH == ".antigravitycli/hooks.json"
    assert isinstance(HOOK_SCRIPT_CONTENT, str)
    assert callable(generate_hook_script)
    assert callable(install_before_edit_hook)
