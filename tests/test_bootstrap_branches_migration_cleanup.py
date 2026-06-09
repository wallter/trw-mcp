"""Split bootstrap branch coverage for migration and stale-cleanup edges."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import (
    PREDECESSOR_MAP,
    _get_bundled_names,
    _migrate_prefix_predecessors,
    _read_manifest,
    _remove_stale_artifacts,
    _write_manifest,
    update_project,
)

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401


@pytest.mark.unit
class TestPrefixMigrationExtra:
    """Edge-case tests for _migrate_prefix_predecessors and manifest cleanup."""

    def test_migrate_oserror_resilience(self, tmp_path: Path) -> None:
        """OSError during shutil.rmtree skips the item and continues."""
        target = tmp_path
        skills_dir = target / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        for old, new in [("commit", "trw-commit"), ("deliver", "trw-deliver")]:
            (skills_dir / old).mkdir()
            (skills_dir / old / "SKILL.md").write_text("old", encoding="utf-8")
            (skills_dir / new).mkdir()
            (skills_dir / new / "SKILL.md").write_text("new", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        call_count = 0

        original_rmtree = shutil.rmtree

        def failing_rmtree(path: Path, *args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("permission denied")
            original_rmtree(path)

        with patch("trw_mcp.bootstrap.shutil.rmtree", side_effect=failing_rmtree):
            _migrate_prefix_predecessors(target, result)

        assert not result.get("errors")

    def test_dry_run_migration_reports_would_migrate(self, initialized_repo: Path) -> None:
        """dry_run=True appends 'would migrate:' without deleting."""
        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "commit").mkdir(parents=True, exist_ok=True)
        (skills_dir / "commit" / "SKILL.md").write_text("old", encoding="utf-8")
        (skills_dir / "trw-commit").mkdir(parents=True, exist_ok=True)
        (skills_dir / "trw-commit" / "SKILL.md").write_text("new", encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)

        assert (skills_dir / "commit").exists()
        would_migrate = [e for e in result["updated"] if "would migrate:" in e and "commit" in e]
        assert len(would_migrate) >= 1

    def test_manifest_excludes_predecessor_names_from_custom(self, initialized_repo: Path) -> None:
        """Predecessor names are excluded from custom_skills in manifest."""
        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "commit").mkdir(parents=True, exist_ok=True)
        (skills_dir / "commit" / "SKILL.md").write_text("old", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _write_manifest(initialized_repo, result)

        manifest = _read_manifest(initialized_repo)
        assert manifest is not None
        assert "commit" not in manifest.get("custom_skills", [])

    def test_migrate_prefix_predecessors_direct_call(self, tmp_path: Path) -> None:
        """Direct call removes both skill dirs and agent files."""
        target = tmp_path
        skills_dir = target / ".claude" / "skills"
        agents_dir = target / ".claude" / "agents"
        skills_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)

        (skills_dir / "simplify").mkdir()
        (skills_dir / "simplify" / "SKILL.md").write_text("old", encoding="utf-8")
        (skills_dir / "trw-simplify").mkdir()
        (skills_dir / "trw-simplify" / "SKILL.md").write_text("new", encoding="utf-8")

        (agents_dir / "researcher.md").write_text("old", encoding="utf-8")
        (agents_dir / "trw-researcher.md").write_text("new", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(target, result)

        assert not (skills_dir / "simplify").exists()
        assert not (agents_dir / "researcher.md").exists()
        assert (skills_dir / "trw-simplify").exists()
        assert (agents_dir / "trw-researcher.md").exists()
        migrated = [e for e in result["updated"] if "migrated:" in e]
        assert len(migrated) == 2

    def test_migrate_no_skills_dir_no_error(self, tmp_path: Path) -> None:
        """No error when .claude/skills/ directory does not exist."""
        target = tmp_path
        (target / ".claude" / "agents").mkdir(parents=True)

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(target, result)

        assert not result["errors"]
        assert result["updated"] == []

    def test_migrate_no_agents_dir_no_error(self, tmp_path: Path) -> None:
        """No error when .claude/agents/ directory does not exist."""
        target = tmp_path
        (target / ".claude" / "skills").mkdir(parents=True)

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(target, result)

        assert not result["errors"]
        assert result["updated"] == []

    def test_predecessor_map_keys_not_in_bundled(self) -> None:
        """No PREDECESSOR_MAP key appears in _get_bundled_names() output."""
        bundled = _get_bundled_names()
        bundled_skills = set(bundled["skills"])
        bundled_agents = set(bundled["agents"])

        for old_skill in PREDECESSOR_MAP["skills"]:
            assert old_skill not in bundled_skills, f"Predecessor skill '{old_skill}' found in bundled names"
        for old_agent in PREDECESSOR_MAP["agents"]:
            assert old_agent not in bundled_agents, f"Predecessor agent '{old_agent}' found in bundled names"


@pytest.mark.unit
class TestMigratePredecessorSuccessorAbsent:
    """When the trw- successor is absent, the predecessor must NOT be removed."""

    def test_skill_predecessor_kept_when_successor_missing(self, tmp_path: Path) -> None:
        """Skill predecessor dir is left in place when trw- successor dir is absent."""
        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)

        predecessor = skills_dir / "simplify"
        predecessor.mkdir()
        (predecessor / "SKILL.md").write_text("old", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result)

        assert predecessor.exists()
        assert result["updated"] == []

    def test_agent_predecessor_kept_when_successor_missing(self, tmp_path: Path) -> None:
        """Agent predecessor file is left in place when trw- successor file is absent."""
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        predecessor = agents_dir / "lead.md"
        predecessor.write_text("old lead", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result)

        assert predecessor.exists()
        assert result["updated"] == []

    def test_both_skill_and_agent_predecessors_kept_when_successors_missing(self, tmp_path: Path) -> None:
        """Both skill and agent predecessors are preserved when successors are absent."""
        skills_dir = tmp_path / ".claude" / "skills"
        agents_dir = tmp_path / ".claude" / "agents"
        skills_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)

        skill_pred = skills_dir / "commit"
        skill_pred.mkdir()
        (skill_pred / "SKILL.md").write_text("old", encoding="utf-8")

        agent_pred = agents_dir / "implementer.md"
        agent_pred.write_text("old implementer", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result)

        assert skill_pred.exists()
        assert agent_pred.exists()
        assert result["updated"] == []


@pytest.mark.unit
class TestRemoveStaleArtifactsCustomPreservation:
    """Custom artifacts listed in prev_custom_* must not be removed during stale cleanup."""

    def _setup_manifest_with_custom(
        self,
        target_dir: Path,
        extra_skills: list[str] | None = None,
        extra_agents: list[str] | None = None,
        extra_hooks: list[str] | None = None,
        custom_skills: list[str] | None = None,
        custom_agents: list[str] | None = None,
        custom_hooks: list[str] | None = None,
    ) -> None:
        from trw_mcp.state.persistence import FileStateWriter

        bundled = _get_bundled_names()
        manifest = {
            "version": 1,
            "skills": bundled["skills"] + (extra_skills or []),
            "agents": bundled["agents"] + (extra_agents or []),
            "hooks": bundled["hooks"] + (extra_hooks or []),
            "custom_skills": custom_skills or [],
            "custom_agents": custom_agents or [],
            "custom_hooks": custom_hooks or [],
        }
        manifest_path = target_dir / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        FileStateWriter().write_yaml(manifest_path, manifest)

    def test_custom_skill_not_removed(self, initialized_repo: Path) -> None:
        """A skill listed in prev_custom_skills is NOT removed even if it's stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_skills=["trw-my-custom"],
            custom_skills=["trw-my-custom"],
        )
        custom_skill = initialized_repo / ".claude" / "skills" / "trw-my-custom"
        custom_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_skill.exists()
        assert not any("trw-my-custom" in u for u in result["updated"])

    def test_custom_agent_not_removed(self, initialized_repo: Path) -> None:
        """A trw- agent in prev_custom_agents is NOT removed even if it's stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_agents=["trw-my-agent.md"],
            custom_agents=["trw-my-agent.md"],
        )
        custom_agent = initialized_repo / ".claude" / "agents" / "trw-my-agent.md"
        custom_agent.write_text("custom agent", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_agent.exists()
        assert not any("trw-my-agent" in u for u in result["updated"])

    def test_custom_hook_not_removed(self, initialized_repo: Path) -> None:
        """A hook listed in prev_custom_hooks is NOT removed even if stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_hooks=["my-custom-hook.sh"],
            custom_hooks=["my-custom-hook.sh"],
        )
        custom_hook = initialized_repo / ".claude" / "hooks" / "my-custom-hook.sh"
        custom_hook.write_text("#!/bin/sh", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_hook.exists()
        assert not any("my-custom-hook" in u for u in result["updated"])

    def test_non_trw_prefixed_stale_skill_not_removed(self, initialized_repo: Path) -> None:
        """Stale skills without trw- prefix are skipped (defense-in-depth guard)."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_skills=["stale-no-prefix"],
        )
        stale_skill = initialized_repo / ".claude" / "skills" / "stale-no-prefix"
        stale_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert stale_skill.exists()
        assert not any("stale-no-prefix" in u for u in result["updated"])

    def test_non_trw_prefixed_stale_agent_not_removed(self, initialized_repo: Path) -> None:
        """Stale agents without trw- prefix are skipped (defense-in-depth guard)."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_agents=["my-old-agent.md"],
        )
        stale_agent = initialized_repo / ".claude" / "agents" / "my-old-agent.md"
        stale_agent.write_text("old", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert stale_agent.exists()
        assert not any("my-old-agent" in u for u in result["updated"])
