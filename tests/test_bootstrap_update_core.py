"""Split bootstrap update core behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.models.config import TRWConfig

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401


class TestUpdateProjectBasics:
    """Test update_project basic behavior."""

    def test_requires_trw_installed(self, tmp_path: Path) -> None:
        """update_project errors if .trw/ does not exist (in a real repo)."""
        (tmp_path / ".git").mkdir()
        result = update_project(tmp_path)
        assert len(result["errors"]) == 1
        assert ".trw/ not found" in result["errors"][0]

    def test_requires_git_repo(self, tmp_path: Path) -> None:
        """update_project refuses to scaffold into a non-repo (symmetry with init)."""
        # No .git/ at all — even with .trw/ present the guard must fire first.
        (tmp_path / ".trw").mkdir()
        result = update_project(tmp_path)
        assert any("not a git repository" in e for e in result["errors"])

    def test_rejects_symlinked_git(self, tmp_path: Path) -> None:
        """A symlinked .git must not satisfy the git-repo guard (symlink-safe)."""
        real_repo = tmp_path / "real"
        real_repo.mkdir()
        (real_repo / ".git").mkdir()
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / ".trw").mkdir()
        (victim / ".git").symlink_to(real_repo / ".git")
        result = update_project(victim)
        assert any("not a git repository" in e for e in result["errors"])

    def test_no_errors_on_initialized_repo(self, initialized_repo: Path) -> None:
        """update_project succeeds on an initialized repo."""
        result = update_project(initialized_repo)
        assert not result["errors"]

    def test_reports_updated_files(self, initialized_repo: Path) -> None:
        """update_project reports framework files as updated."""
        result = update_project(initialized_repo)
        assert len(result["updated"]) > 0
        # Should have updated hooks, skills, agents, framework files
        updated_str = "\n".join(result["updated"])
        assert "FRAMEWORK.md" in updated_str
        assert "hooks" in updated_str

    def test_reports_preserved_files(self, initialized_repo: Path) -> None:
        """update_project reports user files as preserved."""
        result = update_project(initialized_repo)
        preserved_str = "\n".join(result["preserved"])
        assert "config.yaml" in preserved_str


@pytest.mark.unit
class TestUpdatePreservesUserFiles:
    """Test that update_project never overwrites user-customized files."""

    def test_preserves_config_yaml(self, initialized_repo: Path) -> None:
        """User's config.yaml is never overwritten."""
        config_path = initialized_repo / ".trw" / "config.yaml"
        config_path.write_text("custom_setting: true\n", encoding="utf-8")

        update_project(initialized_repo)

        content = config_path.read_text(encoding="utf-8")
        assert "custom_setting: true" in content

    def test_preserves_learnings(self, initialized_repo: Path) -> None:
        """User's learnings index is never overwritten."""
        index_path = initialized_repo / ".trw" / "learnings" / "index.yaml"
        index_path.write_text("entries:\n- id: L001\n", encoding="utf-8")

        update_project(initialized_repo)

        content = index_path.read_text(encoding="utf-8")
        assert "L001" in content

    def test_preserves_mcp_json(self, initialized_repo: Path) -> None:
        """User's .mcp.json is never overwritten."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text('{"custom": true}\n', encoding="utf-8")

        update_project(initialized_repo)

        content = mcp_path.read_text(encoding="utf-8")
        assert '"custom": true' in content


@pytest.mark.unit
class TestUpdateOverwritesFrameworkFiles:
    """Test that update_project overwrites framework-managed files."""

    def test_updates_framework_md(self, initialized_repo: Path) -> None:
        """FRAMEWORK.md is overwritten with latest version."""
        fw_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        fw_path.write_text("old framework content", encoding="utf-8")

        update_project(initialized_repo)

        content = fw_path.read_text(encoding="utf-8")
        assert content != "old framework content"
        assert TRWConfig().framework_version in content

    def test_updates_hooks(self, initialized_repo: Path) -> None:
        """A stale-but-unedited hook is overwritten with the latest version.

        The manifest hash is refreshed to record the stale content as the
        install baseline (i.e. the user did NOT edit it since install), so the
        PRD-FIX-068-FR05 guard reports it unmodified and the newer bundled
        content wins.
        """
        from trw_mcp.bootstrap._utils import _DATA_DIR
        from trw_mcp.bootstrap._version_migration import _write_manifest

        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        hook_path.write_text("old hook", encoding="utf-8")
        # Refresh the manifest so "old hook" is the recorded baseline (unmodified).
        _write_manifest(initialized_repo, {"updated": [], "created": [], "errors": []}, _DATA_DIR)

        update_project(initialized_repo)

        content = hook_path.read_text(encoding="utf-8")
        assert content != "old hook"

    def test_updates_skills(self, initialized_repo: Path) -> None:
        """A stale-but-unedited skill file is overwritten with the latest version."""
        from trw_mcp.bootstrap._utils import _DATA_DIR
        from trw_mcp.bootstrap._version_migration import _write_manifest

        skill_path = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
        skill_path.write_text("old skill", encoding="utf-8")
        _write_manifest(initialized_repo, {"updated": [], "created": [], "errors": []}, _DATA_DIR)

        update_project(initialized_repo)

        content = skill_path.read_text(encoding="utf-8")
        assert content != "old skill"

    def test_updates_agents(self, initialized_repo: Path) -> None:
        """Framework-managed (unmodified) agents are re-materialized on update.

        A stale on-disk agent left in the raw bundled tier form
        (``model: frontier``) is framework-recognized (not a user edit), so the
        update path self-heals it to the resolved ``model: opus`` line instead
        of freezing it. This proves the PRD-FIX-068-FR05 guard does NOT protect
        framework-managed files (only genuine user edits are preserved).
        """
        from trw_mcp.bootstrap._utils import _DATA_DIR

        agent_path = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        # Raw bundled form carries the unresolved capability tier token.
        raw_bundled = (_DATA_DIR / "agents" / "trw-implementer.md").read_text(encoding="utf-8")
        assert "model: frontier" in raw_bundled
        agent_path.write_text(raw_bundled, encoding="utf-8")

        result = update_project(initialized_repo)

        content = agent_path.read_text(encoding="utf-8")
        assert "model: frontier" not in content
        assert "model: opus" in content
        assert str(agent_path) not in result.get("modified", [])


@pytest.mark.unit
class TestUpdateClaudeMdSmartMerge:
    """Test that update_project smart-merges CLAUDE.md."""

    def test_preserves_user_sections(self, initialized_repo: Path) -> None:
        """User content above TRW markers is preserved."""
        claude_md = initialized_repo / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")

        # Add user content before the TRW section
        user_section = "## My Custom Section\n\nThis is user content.\n\n"
        content = content.replace("<!-- TRW AUTO-GENERATED", user_section + "<!-- TRW AUTO-GENERATED")
        claude_md.write_text(content, encoding="utf-8")

        update_project(initialized_repo)

        updated = claude_md.read_text(encoding="utf-8")
        assert "My Custom Section" in updated
        assert "This is user content." in updated
        assert "trw_session_start" in updated  # TRW section still present

    def test_updates_trw_section(self, initialized_repo: Path) -> None:
        """TRW auto-generated section is updated."""
        claude_md = initialized_repo / "CLAUDE.md"

        update_project(initialized_repo)

        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content
        assert "trw_session_start" in content

    def test_appends_trw_section_if_missing(self, initialized_repo: Path) -> None:
        """If CLAUDE.md has no TRW markers, append the section."""
        claude_md = initialized_repo / "CLAUDE.md"
        claude_md.write_text("# My Project\n\nNo TRW section here.\n", encoding="utf-8")

        update_project(initialized_repo)

        content = claude_md.read_text(encoding="utf-8")
        assert "# My Project" in content
        assert "<!-- trw:start -->" in content
        assert "trw_session_start" in content

    def test_creates_claude_md_if_missing(self, initialized_repo: Path) -> None:
        """If CLAUDE.md doesn't exist, create it from template."""
        claude_md = initialized_repo / "CLAUDE.md"
        claude_md.unlink()

        result = update_project(initialized_repo)
        assert not result["errors"]
        assert claude_md.exists()
        assert "trw_session_start" in claude_md.read_text(encoding="utf-8")


@pytest.mark.unit
class TestUpdateResolvesAgentModelTier:
    """sub_5ctrrLJ: update-project must resolve agent ``model:`` tiers like init.

    The pre-fix update path raw-copied bundled agents, re-materializing the
    unresolvable ``model: frontier`` token so agent spawns failed after upgrades.
    """

    def test_update_resolves_frontier_to_opus(self, initialized_repo: Path) -> None:
        """After update, a bundled agent carries ``model: opus`` — never ``frontier``."""
        agent = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        # Fresh install already resolves; prove update KEEPS it resolved.
        assert "model: opus" in agent.read_text(encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = agent.read_text(encoding="utf-8")
        assert "model: opus" in content
        assert "model: frontier" not in content

    def test_update_heals_agent_left_at_raw_tier(self, initialized_repo: Path) -> None:
        """A resolved-but-unmodified agent (raw framework form on disk) IS updated.

        Simulates a project a pre-fix update left at ``model: frontier`` while the
        manifest still records the resolved hash. The reconciled guard must treat
        the raw framework form as unmodified (not a user edit) and heal it to
        ``model: opus`` — the exact misclassification the fix prevents.
        """
        from trw_mcp.bootstrap import _DATA_DIR

        agent = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        raw_bundled = (_DATA_DIR / "agents" / "trw-implementer.md").read_text(encoding="utf-8")
        assert "model: frontier" in raw_bundled
        # Leave the agent in the broken raw-tier state a pre-fix update produced.
        agent.write_text(raw_bundled, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = agent.read_text(encoding="utf-8")
        assert "model: opus" in content
        assert "model: frontier" not in content
        # Must NOT be misclassified as a user modification.
        assert not any("trw-implementer.md" in m for m in result.get("modified", []))

    def test_update_preserves_genuinely_user_edited_agent(self, initialized_repo: Path) -> None:
        """A genuinely user-edited agent is preserved and reported, not clobbered.

        Focused unit: exercises the guard directly via ``_update_framework_files``
        with explicit manifest hashes. The end-to-end proof that the LIVE
        ``update_project()`` path now threads these hashes (PRD-FIX-068-FR05) lives
        in :class:`TestUpdateLivePathPreservesUserEdits`.
        """
        from trw_mcp.bootstrap._template_updater import _update_framework_files
        from trw_mcp.bootstrap._version_manifest import _read_manifest

        agent = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        edited = agent.read_text(encoding="utf-8") + "\n\n<!-- user note: do not overwrite -->\n"
        agent.write_text(edited, encoding="utf-8")

        manifest = _read_manifest(initialized_repo)
        assert isinstance(manifest, dict)
        manifest_hashes = manifest["content_hashes"]
        assert isinstance(manifest_hashes, dict)

        from trw_mcp.bootstrap import _DATA_DIR

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "modified": [],
        }
        _update_framework_files(initialized_repo, _DATA_DIR, result, dry_run=False, manifest_hashes=manifest_hashes)

        # User edit survives untouched and is reported as modified.
        assert agent.read_text(encoding="utf-8") == edited
        assert any("trw-implementer.md" in m for m in result["modified"])


@pytest.mark.unit
class TestUpdateLivePathPreservesUserEdits:
    """PRD-FIX-068-FR05 on the REAL update_project() path.

    Before the fix, ``_run_core_update_phases`` called ``_update_framework_files``
    with ``manifest_hashes=None`` (the manifest was only read later), so the
    user-modification guard was dead on the live path and user-edited agents were
    silently overwritten. These tests drive the full ``update_project()`` entry
    point to prove the prior manifest's content hashes are now threaded through.
    """

    def test_live_path_preserves_user_edited_agent(self, initialized_repo: Path) -> None:
        """A user-edited agent survives update_project() and is reported modified.

        A sibling un-edited agent is still updated (frontier->opus resolution
        intact), proving the guard protects only genuine user edits.
        """
        agents_dir = initialized_repo / ".claude" / "agents"
        edited_agent = agents_dir / "trw-implementer.md"
        # trw-lead is a frontier-tier sibling (resolves to model: opus like
        # trw-implementer); left un-edited it must still update.
        untouched_agent = agents_dir / "trw-lead.md"

        # Genuine user edit — a body change no framework rendering produces.
        original = edited_agent.read_text(encoding="utf-8")
        edited = original + "\n\n<!-- user note: do not overwrite -->\n"
        edited_agent.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        # User edit preserved byte-for-byte and reported in result["modified"].
        assert edited_agent.read_text(encoding="utf-8") == edited
        assert any("trw-implementer.md" in m for m in result.get("modified", []))

        # The un-edited frontier sibling still resolves to the client model.
        untouched = untouched_agent.read_text(encoding="utf-8")
        assert "model: opus" in untouched
        assert "model: frontier" not in untouched
        assert not any("trw-lead.md" in m for m in result.get("modified", []))

    def test_live_path_first_run_without_manifest_preserves_edit_and_heals_raw(self, initialized_repo: Path) -> None:
        """No prior manifest: a genuine user edit is PRESERVED; a raw-tier file HEALS.

        Simulates the first update on a project installed before manifest support
        existed (``_read_manifest`` returns None → ``manifest_hashes`` is None).
        The reconciled guard (P1-7 round-2 audit) must still distinguish a genuine
        user edit (matches no framework rendering → preserved, FR05's unconditional
        AC) from a raw ``model: frontier`` file a pre-fix update left behind
        (matches the raw framework rendering → self-heals to ``model: opus``).
        """
        from trw_mcp.bootstrap._utils import _DATA_DIR
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE, _read_manifest

        manifest_path = initialized_repo / ".trw" / _MANIFEST_FILE
        if manifest_path.exists():
            manifest_path.unlink()
        assert _read_manifest(initialized_repo) is None

        agents_dir = initialized_repo / ".claude" / "agents"

        # Agent A: genuine user edit — a body change no framework rendering produces.
        edited_agent = agents_dir / "trw-implementer.md"
        edited = edited_agent.read_text(encoding="utf-8") + "\n\n<!-- user note: keep me -->\n"
        edited_agent.write_text(edited, encoding="utf-8")

        # Agent B: raw framework tier a pre-fix update produced (frontier sibling).
        raw_agent = agents_dir / "trw-lead.md"
        raw_bundled = (_DATA_DIR / "agents" / "trw-lead.md").read_text(encoding="utf-8")
        assert "model: frontier" in raw_bundled
        raw_agent.write_text(raw_bundled, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        # Genuine edit preserved byte-for-byte + reported even without a manifest.
        assert edited_agent.read_text(encoding="utf-8") == edited
        assert any("trw-implementer.md" in m for m in result.get("modified", []))

        # Raw-tier framework file still heals to the resolved client model.
        healed = raw_agent.read_text(encoding="utf-8")
        assert "model: opus" in healed
        assert "model: frontier" not in healed
        assert not any("trw-lead.md" in m for m in result.get("modified", []))


class TestUpdateCreatesNewArtifacts:
    """Test that update_project creates new artifacts from newer versions."""

    def test_creates_new_skill(self, initialized_repo: Path) -> None:
        """New skills in bundled data are deployed."""
        # All skills should exist after update
        result = update_project(initialized_repo)
        assert not result["errors"]

        skills_dir = initialized_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        # Should have all expected skills
        assert "trw-deliver" in deployed
        assert "trw-learn" in deployed
        assert "trw-project-health" in deployed

    def test_creates_new_agent(self, initialized_repo: Path) -> None:
        """New agents in bundled data are deployed."""
        result = update_project(initialized_repo)
        assert not result["errors"]

        agents_dir = initialized_repo / ".claude" / "agents"
        deployed = sorted(f.name for f in agents_dir.iterdir() if f.suffix == ".md")
        assert "trw-implementer.md" in deployed
        assert "trw-auditor.md" in deployed


@pytest.mark.unit
class TestUpdateWarningsAndVersionCheck:
    """Test update_project warnings, version check, and restart guidance."""

    def test_includes_restart_warning(self, initialized_repo: Path) -> None:
        """update_project always warns about restarting sessions."""
        result = update_project(initialized_repo)
        assert "warnings" in result
        assert any("Restart" in w for w in result["warnings"])

    def test_includes_version_check(self, initialized_repo: Path) -> None:
        """update_project checks installed package version."""
        result = update_project(initialized_repo)
        # Should have either a version match (preserved) or mismatch (warning)
        version_related = [p for p in result["preserved"] if "trw-mcp package" in p] + [
            w for w in result["warnings"] if "trw-mcp" in w and "differs" in w
        ]
        assert len(version_related) > 0

    def test_warnings_key_always_present(self, initialized_repo: Path) -> None:
        """update_project result always includes 'warnings' key."""
        result = update_project(initialized_repo)
        assert "warnings" in result
        assert isinstance(result["warnings"], list)


class TestRootFrameworkMd:
    """Test that init/update deploy FRAMEWORK.md to the project root."""

    def test_init_creates_root_framework_md(self, fake_git_repo: Path) -> None:
        """init_project creates FRAMEWORK.md at the project root."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        root_fw = fake_git_repo / "FRAMEWORK.md"
        assert root_fw.is_file()
        content = root_fw.read_text(encoding="utf-8")
        assert TRWConfig().framework_version in content

    def test_init_root_matches_cached(self, fake_git_repo: Path) -> None:
        """Root FRAMEWORK.md matches .trw/frameworks/FRAMEWORK.md after init."""
        init_project(fake_git_repo)

        root_fw = fake_git_repo / "FRAMEWORK.md"
        cached_fw = fake_git_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert root_fw.read_text(encoding="utf-8") == cached_fw.read_text(encoding="utf-8")

    def test_update_overwrites_stale_root_framework_md(self, initialized_repo: Path) -> None:
        """update_project overwrites a stale root FRAMEWORK.md."""
        root_fw = initialized_repo / "FRAMEWORK.md"
        root_fw.write_text("old stale content v16.0", encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = root_fw.read_text(encoding="utf-8")
        assert content != "old stale content v16.0"
        assert TRWConfig().framework_version in content

    def test_update_root_matches_cached(self, initialized_repo: Path) -> None:
        """After update, root FRAMEWORK.md matches cached version."""
        update_project(initialized_repo)

        root_fw = initialized_repo / "FRAMEWORK.md"
        cached_fw = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert root_fw.read_text(encoding="utf-8") == cached_fw.read_text(encoding="utf-8")


@pytest.mark.unit
class TestUpdatePreservesUserEditedHooksAndSkills:
    """Codex HIGH round-2 audit: PRD-FIX-068-FR05 covers hooks + skills, not only agents.

    The manifest already records hook/skill content hashes (``_compute_content_hashes``),
    but the pre-fix ``_update_hooks`` / ``_update_skills`` raw-copied unconditionally,
    clobbering user-edited hooks/skills. The guard is now threaded through both.
    """

    def test_live_path_preserves_user_edited_hook(self, initialized_repo: Path) -> None:
        """A user-edited hook survives update_project() and is reported modified."""
        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        edited = hook.read_text(encoding="utf-8") + "\n# user custom line — keep me\n"
        hook.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert hook.read_text(encoding="utf-8") == edited
        assert any("session-start.sh" in m for m in result.get("modified", []))

    def test_live_path_preserves_user_edited_skill(self, initialized_repo: Path) -> None:
        """A user-edited skill SKILL.md survives update_project() and is reported."""
        skill = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
        edited = skill.read_text(encoding="utf-8") + "\n<!-- user note: keep me -->\n"
        skill.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert skill.read_text(encoding="utf-8") == edited
        assert any("trw-deliver" in m and "SKILL.md" in m for m in result.get("modified", []))

    def test_unedited_hook_still_updates(self, initialized_repo: Path) -> None:
        """An un-edited hook (on-disk hash matches manifest) is still updated.

        Focused: the recorded manifest hash matches the (stale) on-disk content,
        so the guard reports NOT-modified and the newer bundled content overwrites.
        """
        import hashlib

        from trw_mcp.bootstrap._template_updater import _update_hooks
        from trw_mcp.bootstrap._utils import _DATA_DIR

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        stale = "#!/bin/bash\n# stale bundled version\n"
        hook.write_text(stale, encoding="utf-8")
        stale_hash = hashlib.sha256(stale.encode("utf-8")).hexdigest()

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(
            initialized_repo,
            _DATA_DIR,
            result,
            dry_run=False,
            manifest_hashes={"session-start.sh": stale_hash},
        )

        assert hook.read_text(encoding="utf-8") != stale
        assert not any("session-start.sh" in m for m in result["modified"])

    def test_unedited_skill_still_updates(self, initialized_repo: Path) -> None:
        """An un-edited skill SKILL.md (hash matches manifest) is still updated."""
        import hashlib

        from trw_mcp.bootstrap._template_updater import _update_skills
        from trw_mcp.bootstrap._utils import _DATA_DIR

        skill = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
        stale = "# stale skill body\n"
        skill.write_text(stale, encoding="utf-8")
        stale_hash = hashlib.sha256(stale.encode("utf-8")).hexdigest()

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_skills(
            initialized_repo,
            _DATA_DIR,
            result,
            dry_run=False,
            manifest_hashes={"trw-deliver/SKILL.md": stale_hash},
        )

        assert skill.read_text(encoding="utf-8") != stale
        assert not any("trw-deliver/SKILL.md" in m for m in result["modified"])


@pytest.mark.unit
class TestUpdatePreservesUserEditsWithoutManifest:
    """Round-3 audit: hooks/skills had NO framework baseline, so a missing/corrupt/
    pre-hash manifest (``manifest_hashes is None``) silently clobbered user edits.

    Only agents carried a framework-content baseline (``_framework_agent_hashes``).
    The fix derives a bundled-content baseline for hooks/skills too and threads it
    through ``_guarded_copy_update`` so preservation is decidable without a manifest
    and fails toward preservation on divergence.
    """

    def test_corrupt_manifest_preserves_user_edited_hook(self, initialized_repo: Path) -> None:
        """A user-edited hook survives update_project() even with a corrupt manifest.

        The corrupt manifest degrades ``_read_manifest`` → None, so the live path
        passes ``manifest_hashes=None``; the framework-content baseline must still
        recognize the divergence and preserve + report the edit.
        """
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE

        (initialized_repo / ".trw" / _MANIFEST_FILE).write_text("{ unclosed: [1, 2", encoding="utf-8")

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        edited = hook.read_text(encoding="utf-8") + "\n# user custom line — keep me\n"
        hook.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]
        assert hook.read_text(encoding="utf-8") == edited
        assert any("session-start.sh" in m for m in result.get("modified", []))

    def test_no_manifest_preserves_user_edited_hook_unit(self, initialized_repo: Path) -> None:
        """Focused: ``_update_hooks`` with ``manifest_hashes=None`` preserves a diverged hook."""
        from trw_mcp.bootstrap._template_updater import _update_hooks
        from trw_mcp.bootstrap._utils import _DATA_DIR

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        edited = (_DATA_DIR / "hooks" / "session-start.sh").read_text(encoding="utf-8") + "\n# diverged\n"
        hook.write_text(edited, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(initialized_repo, _DATA_DIR, result, dry_run=False, manifest_hashes=None)

        assert hook.read_text(encoding="utf-8") == edited
        assert any("session-start.sh" in m for m in result["modified"])

    def test_no_manifest_pristine_hook_still_updates(self, initialized_repo: Path) -> None:
        """A hook matching the shipped bundle is framework-managed, so it still updates.

        Guards against the fix over-preserving: fail-toward-preservation must only
        trigger on genuine divergence, not on a pristine framework file.
        """
        from trw_mcp.bootstrap._template_updater import _update_hooks
        from trw_mcp.bootstrap._utils import _DATA_DIR

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        shipped = (_DATA_DIR / "hooks" / "session-start.sh").read_text(encoding="utf-8")
        hook.write_text(shipped, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(initialized_repo, _DATA_DIR, result, dry_run=False, manifest_hashes=None)

        assert not any("session-start.sh" in m for m in result["modified"])
        assert any("session-start.sh" in u for u in result["updated"])


@pytest.mark.unit
class TestReadManifestCorruptDegrades:
    """P2-3 round-2 audit: a malformed managed-artifacts.yaml must not crash update."""

    def test_read_manifest_malformed_yaml_returns_none(self, initialized_repo: Path) -> None:
        """_read_manifest degrades to None on StateError (malformed YAML), not raises."""
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE, _read_manifest

        manifest_path = initialized_repo / ".trw" / _MANIFEST_FILE
        # Unbalanced flow mapping — a hard YAML parse error → FileStateReader raises StateError.
        manifest_path.write_text("{ unclosed: [1, 2, 3", encoding="utf-8")

        assert _read_manifest(initialized_repo) is None

    def test_update_project_survives_corrupt_manifest(self, initialized_repo: Path) -> None:
        """update_project() completes without errors when the manifest is corrupt."""
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE

        manifest_path = initialized_repo / ".trw" / _MANIFEST_FILE
        manifest_path.write_text("{ unclosed: [1, 2, 3", encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]


@pytest.mark.unit
class TestLivePathExistingAgentRelabel:
    """P2-4 round-2 audit: an existing agent on the live path is 'updated', not 'created'."""

    def test_existing_agent_reported_updated_not_created(self, initialized_repo: Path) -> None:
        """A pre-existing agent is reclassified from created→updated on live update.

        Exercises the relabel block in ``_apply_agent_update`` (``_install_one_agent``
        always records 'created'; an existing dest is semantically an update).
        """
        agent = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        assert agent.exists()

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert str(agent) in result["updated"]
        assert str(agent) not in result["created"]


class TestCompactCanonUpdateOrdering:
    """PRD-CORE-207 FR07/NFR03: ordered compact-generation update + legacy compat."""

    def _registry(self):
        from trw_mcp.canons.registry import bundled_manifest_bytes, clear_cache, load_registry

        clear_cache()
        return load_registry(bundled_manifest_bytes())

    def test_compact_canon_update_orders_artifacts_before_instruction_pointer(self) -> None:
        """FR07: every body/stamp write precedes the single terminal pointer flip."""
        from trw_mcp.canons.registry import compact_generation_write_plan

        registry = self._registry()
        plan = compact_generation_write_plan(registry, stamp_path=".trw/frameworks/VERSION.yaml")

        kinds = [kind for kind, _path in plan]
        # Exactly one instruction-pointer step, and it is last (fail-safe ordering).
        assert kinds.count("instruction_pointer") == 1
        assert kinds[-1] == "instruction_pointer"
        pointer_idx = kinds.index("instruction_pointer")
        # Every body / inventory / stamp write happens strictly before the flip.
        for i, kind in enumerate(kinds):
            if kind in {"body", "inventory", "stamp"}:
                assert i < pointer_idx
        # Both compact cores and both references are written before the pointer moves.
        body_paths = {path for kind, path in plan if kind == "body"}
        for compiled in registry.compiled_canons:
            assert compiled.compact_core in body_paths
            assert compiled.reference in body_paths
            assert compiled.combined in body_paths  # legacy body still written

    def test_legacy_combined_canon_paths_remain_compatible(self) -> None:
        """NFR03: legacy combined paths stay declared outputs with a >=2 release window."""
        from trw_mcp.canons.registry import (
            COMBINED_COMPATIBILITY_MIN_RELEASES,
            legacy_combined_paths,
        )

        registry = self._registry()
        legacy = legacy_combined_paths(registry)
        # The legacy combined filenames survive the migration (not removed).
        assert any(p.endswith("data/framework.md") for p in legacy)
        assert any(p.endswith("data/aaref.md") for p in legacy)
        # Each legacy combined path is still a manifest-declared artifact source.
        sources = {a.authoring_source for a in registry.artifacts}
        for path in legacy:
            assert path in sources
        # The compatibility window is at least two minor releases (documented horizon).
        assert COMBINED_COMPATIBILITY_MIN_RELEASES >= 2
