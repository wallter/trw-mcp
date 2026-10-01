"""Split bootstrap update migration and scoped cleanup tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import update_project

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def _record(repo: Path, key: str, text: str) -> None:
    """Record *text* as what TRW last wrote under *key* — the proof every sweep needs (PRD-INFRA-190-FR06)."""
    import hashlib

    from trw_mcp.state.persistence import FileStateReader, FileStateWriter

    manifest_path = repo / ".trw" / "managed-artifacts.yaml"
    manifest = FileStateReader().read_yaml(manifest_path)
    manifest["content_hashes"][key] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    FileStateWriter().write_yaml(manifest_path, manifest)


class TestUpdatePrefixScopedCleanup:
    """Test that _remove_stale_artifacts only removes trw- prefixed items."""

    def test_custom_skill_without_trw_prefix_survives(self, initialized_repo: Path) -> None:
        """Custom skill without trw- prefix survives update_project()."""
        # Add a non-trw-prefixed skill to the manifest (simulate pre-migration)
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        skills_list = list(manifest.get("skills", []))
        skills_list.append("my-custom-skill")
        manifest["skills"] = skills_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        custom_skill = initialized_repo / ".claude" / "skills" / "my-custom-skill"
        custom_skill.mkdir(parents=True, exist_ok=True)
        (custom_skill / "SKILL.md").write_text("custom content", encoding="utf-8")

        update_project(initialized_repo)

        # Non-trw-prefixed skill should survive even if not in current bundle
        assert custom_skill.exists()
        assert (custom_skill / "SKILL.md").read_text(encoding="utf-8") == "custom content"

    def test_custom_agent_without_trw_prefix_survives(self, initialized_repo: Path) -> None:
        """Custom agent without trw- prefix survives update_project()."""
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        agents_list = list(manifest.get("agents", []))
        agents_list.append("my-custom-agent.md")
        manifest["agents"] = agents_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        custom_agent = initialized_repo / ".claude" / "agents" / "my-custom-agent.md"
        custom_agent.write_text("custom agent content", encoding="utf-8")

        update_project(initialized_repo)

        # Non-trw-prefixed agent should survive even if not in current bundle
        assert custom_agent.exists()
        assert custom_agent.read_text(encoding="utf-8") == "custom agent content"

    def test_stale_trw_skill_is_removed(self, initialized_repo: Path) -> None:
        """Stale trw-prefixed skill IS removed by update_project()."""
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        skills_list = list(manifest.get("skills", []))
        skills_list.append("trw-deprecated-skill")
        manifest["skills"] = skills_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        stale_skill = initialized_repo / ".claude" / "skills" / "trw-deprecated-skill"
        stale_skill.mkdir(parents=True, exist_ok=True)
        (stale_skill / "SKILL.md").write_text("deprecated", encoding="utf-8")
        _record(initialized_repo, "trw-deprecated-skill/SKILL.md", "deprecated")

        update_project(initialized_repo)

        assert not stale_skill.exists()

    def test_stale_trw_agent_is_removed(self, initialized_repo: Path) -> None:
        """Stale trw-prefixed agent IS removed by update_project()."""
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        agents_list = list(manifest.get("agents", []))
        agents_list.append("trw-deprecated-agent.md")
        manifest["agents"] = agents_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        stale_agent = initialized_repo / ".claude" / "agents" / "trw-deprecated-agent.md"
        stale_agent.write_text("deprecated agent", encoding="utf-8")
        _record(initialized_repo, "trw-deprecated-agent.md", "deprecated agent")

        update_project(initialized_repo)

        assert not stale_agent.exists()


class TestPrefixMigration:
    """Retirement through update_project (REMOVE-S8a: disk-driven ``trw-*`` retirement, no name list)."""

    def test_a_pre_prefix_skill_name_is_no_longer_migrated(self, initialized_repo: Path) -> None:
        """REMOVE-S1: the PRD-FIX-032 rename entries are gone, so ``learn`` is the project's own skill."""
        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "learn").mkdir(parents=True, exist_ok=True)
        (skills_dir / "learn" / "SKILL.md").write_text("old", encoding="utf-8")
        _record(initialized_repo, "learn/SKILL.md", "old")

        result = update_project(initialized_repo)

        assert (skills_dir / "learn" / "SKILL.md").read_text(encoding="utf-8") == "old"
        assert not [e for e in result["cleaned"] if "/learn/" in e]

    def test_migrate_removes_a_retired_agent(self, initialized_repo: Path) -> None:
        """A TRW-recorded ``trw-*`` agent the bundle no longer ships is removed."""
        agents_dir = initialized_repo / ".claude" / "agents"
        (agents_dir / "trw-tester.md").write_text("old", encoding="utf-8")
        _record(initialized_repo, "trw-tester.md", "old")

        result = update_project(initialized_repo)

        assert not (agents_dir / "trw-tester.md").exists()
        assert ".claude/agents/trw-tester.md" in result["cleaned"]

    def test_migrate_idempotent(self, initialized_repo: Path) -> None:
        """Second update_project run is a no-op on already-cleaned dirs."""
        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "trw-review-pr").mkdir(parents=True, exist_ok=True)
        (skills_dir / "trw-review-pr" / "SKILL.md").write_text("old", encoding="utf-8")
        _record(initialized_repo, "trw-review-pr/SKILL.md", "old")

        update_project(initialized_repo)
        assert not (skills_dir / "trw-review-pr").exists()

        result2 = update_project(initialized_repo)
        assert not [e for e in result2["cleaned"] if "/trw-review-pr/" in e]

    def test_genuine_custom_skill_not_removed(self, initialized_repo: Path) -> None:
        """A custom skill outside the ``trw-`` namespace survives update_project."""
        skills_dir = initialized_repo / ".claude" / "skills"
        custom_skill = skills_dir / "my-custom-tool"
        custom_skill.mkdir(parents=True, exist_ok=True)
        (custom_skill / "SKILL.md").write_text("custom", encoding="utf-8")

        result = update_project(initialized_repo)

        assert custom_skill.exists()
        assert (custom_skill / "SKILL.md").read_text(encoding="utf-8") == "custom"
        # Not in any removal
        assert not [e for e in result["cleaned"] if "my-custom-tool" in e]
