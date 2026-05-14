"""Split bootstrap update cleanup tests."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.bootstrap import init_project, update_project


class TestUpdateRemovesStaleArtifacts:
    """Test that update_project cleans up renamed/removed artifacts."""

    def test_removes_stale_managed_hook(self, initialized_repo: Path) -> None:
        """Hook listed in manifest but no longer bundled is removed."""
        # init_project writes manifest; add a fake entry to simulate stale
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        hooks_list = list(manifest.get("hooks", []))
        hooks_list.append("old-removed-hook.sh")
        manifest["hooks"] = hooks_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        stale_hook = initialized_repo / ".claude" / "hooks" / "old-removed-hook.sh"
        stale_hook.write_text("#!/bin/sh\nexit 0", encoding="utf-8")

        result = update_project(initialized_repo)

        assert not stale_hook.exists()
        assert any("removed:" in u and "old-removed-hook" in u for u in result["updated"])

    def test_removes_stale_managed_skill(self, initialized_repo: Path) -> None:
        """Skill listed in manifest but no longer bundled is removed."""
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        skills_list = list(manifest.get("skills", []))
        skills_list.append("trw-old-skill")
        manifest["skills"] = skills_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        stale_skill = initialized_repo / ".claude" / "skills" / "trw-old-skill"
        stale_skill.mkdir(parents=True, exist_ok=True)
        (stale_skill / "SKILL.md").write_text("old", encoding="utf-8")

        update_project(initialized_repo)

        assert not stale_skill.exists()

    def test_removes_stale_managed_agent(self, initialized_repo: Path) -> None:
        """Agent listed in manifest but no longer bundled is removed."""
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        manifest = reader.read_yaml(manifest_path)
        assert isinstance(manifest, dict)
        agents_list = list(manifest.get("agents", []))
        agents_list.append("trw-old-agent.md")
        manifest["agents"] = agents_list
        FileStateWriter().write_yaml(manifest_path, manifest)

        stale_agent = initialized_repo / ".claude" / "agents" / "trw-old-agent.md"
        stale_agent.write_text("old agent", encoding="utf-8")

        update_project(initialized_repo)

        assert not stale_agent.exists()

    def test_does_not_remove_non_md_agents(self, initialized_repo: Path) -> None:
        """Non-.md files in agents directory are not touched."""
        other_file = initialized_repo / ".claude" / "agents" / "notes.txt"
        other_file.write_text("user notes", encoding="utf-8")

        update_project(initialized_repo)

        assert other_file.exists()

    def test_custom_skill_survives_update(self, initialized_repo: Path) -> None:
        """Custom skills NOT in manifest are never deleted by update."""
        custom_skill = initialized_repo / ".claude" / "skills" / "my-deploy"
        custom_skill.mkdir(parents=True, exist_ok=True)
        (custom_skill / "SKILL.md").write_text("custom", encoding="utf-8")

        update_project(initialized_repo)

        assert custom_skill.exists()
        assert (custom_skill / "SKILL.md").read_text(encoding="utf-8") == "custom"

    def test_custom_agent_survives_update(self, initialized_repo: Path) -> None:
        """Custom agents NOT in manifest are never deleted by update."""
        custom_agent = initialized_repo / ".claude" / "agents" / "my-reviewer.md"
        custom_agent.write_text("custom agent", encoding="utf-8")

        update_project(initialized_repo)

        assert custom_agent.exists()
        assert custom_agent.read_text(encoding="utf-8") == "custom agent"

    def test_no_cleanup_without_manifest(self, fake_git_repo: Path) -> None:
        """First update without manifest writes manifest but skips cleanup."""
        # Manually init without manifest (simulate pre-manifest install)
        init_project(fake_git_repo)
        manifest_path = fake_git_repo / ".trw" / "managed-artifacts.yaml"
        manifest_path.unlink()  # Remove manifest written by init

        # Add a custom skill that should survive
        custom_skill = fake_git_repo / ".claude" / "skills" / "my-custom"
        custom_skill.mkdir(parents=True, exist_ok=True)
        (custom_skill / "SKILL.md").write_text("custom", encoding="utf-8")

        result = update_project(fake_git_repo)
        assert not result["errors"]

        # Custom skill survives (no cleanup without prior manifest)
        assert custom_skill.exists()
        # Manifest is now written for future updates
        assert manifest_path.exists()


class TestContextCleanup:
    """Test _cleanup_context_transients via update_project — PRD-FIX-031."""

    def test_removes_transient_files(self, initialized_repo: Path) -> None:
        """Transient files are removed; allowlisted files are preserved."""
        context = initialized_repo / ".trw" / "context"
        # Allowlisted (should survive)
        (context / "analytics.yaml").write_text("data: 1", encoding="utf-8")
        (context / "build-status.yaml").write_text("ok", encoding="utf-8")
        (context / "pre_compact_state.json").write_text("{}", encoding="utf-8")
        (context / "hooks-reference.yaml").write_text("ref", encoding="utf-8")
        # Transient (should be removed)
        (context / "tc_block_abc").write_text("", encoding="utf-8")
        (context / "sprint-34-findings.yaml").write_text("", encoding="utf-8")
        (context / "velocity.yaml").write_text("", encoding="utf-8")
        (context / "tool-telemetry.jsonl").write_text("", encoding="utf-8")

        result = update_project(initialized_repo)

        # Allowlisted files still present
        assert (context / "analytics.yaml").exists()
        assert (context / "behavioral_protocol.yaml").exists()
        assert (context / "messages.yaml").exists()
        assert (context / "build-status.yaml").exists()
        assert (context / "pre_compact_state.json").exists()
        assert (context / "hooks-reference.yaml").exists()
        # Transient files removed
        assert not (context / "tc_block_abc").exists()
        assert not (context / "sprint-34-findings.yaml").exists()
        assert not (context / "velocity.yaml").exists()
        assert not (context / "tool-telemetry.jsonl").exists()

    def test_result_cleaned_key_populated(self, initialized_repo: Path) -> None:
        """result['cleaned'] contains paths of removed files."""
        context = initialized_repo / ".trw" / "context"
        (context / "velocity.yaml").write_text("stale", encoding="utf-8")
        (context / "tc_block_x").write_text("", encoding="utf-8")

        result = update_project(initialized_repo)

        assert len(result["cleaned"]) >= 2
        cleaned_names = [Path(p).name for p in result["cleaned"]]
        assert "velocity.yaml" in cleaned_names
        assert "tc_block_x" in cleaned_names

    def test_dry_run_reports_without_deleting(self, initialized_repo: Path) -> None:
        """dry_run=True reports would-be removals without deleting files."""
        context = initialized_repo / ".trw" / "context"
        (context / "velocity.yaml").write_text("stale", encoding="utf-8")
        (context / "idle_block_lead").write_text("", encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)

        # Files still exist
        assert (context / "velocity.yaml").exists()
        assert (context / "idle_block_lead").exists()
        # Cleaned entries have "would remove:" prefix
        assert len(result["cleaned"]) == 2
        for entry in result["cleaned"]:
            assert entry.startswith("would remove: ")

    def test_noop_when_only_allowlisted(self, initialized_repo: Path) -> None:
        """No files removed when only allowlisted files are present."""
        result = update_project(initialized_repo)

        # Only allowlisted files should be in context dir (behavioral_protocol, messages)
        assert result["cleaned"] == []

    def test_result_cleaned_key_always_present(self, initialized_repo: Path) -> None:
        """result dict always has 'cleaned' key, even when nothing is removed."""
        result = update_project(initialized_repo)

        assert "cleaned" in result
        assert isinstance(result["cleaned"], list)
