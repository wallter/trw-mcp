"""Bootstrap coverage tests for dry-run update branches."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch


class TestBootstrapDryRunBranches:
    """Cover dry_run branches in update_project that require specific file states."""

    def _make_trw_target(self, tmp_path: Path) -> Path:
        """Create a minimal target dir with .trw/ so update_project doesn't error.

        PRD-INFRA-192-NFR02: update_project refuses before touching artifacts
        when the manifest is missing/corrupt/unsupported-schema, so this
        fixture writes a valid ``version: 2`` manifest — these tests exercise
        dry-run branches, not the refusal path.
        """
        target = tmp_path / "target"
        target.mkdir()
        (target / ".git").mkdir()  # update_project now requires a real git repo
        (target / ".trw").mkdir()
        (target / ".trw" / "managed-artifacts.yaml").write_text("version: 2\ncontent_hashes: {}\n", encoding="utf-8")
        (target / ".claude" / "hooks").mkdir(parents=True)
        (target / ".claude" / "skills").mkdir(parents=True)
        (target / ".claude" / "agents").mkdir(parents=True)
        return target

    def test_dry_run_hook_identical_file_skips_update(self, tmp_path: Path) -> None:
        """A dry run does not report an identical hook as updated."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        hooks_source = bs._DATA_DIR / "hooks"
        hook_files = [f for f in hooks_source.iterdir() if f.suffix == ".sh"]

        hook_src = hook_files[0]
        dest_hook = target / ".claude" / "hooks" / hook_src.name
        shutil.copy2(hook_src, dest_hook)
        dest_hook.chmod(0o755)  # installed hooks are executable

        result = bs.update_project(target, dry_run=True)
        assert f".claude/hooks/{hook_src.name}" not in result["updated"], "an identical file is not a change"

    def test_dry_run_hook_different_content_preserved_as_modified(self, tmp_path: Path) -> None:
        """PRD-FIX-068-FR05: with no manifest baseline, a hook whose content
        diverges from the bundled source is indistinguishable from a user edit
        and MUST be preserved (reported in result['modified']), never clobbered
        — the pre-FR05 behavior this test used to assert was the bug."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        hooks_source = bs._DATA_DIR / "hooks"
        hook_files = [f for f in hooks_source.iterdir() if f.suffix == ".sh"]

        hook_src = hook_files[0]
        dest_hook = target / ".claude" / "hooks" / hook_src.name
        original = dest_hook.read_text(encoding="utf-8") if dest_hook.exists() else None
        dest_hook.write_text("#!/bin/bash\necho 'user customization'\n", encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        assert f".claude/hooks/{hook_src.name}" not in result["updated"]
        assert any(hook_src.name in s for s in result.get("modified", []))
        # The user's content survives (dry-run or not — it was never a copy target).
        assert dest_hook.read_text(encoding="utf-8") == "#!/bin/bash\necho 'user customization'\n"
        del original

    def test_dry_run_skill_file_identical_no_update(self, tmp_path: Path) -> None:
        """Line 305: dry_run skill file identical — not added to updated list."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        skills_source = bs._DATA_DIR / "skills"
        skill_dirs = [d for d in skills_source.iterdir() if d.is_dir()]
        skill_dir = skill_dirs[0]
        skill_files = [f for f in skill_dir.iterdir() if f.is_file()]
        skill_file = skill_files[0]
        dest_skill_dir = target / ".claude" / "skills" / skill_dir.name
        dest_skill_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_skill_dir / skill_file.name
        shutil.copy2(skill_file, dest_file)

        result = bs.update_project(target, dry_run=True)
        assert not any(skill_file.name in s for s in result["updated"]), (
            f"Identical skill file should not be flagged: {would_update}"
        )

    def test_dry_run_skill_file_different_preserved_as_modified(self, tmp_path: Path) -> None:
        """PRD-FIX-068-FR05: a skill file diverging from the bundled source with
        no manifest baseline is preserved as user-modified, not overwritten."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        skills_source = bs._DATA_DIR / "skills"
        skill_dirs = [d for d in skills_source.iterdir() if d.is_dir()]
        skill_dir = skill_dirs[0]
        skill_files = [f for f in skill_dir.iterdir() if f.is_file()]
        skill_file = skill_files[0]
        dest_skill_dir = target / ".claude" / "skills" / skill_dir.name
        dest_skill_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_skill_dir / skill_file.name
        dest_file.write_text("# user customization that differs", encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        assert not any(skill_file.name in s for s in result["updated"])
        assert any(skill_file.name in s for s in result.get("modified", []))
        assert dest_file.read_text(encoding="utf-8") == "# user customization that differs"

    def test_dry_run_new_skill_file_would_create(self, tmp_path: Path) -> None:
        """A missing skill file is reported as created by the dry run."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        result = bs.update_project(target, dry_run=True)
        assert any(p.startswith(".claude/skills/") for p in result["created"])

    def test_dry_run_agent_file_identical_not_flagged(self, tmp_path: Path) -> None:
        """dry_run agent identical to the RESOLVED form is not reported as updated.

        sub_5ctrrLJ: agents are materialized through the capability-tier resolver
        (``model: frontier`` -> ``model: opus``), so "identical" means matching the
        RESOLVED rendering the real update would write — not the raw bundled tier
        form. Writing the resolved form to dest must leave it un-flagged.
        """
        from trw_mcp import bootstrap as bs
        from trw_mcp.bootstrap._version_manifest import _render_agent

        target = self._make_trw_target(tmp_path)
        agents_source = bs._DATA_DIR / "agents"
        agent_files = [f for f in agents_source.iterdir() if f.suffix == ".md"]
        agent_file = agent_files[0]
        dest_agent = target / ".claude" / "agents" / agent_file.name
        resolved = _render_agent(agent_file, client="claude-code")
        assert resolved is not None
        dest_agent.write_text(resolved, encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        assert f".claude/agents/{agent_file.name}" not in result["updated"]

    def test_dry_run_agent_different_content_flags_would_update(self, tmp_path: Path) -> None:
        """Line 330 alt path: a framework-recognized-but-stale agent flags would-update.

        No manifest exists on this bare target, so an ARBITRARY body would now be
        treated as a genuine user edit (preserved) under the reconciled guard
        (P1-7 round-2 audit). To exercise the dry-run "would update" branch the
        dest must be a recognized framework rendering that differs from the
        resolved form — the raw bundled ``model: frontier`` tier form, which
        self-heals to ``model: opus``.
        """
        from trw_mcp import bootstrap as bs

        agents_source = bs._DATA_DIR / "agents"
        agent_files = [f for f in agents_source.iterdir() if f.suffix == ".md"]
        # Pick a frontier-tier agent whose raw form differs from its resolved form.
        agent_file = next(f for f in agent_files if "model: frontier" in f.read_text(encoding="utf-8"))

        target = self._make_trw_target(tmp_path)
        dest_agent = target / ".claude" / "agents" / agent_file.name
        dest_agent.write_text(agent_file.read_text(encoding="utf-8"), encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        assert f".claude/agents/{agent_file.name}" in result["updated"]

    def test_dry_run_new_agent_file_would_create(self, tmp_path: Path) -> None:
        """A missing agent file is reported as created by the dry run."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        result = bs.update_project(target, dry_run=True)
        assert any(p.startswith(".claude/agents/") for p in result["created"])

    def test_update_project_agents_md_write_failure(self, tmp_path: Path) -> None:
        """An unwritable AGENTS.md is reported, never silently swallowed.

        PUBLISH-RACE-HARDEN: the instruction-file guard stages the new bytes through
        ``_proven_replace._write_new`` and links them at the name, so a failure there
        is what an unwritable AGENTS.md looks like; it must be reported as a write
        failure, never as a concurrent save.
        """
        from trw_mcp import bootstrap as bs
        from trw_mcp.bootstrap import _proven_replace

        target = self._make_trw_target(tmp_path)
        agents = target / "AGENTS.md"
        before = agents.read_bytes() if agents.exists() else None
        staged: list[str] = []
        real_create, real_replace = _proven_replace.create_exclusive, _proven_replace.replace_proven

        def failing_write_new(sfd: int, new: bytes, mode: int | None) -> None:
            raise OSError(13, "permission denied")

        def only_agents_md(real: Any) -> Any:
            def call(path: Path, *args: Any) -> Any:
                if path.name != "AGENTS.md":
                    return real(path, *args)
                staged.append(path.name)
                with patch.object(_proven_replace, "_write_new", failing_write_new):
                    return real(path, *args)

            return call

        with (
            patch.object(_proven_replace, "create_exclusive", only_agents_md(real_create)),
            patch.object(_proven_replace, "replace_proven", only_agents_md(real_replace)),
        ):
            result = bs.update_project(target, dry_run=False)

        agents_errors = [e for e in result["errors"] if "AGENTS.md" in e]
        assert agents_errors, result["errors"]
        assert any("write_failed" in e and "permission denied" in e for e in agents_errors), agents_errors
        assert staged
        assert (agents.read_bytes() if agents.exists() else None) == before
