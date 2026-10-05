"""Tests: the Antigravity before-edit hook is implemented (not a NotImplementedError stub).

The installer registers it in ``.agents/hooks.json`` (agy 1.2.x's named-hook schema) and writes its script
to ``.agents/hooks/``. Verified live on agy 1.2.15: the hook fires for ``write_to_file``. Surface contract:

- ``{"<name>": {"PreToolUse": [{"matcher": "<regex>", "hooks": [{"type": "command", "command": "<shell>"}]}]}}``
- the command runs with cwd = the directory holding ``hooks.json``;
- stdin is ``{"toolCall": {"name", "args": {"TargetFile"}}}``; the reply is ``{"decision": "allow"|"deny"}``.
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
        AG03_HOOKS_PATH,
        HOOK_SCRIPT_CONTENT,
        generate_hook_script,
        install_before_edit_hook,
    )

    assert AG03_HOOKS_PATH == ".agents/hooks.json"
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

    # Reads agy's camelCase payload and answers allow on every path (fail-open).
    assert "toolCall" in HOOK_SCRIPT_CONTENT and "TargetFile" in HOOK_SCRIPT_CONTENT
    assert '"decision": "allow"' in HOOK_SCRIPT_CONTENT
    # Must use __file__-relative path resolution (audit P0-02 pattern)
    assert "__file__" in HOOK_SCRIPT_CONTENT


def test_install_before_edit_hook_creates_files(tmp_path: Path) -> None:
    """AG-03: install_before_edit_hook creates the hook script + a named-hook hooks.json under .agents/."""
    import json

    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    result = install_before_edit_hook(tmp_path, overwrite=True)

    assert result["installed"] is True and result["error"] is None and result["skipped"] is False
    assert (tmp_path / ".agents" / "hooks" / "trw_before_edit_telemetry.py").exists()
    content = json.loads((tmp_path / ".agents" / "hooks.json").read_text(encoding="utf-8"))
    entry = content["trw-before-edit-telemetry"]["PreToolUse"][0]
    assert entry["matcher"] and entry["hooks"][0]["type"] == "command"
    assert not (tmp_path / ".antigravitycli").exists(), "agy 1.2.x does not read the legacy directory"


def test_install_before_edit_hook_idempotent(tmp_path: Path) -> None:
    """AG-03: a second install changes nothing (no duplicate entry, identical bytes)."""
    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    install_before_edit_hook(tmp_path, overwrite=True)
    first = (tmp_path / ".agents" / "hooks.json").read_bytes()
    again = install_before_edit_hook(tmp_path, overwrite=True)

    assert again["status"] == "unchanged"
    assert (tmp_path / ".agents" / "hooks.json").read_bytes() == first


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
    """AG-03: install preserves the user's other named hooks in hooks.json."""
    import json

    from trw_mcp.channels.antigravity._before_edit_hook import install_before_edit_hook

    hooks_dir = tmp_path / ".agents"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    mine = {"post-read": {"PostToolUse": [{"matcher": "read_file", "hooks": [{"command": "echo POST"}]}]}}
    (hooks_dir / "hooks.json").write_text(json.dumps(mine), encoding="utf-8")

    install_before_edit_hook(tmp_path, overwrite=True)

    content = json.loads((hooks_dir / "hooks.json").read_text(encoding="utf-8"))
    assert content["post-read"] == mine["post-read"]
    assert "trw-before-edit-telemetry" in content


def test_ag03_channel_id_in_init_exports() -> None:
    """AG-03: the hook installer surface is re-exported from __init__."""
    from trw_mcp.channels.antigravity import (
        AG03_HOOKS_PATH,
        HOOK_SCRIPT_CONTENT,
        generate_hook_script,
        install_before_edit_hook,
    )

    assert AG03_HOOKS_PATH == ".agents/hooks.json"
    assert isinstance(HOOK_SCRIPT_CONTENT, str)
    assert callable(generate_hook_script)
    assert callable(install_before_edit_hook)
