"""Tests for Copilot CLI integration — PRD-CORE-127.

Covers profile registration, detection, instructions generation (with smart merge),
path-scoped instructions, hooks (with merge), agents, skills, and init/update wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import (
    _COPILOT_AGENTS_DIR,
    _COPILOT_HOOK_MAP,
    _COPILOT_HOOKS_PATH,
    _COPILOT_INSTRUCTIONS_DIR,
    _COPILOT_INSTRUCTIONS_PATH,
    _COPILOT_SKILLS_DIR,
    _COPILOT_TRW_END_MARKER,
    _COPILOT_TRW_START_MARKER,
    _PATH_SCOPED_TEMPLATES,
    _TRW_HOOK_DESCRIPTION_PREFIX,
    _copilot_hooks_payload,
    _copilot_instructions_content,
    _is_trw_hook_group,
    _merge_copilot_hooks,
    _smart_merge_instructions,
    generate_copilot_agents,
    generate_copilot_hooks,
    generate_copilot_instructions,
    generate_copilot_path_instructions,
    install_copilot_skills,
)
from trw_mcp.bootstrap._utils import detect_ide, resolve_ide_targets
from trw_mcp.models.config._profiles import _PROFILES, resolve_client_profile

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


# =====================================================================
# 1. Profile Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotProfile:
    """Verify the 'copilot' entry in the _PROFILES registry."""

    def test_copilot_profile_exists(self) -> None:
        assert "copilot" in _PROFILES

    def test_copilot_profile_is_full_mode(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.ceremony_mode == "full"

    def test_copilot_hooks_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.hooks_enabled is True

    def test_copilot_skills_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.skills_enabled is True

    def test_copilot_agents_md_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.write_targets.agents_md is True

    def test_copilot_context_window(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.context_window_tokens == 200_000

    def test_copilot_ceremony_weights_sum_100(self) -> None:
        profile = _PROFILES["copilot"]
        w = profile.ceremony_weights
        total = w.session_start + w.deliver + w.checkpoint + w.learn + w.build_check + w.review
        assert total == 100

    def test_copilot_scoring_weights_sum_to_one(self) -> None:
        profile = _PROFILES["copilot"]
        sw = profile.scoring_weights
        total = sw.outcome + sw.plan_quality + sw.implementation + sw.ceremony + sw.knowledge
        assert abs(total - 1.0) < 0.01

    def test_copilot_instruction_path(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.write_targets.instruction_path == ".github/copilot-instructions.md"

    def test_copilot_display_name(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.display_name == "GitHub Copilot CLI"

    def test_copilot_client_id(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.client_id == "copilot"

    def test_resolve_client_profile_copilot(self) -> None:
        profile = resolve_client_profile("copilot")
        assert profile.client_id == "copilot"
        assert profile.ceremony_mode == "full"

    def test_copilot_nudge_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.nudge_enabled is True

    def test_copilot_learning_recall_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.learning_recall_enabled is True

    def test_copilot_mcp_instructions_enabled(self) -> None:
        profile = _PROFILES["copilot"]
        assert profile.mcp_instructions_enabled is True


# =====================================================================
# 2. Detection Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotDetection:
    """Verify IDE detection recognizes Copilot artifacts."""

    def test_detect_copilot_instructions_file(self, tmp_path: Path) -> None:
        """Detect copilot via .github/copilot-instructions.md."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "copilot-instructions.md").write_text("# Instructions\n")
        detected = detect_ide(tmp_path)
        assert "copilot" in detected

    def test_detect_copilot_agents_dir(self, tmp_path: Path) -> None:
        """Detect copilot via .github/agents/ directory with .agent.md files."""
        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "trw-explorer.agent.md").write_text("---\nname: test\n---\n")
        detected = detect_ide(tmp_path)
        assert "copilot" in detected

    def test_no_detect_agents_dir_without_agent_files(self, tmp_path: Path) -> None:
        """Empty .github/agents/ directory should NOT detect copilot."""
        (tmp_path / ".github" / "agents").mkdir(parents=True)
        detected = detect_ide(tmp_path)
        assert "copilot" not in detected

    def test_no_detect_without_artifacts(self, tmp_path: Path) -> None:
        """Clean dir should not detect copilot."""
        detected = detect_ide(tmp_path)
        assert "copilot" not in detected

    def test_detect_copilot_both_signals(self, tmp_path: Path) -> None:
        """Both instructions file and agents dir present — still single detection."""
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "copilot-instructions.md").write_text("# Instructions\n")
        (github_dir / "agents").mkdir()
        detected = detect_ide(tmp_path)
        assert detected.count("copilot") == 1

    def test_resolve_ide_targets_copilot(self, tmp_path: Path) -> None:
        """When copilot artifacts exist, resolve_ide_targets includes 'copilot'."""
        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.agent.md").write_text("---\nname: test\n---\n")
        targets = resolve_ide_targets(tmp_path)
        assert "copilot" in targets

    def test_resolve_ide_targets_copilot_override(self, tmp_path: Path) -> None:
        """Explicit ide_override='copilot' returns copilot even with no artifacts."""
        targets = resolve_ide_targets(tmp_path, ide_override="copilot")
        assert targets == ["copilot"]

    def test_resolve_ide_targets_all_includes_copilot(self, tmp_path: Path) -> None:
        """Override 'all' includes copilot."""
        targets = resolve_ide_targets(tmp_path, ide_override="all")
        assert "copilot" in targets


# =====================================================================
# 3. Instructions Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotInstructions:
    """Test generate_copilot_instructions and smart-merge logic."""

    def test_instructions_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).is_file()
        assert _COPILOT_INSTRUCTIONS_PATH in result["created"]

    def test_instructions_contains_trw_markers(self, fake_git_repo: Path) -> None:
        generate_copilot_instructions(fake_git_repo)
        content = (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).read_text()
        assert _COPILOT_TRW_START_MARKER in content
        assert _COPILOT_TRW_END_MARKER in content

    def test_instructions_contains_ceremony_protocol(self, fake_git_repo: Path) -> None:
        generate_copilot_instructions(fake_git_repo)
        content = (fake_git_repo / _COPILOT_INSTRUCTIONS_PATH).read_text()
        assert "TRW Framework Integration" in content
        assert "Session Protocol" in content
        assert "trw_session_start" in content
        assert "trw_learn" in content
        assert "trw_checkpoint" in content
        assert "trw_deliver" in content

    def test_instructions_smart_merge_preserves_user_content(self, fake_git_repo: Path) -> None:
        """Existing file with user content + TRW markers → user content preserved."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)

        user_before = "# My Custom Instructions\n\nDo NOT delete this.\n\n"
        user_after = "\n\n## My Other Section\n\nKeep this too.\n"
        original_trw = f"{_COPILOT_TRW_START_MARKER}\nold content here\n{_COPILOT_TRW_END_MARKER}"
        instructions_path.write_text(user_before + original_trw + user_after)

        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        # User content before markers must be preserved
        assert "My Custom Instructions" in content
        assert "Do NOT delete this." in content
        # User content after markers must be preserved
        assert "My Other Section" in content
        assert "Keep this too." in content
        # TRW section must be updated
        assert "TRW Framework Integration" in content
        # Old content must be gone
        assert "old content here" not in content

    def test_instructions_fresh_file_when_no_markers(self, fake_git_repo: Path) -> None:
        """Existing file without markers gets TRW section appended."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# User instructions only\n\nNo markers here.\n")

        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]

        content = instructions_path.read_text()
        # User content preserved
        assert "User instructions only" in content
        assert "No markers here." in content
        # TRW markers appended
        assert _COPILOT_TRW_START_MARKER in content
        assert _COPILOT_TRW_END_MARKER in content

    def test_instructions_force_overwrites(self, fake_git_repo: Path) -> None:
        """force=True completely replaces the file with TRW content."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# I will be overwritten\nUser content here.\n")

        result = generate_copilot_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = instructions_path.read_text()
        # User content is gone — full overwrite
        assert "I will be overwritten" not in content
        # TRW content is present
        assert _COPILOT_TRW_START_MARKER in content
        assert "TRW Framework Integration" in content

    def test_instructions_updated_when_existing(self, fake_git_repo: Path) -> None:
        """Re-running on existing file marks it as updated, not created."""
        instructions_path = fake_git_repo / _COPILOT_INSTRUCTIONS_PATH
        (fake_git_repo / ".github").mkdir(parents=True, exist_ok=True)
        instructions_path.write_text("# Existing\n")

        result = generate_copilot_instructions(fake_git_repo)
        assert _COPILOT_INSTRUCTIONS_PATH in result["updated"]
        assert _COPILOT_INSTRUCTIONS_PATH not in result["created"]

    def test_instructions_creates_github_dir(self, fake_git_repo: Path) -> None:
        """The .github directory is created if it doesn't exist."""
        result = generate_copilot_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / ".github").is_dir()


# =====================================================================
# 3a. Smart-merge unit tests (pure functions)
# =====================================================================


@pytest.mark.unit
class TestSmartMergeInstructions:
    """Unit tests for the _smart_merge_instructions helper."""

    def test_merge_replaces_trw_section(self) -> None:
        existing = f"before\n{_COPILOT_TRW_START_MARKER}\nold\n{_COPILOT_TRW_END_MARKER}\nafter"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "old" not in merged
        assert "new" in merged
        assert "before" in merged
        assert "after" in merged

    def test_merge_appends_when_no_markers(self) -> None:
        existing = "user content only"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew section\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert "user content only" in merged
        assert "new section" in merged

    def test_merge_empty_existing(self) -> None:
        new_content = f"{_COPILOT_TRW_START_MARKER}\nstuff\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions("", new_content)
        assert "stuff" in merged

    def test_copilot_instructions_content_has_markers(self) -> None:
        content = _copilot_instructions_content()
        assert content.startswith(_COPILOT_TRW_START_MARKER)
        assert _COPILOT_TRW_END_MARKER in content

    def test_merge_end_before_start_appends(self) -> None:
        """End marker before start marker is treated as corrupted — append instead."""
        existing = f"user\n{_COPILOT_TRW_END_MARKER}\nmiddle\n{_COPILOT_TRW_START_MARKER}"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        # Should append rather than corrupt
        assert merged.endswith(new_content + "\n")
        assert "user" in merged

    def test_merge_single_start_marker_appends(self) -> None:
        """Only start marker present (no end) — treated as no valid pair, append."""
        existing = f"user\n{_COPILOT_TRW_START_MARKER}\npartial"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_single_end_marker_appends(self) -> None:
        """Only end marker present — treated as no valid pair, append."""
        existing = f"user\n{_COPILOT_TRW_END_MARKER}\nstuff"
        new_content = f"{_COPILOT_TRW_START_MARKER}\nnew\n{_COPILOT_TRW_END_MARKER}"
        merged = _smart_merge_instructions(existing, new_content)
        assert merged.endswith(new_content + "\n")

    def test_merge_idempotent(self, fake_git_repo: Path) -> None:
        """Running generate_copilot_instructions twice marks second as preserved."""
        result1 = generate_copilot_instructions(fake_git_repo)
        assert result1.get("created") or result1.get("updated")
        result2 = generate_copilot_instructions(fake_git_repo)
        assert result2.get("preserved")


# =====================================================================
# 4. Path Instructions Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotPathInstructions:
    """Test generate_copilot_path_instructions."""

    def test_path_instructions_dir_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_path_instructions(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_INSTRUCTIONS_DIR).is_dir()

    def test_path_instructions_files_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_path_instructions(fake_git_repo)
        assert not result["errors"]
        instructions_dir = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR
        md_files = list(instructions_dir.glob("*.instructions.md"))
        assert len(md_files) >= 1
        # Should match number of templates
        assert len(md_files) == len(_PATH_SCOPED_TEMPLATES)

    def test_path_instructions_yaml_frontmatter(self, fake_git_repo: Path) -> None:
        generate_copilot_path_instructions(fake_git_repo)
        instructions_dir = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR
        for md_file in instructions_dir.glob("*.instructions.md"):
            content = md_file.read_text()
            assert content.startswith("---"), f"{md_file.name} missing YAML frontmatter"
            assert "applyTo:" in content, f"{md_file.name} missing applyTo field"

    def test_path_instructions_no_overwrite(self, fake_git_repo: Path) -> None:
        """Existing files are preserved without force."""
        generate_copilot_path_instructions(fake_git_repo)

        # Overwrite one file with custom content
        first_template_name = next(iter(_PATH_SCOPED_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR / first_template_name
        custom_path.write_text("# My custom instructions\n")

        result = generate_copilot_path_instructions(fake_git_repo)
        assert not result["errors"]

        # The custom content should be preserved (file is in preserved list)
        rel_path = f"{_COPILOT_INSTRUCTIONS_DIR}/{first_template_name}"
        assert rel_path in result["preserved"]
        assert custom_path.read_text() == "# My custom instructions\n"

    def test_path_instructions_force_overwrites(self, fake_git_repo: Path) -> None:
        """force=True overwrites existing files."""
        generate_copilot_path_instructions(fake_git_repo)

        first_template_name = next(iter(_PATH_SCOPED_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_INSTRUCTIONS_DIR / first_template_name
        custom_path.write_text("# Custom\n")

        result = generate_copilot_path_instructions(fake_git_repo, force=True)
        assert not result["errors"]

        content = custom_path.read_text()
        assert "applyTo:" in content  # overwritten with template content

    def test_path_instructions_created_list(self, fake_git_repo: Path) -> None:
        result = generate_copilot_path_instructions(fake_git_repo)
        assert len(result["created"]) == len(_PATH_SCOPED_TEMPLATES)
        for name in _PATH_SCOPED_TEMPLATES:
            assert f"{_COPILOT_INSTRUCTIONS_DIR}/{name}" in result["created"]


# =====================================================================
# 5. Hooks Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotHooks:
    """Test generate_copilot_hooks and merge logic."""

    def test_hooks_json_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_hooks(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_HOOKS_PATH).is_file()

    def test_hooks_json_valid_structure(self, fake_git_repo: Path) -> None:
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        assert data["version"] == 1
        assert "hooks" in data
        assert isinstance(data["hooks"], dict)

    def test_hooks_json_has_session_start(self, fake_git_repo: Path) -> None:
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        assert "sessionStart" in data["hooks"]

    def test_hooks_json_has_all_expected_events(self, fake_git_repo: Path) -> None:
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name in _COPILOT_HOOK_MAP:
            assert event_name in data["hooks"], f"Missing hook event: {event_name}"

    def test_hooks_json_merge_preserves_user_hooks(self, fake_git_repo: Path) -> None:
        """Write existing hooks.json with user hooks, verify user hooks preserved after merge."""
        hooks_dir = fake_git_repo / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        hooks_path = fake_git_repo / _COPILOT_HOOKS_PATH

        user_hooks = {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {
                        "description": "My custom startup hook",
                        "hooks": [{"type": "command", "command": "echo hello"}],
                    }
                ],
                "myCustomEvent": [
                    {
                        "description": "Totally custom event",
                        "hooks": [{"type": "command", "command": "echo custom"}],
                    }
                ],
            },
        }
        hooks_path.write_text(json.dumps(user_hooks))

        result = generate_copilot_hooks(fake_git_repo)
        assert not result["errors"]

        data = json.loads(hooks_path.read_text())
        # User custom event should be preserved
        assert "myCustomEvent" in data["hooks"]
        # sessionStart should contain both user and TRW groups
        session_groups = data["hooks"]["sessionStart"]
        descriptions = [g.get("description", "") for g in session_groups]
        assert any("My custom startup hook" in d for d in descriptions)
        assert any(d.startswith(_TRW_HOOK_DESCRIPTION_PREFIX) for d in descriptions)

    def test_hooks_stdin_adapter_in_command(self, fake_git_repo: Path) -> None:
        """Verify hook commands reference stdin reading (cat pattern)."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name, groups in data["hooks"].items():
            for group in groups:
                for hook in group.get("hooks", []):
                    command = hook.get("command", "")
                    # All TRW hook commands use stdin reading via cat
                    assert "_input=$(cat)" in command, f"Hook {event_name} missing stdin adapter"

    def test_hooks_pre_tool_use_has_permission_decision(self, fake_git_repo: Path) -> None:
        """preToolUse hook must output JSON with permissionDecision."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        pre_tool_groups = data["hooks"]["preToolUse"]
        command = pre_tool_groups[0]["hooks"][0]["command"]
        assert "permissionDecision" in command

    def test_hooks_force_overwrites_existing(self, fake_git_repo: Path) -> None:
        """force=True ignores existing hooks.json entirely."""
        hooks_dir = fake_git_repo / ".github" / "hooks"
        hooks_dir.mkdir(parents=True)
        hooks_path = fake_git_repo / _COPILOT_HOOKS_PATH
        hooks_path.write_text(json.dumps({"version": 1, "hooks": {"myEvent": []}}))

        result = generate_copilot_hooks(fake_git_repo, force=True)
        assert not result["errors"]

        data = json.loads(hooks_path.read_text())
        # User event gone — full overwrite
        assert "myEvent" not in data["hooks"]

    def test_hooks_trw_description_prefix(self, fake_git_repo: Path) -> None:
        """All TRW hook groups have the description prefix for identification."""
        generate_copilot_hooks(fake_git_repo)
        data = json.loads((fake_git_repo / _COPILOT_HOOKS_PATH).read_text())
        for event_name, groups in data["hooks"].items():
            for group in groups:
                desc = group.get("description", "")
                assert desc.startswith(_TRW_HOOK_DESCRIPTION_PREFIX), (
                    f"Hook {event_name} missing TRW description prefix"
                )


# =====================================================================
# 5a. Hooks merge unit tests (pure functions)
# =====================================================================


@pytest.mark.unit
class TestCopilotHooksMerge:
    """Unit tests for hooks payload and merge helpers."""

    def test_copilot_hooks_payload_version(self) -> None:
        payload = _copilot_hooks_payload()
        assert payload["version"] == 1

    def test_copilot_hooks_payload_events(self) -> None:
        payload = _copilot_hooks_payload()
        hooks = payload["hooks"]
        assert isinstance(hooks, dict)
        for event in _COPILOT_HOOK_MAP:
            assert event in hooks

    def test_is_trw_hook_group_positive(self) -> None:
        group = {"description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} some description"}
        assert _is_trw_hook_group(group) is True

    def test_is_trw_hook_group_negative(self) -> None:
        group = {"description": "My custom hook"}
        assert _is_trw_hook_group(group) is False

    def test_is_trw_hook_group_missing_description(self) -> None:
        assert _is_trw_hook_group({}) is False

    def test_merge_replaces_trw_keeps_user(self) -> None:
        existing = {
            "version": 1,
            "hooks": {
                "sessionStart": [
                    {"description": "User hook", "hooks": []},
                    {"description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} old", "hooks": []},
                ],
            },
        }
        merged = _merge_copilot_hooks(existing)
        session_groups = merged["hooks"]["sessionStart"]
        descriptions = [g["description"] for g in session_groups]
        # User hook preserved
        assert any("User hook" in d for d in descriptions)
        # Old TRW hook replaced with new one
        assert not any("old" in d and _TRW_HOOK_DESCRIPTION_PREFIX in d for d in descriptions)
        # New TRW hook present
        assert any(d.startswith(_TRW_HOOK_DESCRIPTION_PREFIX) for d in descriptions)

    def test_merge_empty_existing(self) -> None:
        merged = _merge_copilot_hooks({"version": 1, "hooks": {}})
        assert merged["version"] == 1
        # Should have all TRW hooks
        for event in _COPILOT_HOOK_MAP:
            assert event in merged["hooks"]


# =====================================================================
# 6. Agents Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotAgents:
    """Test generate_copilot_agents."""

    def test_agents_dir_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_agents(fake_git_repo)
        assert not result["errors"]
        assert (fake_git_repo / _COPILOT_AGENTS_DIR).is_dir()

    def test_agents_files_created(self, fake_git_repo: Path) -> None:
        result = generate_copilot_agents(fake_git_repo)
        assert not result["errors"]
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        agent_files = list(agents_dir.glob("*.agent.md"))
        assert len(agent_files) >= 3

    def test_agent_yaml_frontmatter(self, fake_git_repo: Path) -> None:
        """Verify YAML frontmatter with name, description, tools."""
        generate_copilot_agents(fake_git_repo)
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        for agent_file in agents_dir.glob("*.agent.md"):
            content = agent_file.read_text()
            assert content.startswith("---"), f"{agent_file.name} missing YAML frontmatter"
            assert "name:" in content, f"{agent_file.name} missing name field"
            assert "description:" in content, f"{agent_file.name} missing description field"
            assert "tools:" in content, f"{agent_file.name} missing tools field"

    def test_agent_tools_are_copilot_format(self, fake_git_repo: Path) -> None:
        """Verify tools list uses copilot names (not Claude names like 'Bash', 'Read')."""
        generate_copilot_agents(fake_git_repo)
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        # Known Copilot tool names (execute/read/edit/glob/grep/web/agent)
        copilot_tool_names = {"execute", "read", "edit", "glob", "grep", "web", "agent"}
        claude_tool_names = {"Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebSearch", "WebFetch", "Task"}

        for agent_file in agents_dir.glob("*.agent.md"):
            content = agent_file.read_text()
            # Extract tools section from YAML frontmatter
            parts = content.split("---", 2)
            assert len(parts) >= 3, f"{agent_file.name}: malformed frontmatter"
            frontmatter = parts[1]
            # Tools should be copilot format, not Claude format
            for claude_name in claude_tool_names:
                # Check that Claude tool names don't appear as list items
                assert f"  - {claude_name}\n" not in frontmatter, (
                    f"{agent_file.name} has Claude-format tool name: {claude_name}"
                )

    def test_agents_no_overwrite_existing(self, fake_git_repo: Path) -> None:
        """Existing user agents preserved without force."""
        generate_copilot_agents(fake_git_repo)

        # Write a custom agent file over one of the generated ones
        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        first_agent_name = next(iter(_COPILOT_AGENT_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_AGENTS_DIR / first_agent_name
        custom_path.write_text("# My custom agent\n")

        result = generate_copilot_agents(fake_git_repo)
        assert not result["errors"]

        rel_path = f"{_COPILOT_AGENTS_DIR}/{first_agent_name}"
        assert rel_path in result["preserved"]
        assert custom_path.read_text() == "# My custom agent\n"

    def test_agents_force_overwrites_existing(self, fake_git_repo: Path) -> None:
        """force=True regenerates all agents."""
        generate_copilot_agents(fake_git_repo)

        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        first_agent_name = next(iter(_COPILOT_AGENT_TEMPLATES))
        custom_path = fake_git_repo / _COPILOT_AGENTS_DIR / first_agent_name
        custom_path.write_text("# My custom agent\n")

        result = generate_copilot_agents(fake_git_repo, force=True)
        assert not result["errors"]
        assert custom_path.read_text() != "# My custom agent\n"

    def test_agents_created_count(self, fake_git_repo: Path) -> None:
        from trw_mcp.bootstrap._copilot import _COPILOT_AGENT_TEMPLATES

        result = generate_copilot_agents(fake_git_repo)
        assert len(result["created"]) == len(_COPILOT_AGENT_TEMPLATES)

    def test_agent_mcp_servers(self, fake_git_repo: Path) -> None:
        """Verify agents reference trw MCP server."""
        generate_copilot_agents(fake_git_repo)
        agents_dir = fake_git_repo / _COPILOT_AGENTS_DIR
        for agent_file in agents_dir.glob("*.agent.md"):
            content = agent_file.read_text()
            assert "mcp-servers:" in content, f"{agent_file.name} missing mcp-servers"
            assert "trw" in content, f"{agent_file.name} missing trw server reference"


# =====================================================================
# 7. Skills Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotSkills:
    """Test install_copilot_skills."""

    def test_skills_installed(self, fake_git_repo: Path) -> None:
        result = install_copilot_skills(fake_git_repo)
        assert not result["errors"]
        skills_dir = fake_git_repo / _COPILOT_SKILLS_DIR
        assert skills_dir.is_dir()
        # Should have at least one skill installed
        skill_dirs = [d for d in skills_dir.iterdir() if d.is_dir()]
        assert len(skill_dirs) >= 1

    def test_skill_has_skill_md(self, fake_git_repo: Path) -> None:
        install_copilot_skills(fake_git_repo)
        skills_dir = fake_git_repo / _COPILOT_SKILLS_DIR
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                assert (skill_dir / "SKILL.md").is_file(), f"Skill {skill_dir.name} missing SKILL.md"

    def test_skills_created_list(self, fake_git_repo: Path) -> None:
        result = install_copilot_skills(fake_git_repo)
        assert len(result["created"]) >= 1
        # All created paths should reference COPILOT_SKILLS_DIR
        for path in result["created"]:
            assert path.startswith(_COPILOT_SKILLS_DIR)

    def test_skills_no_overwrite_existing(self, fake_git_repo: Path) -> None:
        """Running twice — second run updates rather than re-creates."""
        result1 = install_copilot_skills(fake_git_repo)
        assert not result1["errors"]
        created_count = len(result1["created"])
        assert created_count >= 1

        # Second run
        result2 = install_copilot_skills(fake_git_repo)
        assert not result2["errors"]
        # All files already existed, so they're "updated" not "created"
        assert len(result2["updated"]) >= 1
        # No new creates
        assert len(result2["created"]) == 0

    def test_skills_force_still_works(self, fake_git_repo: Path) -> None:
        install_copilot_skills(fake_git_repo)
        result = install_copilot_skills(fake_git_repo, force=True)
        assert not result["errors"]
        # force=True means all files are fresh writes — they show as "created"
        assert len(result["created"]) >= 1


# =====================================================================
# 8. Init/Update Integration Tests
# =====================================================================


@pytest.mark.unit
class TestCopilotInitProject:
    """Test init_project wiring for copilot."""

    def test_init_project_with_copilot_override(self, fake_git_repo: Path) -> None:
        """Call init_project with ide='copilot', verify copilot artifacts created."""
        from trw_mcp.bootstrap import init_project

        result = init_project(fake_git_repo, ide="copilot")
        assert not result["errors"]

        # Copilot-specific artifacts should exist
        assert (fake_git_repo / ".github" / "copilot-instructions.md").is_file()
        assert (fake_git_repo / ".github" / "agents").is_dir()
        assert (fake_git_repo / ".github" / "hooks" / "hooks.json").is_file()
        assert (fake_git_repo / ".github" / "instructions").is_dir()

    def test_init_project_auto_detect_copilot(self, tmp_path: Path) -> None:
        """Create copilot detection artifacts first, then init_project auto-detects."""
        from trw_mcp.bootstrap import init_project

        (tmp_path / ".git").mkdir()
        agents_dir = tmp_path / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.agent.md").write_text("---\nname: test\n---\n")

        result = init_project(tmp_path)
        assert not result["errors"]

        # Copilot artifacts should be generated via auto-detection
        assert (tmp_path / ".github" / "copilot-instructions.md").is_file()
        assert (tmp_path / ".github" / "hooks" / "hooks.json").is_file()


@pytest.mark.unit
class TestCopilotUpdateProject:
    """Test _update_copilot_artifacts integration."""

    def test_update_copilot_artifacts(self, fake_git_repo: Path) -> None:
        """Call _update_copilot_artifacts, verify it runs without errors."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        # Create copilot detection artifact so the function proceeds
        agents_dir = fake_git_repo / ".github" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "test.agent.md").write_text("---\nname: test\n---\n")

        _update_copilot_artifacts(fake_git_repo, result)
        assert not result["errors"]
        # Should have created copilot artifacts
        assert len(result["created"]) >= 1

    def test_update_copilot_artifacts_skips_without_detection(self, fake_git_repo: Path) -> None:
        """Without copilot artifacts detected, _update_copilot_artifacts is a no-op."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_copilot_artifacts(fake_git_repo, result)
        # Should be no-op since no copilot detection artifacts
        assert not result["created"]
        assert not result["errors"]

    def test_update_copilot_artifacts_with_override(self, fake_git_repo: Path) -> None:
        """Override to copilot even without detection artifacts."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        result: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_copilot_artifacts(fake_git_repo, result, ide_override="copilot")
        assert not result["errors"]
        assert len(result["created"]) >= 1

    def test_update_copilot_artifacts_idempotent(self, fake_git_repo: Path) -> None:
        """Running update twice is safe — second run updates rather than errors."""
        from trw_mcp.bootstrap._ide_targets import _update_copilot_artifacts

        (fake_git_repo / ".github" / "agents").mkdir(parents=True)

        result1: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_copilot_artifacts(fake_git_repo, result1)
        assert not result1["errors"]

        result2: dict[str, list[str]] = {
            "created": [],
            "updated": [],
            "preserved": [],
            "errors": [],
            "warnings": [],
        }
        _update_copilot_artifacts(fake_git_repo, result2)
        assert not result2["errors"]
