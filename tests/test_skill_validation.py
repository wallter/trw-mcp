"""Tests for SKILL.md validation in bootstrap skill installation.

PRD-QUAL-052 Finding #14: validate SKILL.md files before installing skills.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap._init_project import _validate_skill


@pytest.mark.unit
class TestValidateSkill:
    """Test _validate_skill with various SKILL.md states."""

    def test_valid_skill_returns_true(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill\n---\n# My Skill\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is True
        assert reason == ""

    def test_missing_skill_md_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "empty-skill"
        skill_dir.mkdir()
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "Missing SKILL.md" in reason

    def test_no_yaml_frontmatter_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "no-frontmatter"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "# Just a heading\nNo frontmatter here.\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "No YAML frontmatter" in reason

    def test_malformed_frontmatter_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "bad-frontmatter"
        skill_dir.mkdir()
        # Only one --- delimiter (no closing ---)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "Malformed YAML frontmatter" in reason

    def test_missing_name_field_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "no-name"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: A skill without a name\n---\n# Skill\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "Missing 'name'" in reason

    def test_missing_description_field_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "no-desc"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\n---\n# Skill\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "Missing 'description'" in reason

    def test_frontmatter_not_dict_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "list-frontmatter"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n- item1\n- item2\n---\n# Skill\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "not a dict" in reason

    def test_empty_name_returns_false(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "empty-name"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            '---\nname: ""\ndescription: Has a desc\n---\n# Skill\n',
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is False
        assert "Missing 'name'" in reason

    def test_valid_skill_with_extra_fields(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "extra-fields"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A skill\nmodel: claude-sonnet-4-6\n---\n# Skill\n",
            encoding="utf-8",
        )
        is_valid, reason = _validate_skill(skill_dir)
        assert is_valid is True
        assert reason == ""


@pytest.mark.unit
class TestInstallSkillsValidation:
    """Test that _install_skills skips invalid skills."""

    def test_install_skills_skips_invalid_skill(self, tmp_path: Path) -> None:
        """Invalid skill directories should not be copied."""
        from unittest.mock import patch

        target = tmp_path / "target"
        target.mkdir()

        # Create a fake data/skills source with one valid and one invalid skill
        fake_data = tmp_path / "fake_data"
        skills_src = fake_data / "skills"
        skills_src.mkdir(parents=True)

        # Valid skill
        valid_skill = skills_src / "good-skill"
        valid_skill.mkdir()
        (valid_skill / "SKILL.md").write_text(
            "---\nname: good-skill\ndescription: A good skill\n---\n# Good\n",
            encoding="utf-8",
        )

        # Invalid skill (no SKILL.md)
        invalid_skill = skills_src / "bad-skill"
        invalid_skill.mkdir()
        (invalid_skill / "README.md").write_text("# Bad Skill\n", encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

        with patch("trw_mcp.bootstrap._init_project._DATA_DIR", fake_data):
            from trw_mcp.bootstrap._init_project import _install_skills

            _install_skills(target, force=False, result=result)

        # Good skill should be installed
        assert (target / ".claude" / "skills" / "good-skill" / "SKILL.md").exists()
        # Bad skill directory should NOT be created
        assert not (target / ".claude" / "skills" / "bad-skill").exists()

    def test_install_skills_installs_all_valid_skills(self, tmp_path: Path) -> None:
        """All valid skills should be copied."""
        from unittest.mock import patch

        target = tmp_path / "target"
        target.mkdir()

        fake_data = tmp_path / "fake_data"
        skills_src = fake_data / "skills"
        skills_src.mkdir(parents=True)

        for name in ("skill-a", "skill-b"):
            sd = skills_src / name
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Skill {name}\n---\n# {name}\n",
                encoding="utf-8",
            )

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

        with patch("trw_mcp.bootstrap._init_project._DATA_DIR", fake_data):
            from trw_mcp.bootstrap._init_project import _install_skills

            _install_skills(target, force=False, result=result)

        assert (target / ".claude" / "skills" / "skill-a" / "SKILL.md").exists()
        assert (target / ".claude" / "skills" / "skill-b" / "SKILL.md").exists()
