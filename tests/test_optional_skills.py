"""Opt-in skills (trw-assess) reach every client with a skill surface, and only while enabled.

Covers the five installers that ship skills (Claude Code, Codex, Copilot, Cursor IDE, OpenCode):
off by default, installed when the operator turns the feature on, and retired on the next run once
it is off again, unless the installed copy was edited. A client-rendered copy (Codex drops frontmatter
keys) counts as pristine when it matches that client's render, not the canonical bytes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import TRWConfig

_DESTS = {
    "claude": ".claude/skills/trw-assess",
    "codex": ".agents/skills/trw-assess",
    "copilot": ".github/skills/trw-assess",
    "cursor": ".cursor/skills/trw-assess",
    "opencode": ".opencode/skills/trw-assess",
}


def _set_flag(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    config = TRWConfig(assess_enabled=enabled)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)


def _install(target: Path) -> dict[str, dict[str, Any]]:
    from trw_mcp.bootstrap._codex import install_codex_skills
    from trw_mcp.bootstrap._copilot import install_copilot_skills
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills
    from trw_mcp.bootstrap._init_project_skills import _install_skills
    from trw_mcp.bootstrap._opencode import install_opencode_skills

    claude: dict[str, Any] = {"created": [], "updated": [], "preserved": [], "skipped": [], "errors": []}
    _install_skills(target, False, claude)
    return {
        "claude": claude,
        "codex": dict(install_codex_skills(target)),
        "copilot": install_copilot_skills(target),
        "cursor": dict(generate_cursor_ide_skills(target)),
        "opencode": install_opencode_skills(target),
    }


def test_off_by_default_no_client_gets_the_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_flag(monkeypatch, False)
    _install(tmp_path)
    for rel in _DESTS.values():
        assert not (tmp_path / rel).exists(), rel


def test_enabled_every_skill_capable_client_gets_the_same_canonical_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap._client_skills import render_skill_md
    from trw_mcp.bootstrap._init_project_skills import _data_dir

    _set_flag(monkeypatch, True)
    results = _install(tmp_path)
    canonical_text = (_data_dir() / "skills" / "trw-assess" / "SKILL.md").read_text(encoding="utf-8")
    # codex/opencode render the canonical body with a reduced frontmatter
    # (PRD-CORE-291-FR04); claude and cursor keep it unmodified.
    expected = {
        "claude": canonical_text.encode("utf-8"),
        "codex": render_skill_md(canonical_text, "codex").encode("utf-8"),
        "copilot": render_skill_md(canonical_text, "copilot").encode("utf-8"),
        "cursor": canonical_text.encode("utf-8"),
        "opencode": render_skill_md(canonical_text, "opencode").encode("utf-8"),
    }
    for client, rel in _DESTS.items():
        assert (tmp_path / rel / "SKILL.md").read_bytes() == expected[client], client
        assert not results[client].get("errors"), (client, results[client].get("errors"))


def test_turning_it_off_retires_pristine_copies_and_keeps_edited_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_flag(monkeypatch, True)
    _install(tmp_path)
    edited = tmp_path / _DESTS["cursor"] / "SKILL.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nlocal note\n", encoding="utf-8")

    _set_flag(monkeypatch, False)
    results = _install(tmp_path)

    for client in ("claude", "codex", "copilot", "opencode"):
        assert not (tmp_path / _DESTS[client]).exists(), client
        assert any(r.endswith("trw-assess") for r in results[client].get("removed", [])), client
    assert edited.is_file()
    assert any(r.endswith("trw-assess") for r in results["cursor"]["preserved"])


def test_unreadable_config_keeps_the_opt_in_skill_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._optional_skills import skill_enabled

    def _broken() -> TRWConfig:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("trw_mcp.models.config.get_config", _broken)
    assert skill_enabled("trw-assess") is False
    assert skill_enabled("trw-deliver") is True
