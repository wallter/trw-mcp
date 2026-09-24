"""PRD-INFRA-192 FR09 (C7): ``.claude/**`` and ``.mcp.json`` are claude-code's
own install/update surfaces, not core ones written for every client -- except
``.claude/hooks``, shared with codex and copilot because their own generated
hook commands run scripts from there.

Covers the requirement-numbered scenarios from the PRD that are not otherwise
pinned by ``tests/test_uninstall.py`` (uninstall-side) or
``tests/test_bootstrap_codex_split.py`` (the codex-only install shape):

(b) an explicit non-claude, non-hook-sharing client creates neither ``.claude/``
    nor ``.mcp.json``.
(d) update-project leaves a recorded-elsewhere project's leftover ``.claude/``
    tree and ``.mcp.json`` byte-identical.
(e) a bare init still writes every Claude Code surface and records
    ``claude-code`` in ``target_platforms``.
(g) a derived guard: every client whose GENERATED hook commands reference
    ``.claude/hooks`` is declared as an owner of that path in the catalog.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project


@pytest.mark.integration
def test_explicit_opencode_init_creates_no_claude_or_mcp_json(tmp_path: Path) -> None:
    """(b) init --ide opencode creates no .claude/ and no .mcp.json."""
    (tmp_path / ".git").mkdir()

    result = init_project(tmp_path, ide="opencode")

    assert not result["errors"], result["errors"]
    assert not (tmp_path / ".claude").exists(), "opencode does not read .claude/ at all"
    assert not (tmp_path / ".mcp.json").exists(), "opencode does not read project-root .mcp.json"
    assert (tmp_path / ".opencode").exists(), "opencode's own surface must still be created"


@pytest.mark.integration
def test_update_project_leaves_an_unowned_leftover_claude_tree_byte_identical(tmp_path: Path) -> None:
    """(d) a project recorded as [opencode] with a leftover .claude/ tree and a
    .mcp.json carrying a trw entry (simulating an older install) is left
    byte-identical by update-project -- it neither refreshes nor deletes them.
    """
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="opencode")
    assert not result["errors"], result["errors"]

    claude_dir = tmp_path / ".claude"
    hooks_dir = claude_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    leftover_hook = hooks_dir / "session-start.sh"
    leftover_hook.write_text("#!/bin/sh\necho leftover\n", encoding="utf-8")
    settings = claude_dir / "settings.json"
    settings.write_text('{"hooks": {}}', encoding="utf-8")
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({"mcpServers": {"trw": {"command": "trw-mcp"}}}), encoding="utf-8")

    before_hook = leftover_hook.read_bytes()
    before_settings = settings.read_bytes()
    before_mcp = mcp_json.read_bytes()

    update_result = update_project(tmp_path)
    assert not update_result["errors"], update_result["errors"]

    assert leftover_hook.read_bytes() == before_hook, "an unowned .claude/hooks script must not be refreshed"
    assert settings.read_bytes() == before_settings, "an unowned .claude/settings.json must not be merged into"
    assert mcp_json.read_bytes() == before_mcp, "an unowned .mcp.json must not be merged into"
    assert str(claude_dir) not in "\n".join(update_result.get("cleaned", [])), (
        "an unowned leftover .claude/ tree must not be deleted either"
    )


@pytest.mark.integration
def test_bare_init_writes_claude_code_surfaces_and_records_it(tmp_path: Path) -> None:
    """(e) a bare init (no --ide, no on-disk client markers) still writes every
    Claude Code surface and records claude-code in target_platforms.
    """
    import yaml

    (tmp_path / ".git").mkdir()

    result = init_project(tmp_path)

    assert not result["errors"], result["errors"]
    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert (tmp_path / ".mcp.json").is_file()
    assert list((tmp_path / ".claude" / "hooks").glob("*.sh"))
    assert list((tmp_path / ".claude" / "skills").iterdir())
    assert (tmp_path / "CLAUDE.md").is_file()

    config = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8")) or {}
    assert "claude-code" in (config.get("target_platforms") or [])


@pytest.mark.integration
def test_bare_init_with_another_on_disk_marker_still_records_claude_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(e) regression for the ``kept or [...]`` -> ``kept + [...]`` fix.

    A bare init on a project that already carries an on-disk codex marker
    still scaffolds the Claude Code surfaces unconditionally (CORE262-13), so
    it must record claude-code too -- ``kept or ["claude-code"]`` dropped
    claude-code from ``target_platforms`` whenever detection found ANY other
    on-disk client, even though the scaffold write happened regardless.
    """
    import shutil as _shutil

    import yaml

    from trw_mcp.bootstrap import _utils

    original_which = _shutil.which

    def _which_filtered(cmd: str, *args: object, **kwargs: object) -> str | None:
        if cmd in {"cursor", "cursor-agent"}:
            return None
        return original_which(cmd, *args, **kwargs)

    monkeypatch.setattr(_utils.shutil, "which", _which_filtered)
    monkeypatch.delenv("CURSOR_TRACE_ID", raising=False)
    monkeypatch.delenv("CURSOR_SESSION_ID", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)

    (tmp_path / ".git").mkdir()
    (tmp_path / ".codex").mkdir()

    result = init_project(tmp_path)  # bare -- no ide= override
    assert not result["errors"], result["errors"]
    assert (tmp_path / ".claude" / "settings.json").is_file(), "precondition: the scaffold write still happens"

    config = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8")) or {}
    assert "claude-code" in (config.get("target_platforms") or []), (
        "claude-code must be recorded whenever its scaffold was written, even alongside a detected codex marker"
    )


@pytest.mark.unit
def test_every_client_whose_generated_hooks_reference_claude_hooks_declares_it() -> None:
    """(g) derived guard: read ownership from the REAL hook-command builders,
    never a hand-maintained list of "clients that share .claude/hooks"."""
    from trw_mcp.bootstrap._codex_hooks import _codex_hooks_payload
    from trw_mcp.bootstrap._copilot import _copilot_hooks_payload
    from trw_mcp.client_profiles.catalog import client_scaffold_relpaths

    builders = {
        "codex": _codex_hooks_payload,
        "copilot": _copilot_hooks_payload,
    }
    checked = 0
    for client_id, build in builders.items():
        payload = build()
        references_claude_hooks = ".claude/hooks/" in json.dumps(payload)
        assert references_claude_hooks, (
            f"precondition: {client_id}'s hook builder was expected to reference .claude/hooks/"
        )
        checked += 1
        assert ".claude/hooks" in client_scaffold_relpaths(client_id), (
            f"{client_id}'s generated hook commands run scripts from .claude/hooks/, "
            "but the catalog does not declare that client as an owner of the path -- "
            "an install/uninstall for this client would leave a dangling reference or "
            "delete a directory its own hooks.json still points into."
        )
    assert checked == len(builders), "the derived guard must have actually run against both builders"
