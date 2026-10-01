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

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.unit
class TestPrefixMigrationExtra:
    """Edge-case tests for _migrate_prefix_predecessors and manifest cleanup."""

    def test_migrate_oserror_resilience(self, tmp_path: Path) -> None:
        """OSError during shutil.rmtree skips the item and continues."""
        target = tmp_path
        skills_dir = target / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        for old in ("review-pr", "trw-review-pr"):
            (skills_dir / old).mkdir()
            (skills_dir / old / "SKILL.md").write_text("old", encoding="utf-8")

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
            _migrate_prefix_predecessors(
                target,
                result,
                manifest_hashes={"review-pr/SKILL.md": _sha("old"), "trw-review-pr/SKILL.md": _sha("old")},
            )

        assert not result.get("errors")

    def test_dry_run_reports_the_migration_without_deleting(self, initialized_repo: Path) -> None:
        """A TRW-recorded retired skill is listed under ``cleaned`` by a dry run and left on disk."""
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "review-pr").mkdir(parents=True, exist_ok=True)
        (skills_dir / "review-pr" / "SKILL.md").write_text("old", encoding="utf-8")
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        manifest["content_hashes"]["review-pr/SKILL.md"] = _sha("old")
        FileStateWriter().write_yaml(manifest_path, manifest)

        result = update_project(initialized_repo, dry_run=True)

        assert (skills_dir / "review-pr").exists()
        assert ".claude/skills/review-pr/SKILL.md" in result["cleaned"]

    def test_manifest_excludes_predecessor_names_from_custom(self, initialized_repo: Path) -> None:
        """Retired names are excluded from custom_skills in manifest."""
        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "review-pr").mkdir(parents=True, exist_ok=True)
        (skills_dir / "review-pr" / "SKILL.md").write_text("old", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _write_manifest(initialized_repo, result)

        manifest = _read_manifest(initialized_repo)
        assert manifest is not None
        assert "review-pr" not in manifest.get("custom_skills", [])

    def test_migrate_prefix_predecessors_direct_call(self, tmp_path: Path) -> None:
        """Direct call removes both skill dirs and agent files."""
        target = tmp_path
        skills_dir = target / ".claude" / "skills"
        agents_dir = target / ".claude" / "agents"
        skills_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)

        (skills_dir / "review-pr").mkdir()
        (skills_dir / "review-pr" / "SKILL.md").write_text("old", encoding="utf-8")
        (skills_dir / "trw-audit").mkdir()
        (skills_dir / "trw-audit" / "SKILL.md").write_text("new", encoding="utf-8")

        (agents_dir / "tester.md").write_text("old", encoding="utf-8")
        (agents_dir / "trw-researcher.md").write_text("new", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        hashes = {"review-pr/SKILL.md": _sha("old"), "tester.md": _sha("old")}
        _migrate_prefix_predecessors(target, result, manifest_hashes=hashes)

        assert not (skills_dir / "review-pr").exists()
        assert not (agents_dir / "tester.md").exists()
        assert (skills_dir / "trw-audit").exists()
        assert (agents_dir / "trw-researcher.md").exists()
        assert not result.get("preserved")

    def test_unrecorded_predecessor_is_kept_as_not_installer_owned(self, tmp_path: Path) -> None:
        """PRD-INFRA-190-FR06: no manifest proof, no deletion of a retired name."""
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "tester.md").write_text("mine", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result, manifest_hashes={"tester.md": _sha("old")})

        assert (agents_dir / "tester.md").read_text(encoding="utf-8") == "mine"
        assert result["preserved"] == [".claude/agents/tester.md (not_installer_owned)"]

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

    @pytest.mark.parametrize("retired_name", ["review-pr", "trw-review-pr"])
    def test_retired_review_skill_removed_without_successor(self, tmp_path: Path, retired_name: str) -> None:
        skills_dir = tmp_path / ".claude" / "skills"
        retired = skills_dir / retired_name
        retired.mkdir(parents=True)
        (retired / "SKILL.md").write_text("retired", encoding="utf-8")
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        _migrate_prefix_predecessors(tmp_path, result, manifest_hashes={f"{retired_name}/SKILL.md": _sha("retired")})

        assert not retired.exists()
        assert not result.get("preserved")

    def test_every_predecessor_entry_is_a_retirement(self) -> None:
        """REMOVE-S1: the rename path is gone, so a non-None successor would be deleted without one."""
        for kind, names in PREDECESSOR_MAP.items():
            renamed = {name: successor for name, successor in names.items() if successor is not None}
            assert not renamed, f"{kind}: rename entries are no longer migrated: {renamed}"


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


@pytest.mark.unit
class TestStaleCleanupOwnership:
    """A stale bundled artifact is removed only with manifest proof (PRD-INFRA-190-FR06).

    Removals are reported from the update's surface diff (``cleaned``), in both
    the dry run and the real run.
    """

    @staticmethod
    def _write_manifest_with_ghost(target_dir: Path, *, recorded: bool) -> Path:
        from trw_mcp.state.persistence import FileStateWriter

        bundled = _get_bundled_names()
        manifest = {
            "version": 2,
            "skills": bundled["skills"] + ["trw-ghost"],
            "agents": bundled["agents"],
            "hooks": bundled["hooks"],
            "custom_skills": [],
            "custom_agents": [],
            "custom_hooks": [],
            "content_hashes": {"trw-ghost/SKILL.md": _sha("stale")} if recorded else {},
        }
        manifest_path = target_dir / ".trw" / "managed-artifacts.yaml"
        FileStateWriter().write_yaml(manifest_path, manifest)
        ghost = target_dir / ".claude" / "skills" / "trw-ghost"
        ghost.mkdir(parents=True, exist_ok=True)
        (ghost / "SKILL.md").write_text("stale", encoding="utf-8")
        return ghost

    def test_dry_run_reports_the_removal_and_keeps_the_file(self, initialized_repo: Path) -> None:
        ghost = self._write_manifest_with_ghost(initialized_repo, recorded=True)

        result = update_project(initialized_repo, dry_run=True)

        assert ghost.exists()
        assert ".claude/skills/trw-ghost/SKILL.md" in result["cleaned"]

    def test_real_run_deletes_a_recorded_stale_artifact(self, initialized_repo: Path) -> None:
        ghost = self._write_manifest_with_ghost(initialized_repo, recorded=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert not ghost.exists()

    def test_unrecorded_stale_artifact_is_kept(self, initialized_repo: Path) -> None:
        ghost = self._write_manifest_with_ghost(initialized_repo, recorded=False)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert ghost.exists()
        assert result["preserved"] == [".claude/skills/trw-ghost (not_installer_owned)"]
