"""Behavioral contract for the portable dispatch/delegate skill."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._client_skills import render_skill_md
from trw_mcp.bootstrap._codex import _CODEX_SKILLS_DIR, install_codex_skills
from trw_mcp.bootstrap._copilot import _COPILOT_SKILLS_DIR, install_copilot_skills
from trw_mcp.bootstrap._cursor_ide import _IDE_CURATED_SKILLS
from trw_mcp.bootstrap._opencode import install_opencode_skills

from ._copilot_test_support import fake_git_repo  # noqa: F401

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
SKILL = DATA / "skills" / "trw-delegate" / "SKILL.md"
#: codex/copilot/opencode no longer fork this skill on disk (PRD-CORE-291-FR04)
#: -- they render the canonical body -- so "variants" are (rendered content,
#: label) pairs; ``copilot/plugin`` is a separate, still file-based, unwired
#: skill subset (no production reader) and stays a real path.
_CANONICAL_TEXT = SKILL.read_text(encoding="utf-8")
VARIANTS = (
    ("canonical", _CANONICAL_TEXT),
    ("codex", render_skill_md(_CANONICAL_TEXT, "codex")),
    ("copilot", render_skill_md(_CANONICAL_TEXT, "copilot")),
    ("opencode", render_skill_md(_CANONICAL_TEXT, "opencode")),
    (
        "copilot-plugin",
        (DATA / "copilot" / "plugin" / "skills" / "trw-delegate" / "SKILL.md").read_text(encoding="utf-8"),
    ),
)


def test_delegate_prefers_background_mcp_and_preserves_safety() -> None:
    content = SKILL.read_text(encoding="utf-8")
    for phrase in (
        "trw_dispatch(prompt=..., role=..., client=..., wait=False)",
        "trw_dispatch_status(job_id)",
        "wait=True` only for short work",
        "read_only=True",
        "reduced isolation must be explicit",
        "may still load project or user",
        "CLI as a fallback",
    ):
        assert phrase in content


def test_delegate_keeps_runtime_details_out_of_durable_policy() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "explicit selection,\nthen the selected role's mapping, then the configured default" in content
    for stale_detail in (
        "gpt-",
        "/tmp/",
        "CLAUDE.md",
        "bug #",
        "EOL",
        "--strict-mcp-config",
        "--ignore-user-config",
        "--dangerously-skip-permissions",
        "different coding agent",
    ):
        assert stale_detail not in content


@pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: v[0])
def test_delegate_variants_share_one_client_neutral_contract(variant: tuple[str, str]) -> None:
    _label, content = variant
    canonical_body = _CANONICAL_TEXT.split("---", 2)[2]
    assert content.split("---", 2)[2] == canonical_body
    assert "context: fork" not in content
    assert "agent: general-purpose" not in content


def test_delegate_installs_for_codex(tmp_path: Path) -> None:
    result = install_codex_skills(tmp_path)
    assert not result.get("errors")
    assert (tmp_path / _CODEX_SKILLS_DIR / "trw-delegate" / "SKILL.md").is_file()


def test_delegate_installs_for_copilot(fake_git_repo: Path) -> None:
    result = install_copilot_skills(fake_git_repo)
    assert not result["errors"]
    assert (fake_git_repo / _COPILOT_SKILLS_DIR / "trw-delegate" / "SKILL.md").is_file()


def test_delegate_installs_for_opencode(tmp_path: Path) -> None:
    result = install_opencode_skills(tmp_path)
    assert not result["errors"]
    assert (tmp_path / ".opencode" / "skills" / "trw-delegate" / "SKILL.md").is_file()


def test_delegate_is_curated_for_cursor() -> None:
    assert "trw-delegate" in _IDE_CURATED_SKILLS
