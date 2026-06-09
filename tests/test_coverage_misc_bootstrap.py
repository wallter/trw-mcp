"""Bootstrap coverage tests for dry-run update branches."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

try:
    from trw_mcp.bootstrap._utils import _DATA_DIR as _BS_DATA_DIR
except ImportError:
    _BS_DATA_DIR = Path("/nonexistent")

_HOOKS_DIR = _BS_DATA_DIR / "hooks"
_SKILLS_DIR = _BS_DATA_DIR / "skills"
_AGENTS_DIR = _BS_DATA_DIR / "agents"

_HAS_HOOKS_DIR = _HOOKS_DIR.is_dir()
_HOOK_FILES = list(_HOOKS_DIR.glob("*.sh")) if _HAS_HOOKS_DIR else []
_HAS_HOOK_FILES = len(_HOOK_FILES) > 0

_HAS_SKILLS_DIR = _SKILLS_DIR.is_dir()
_SKILL_DIRS = [d for d in _SKILLS_DIR.iterdir() if d.is_dir()] if _HAS_SKILLS_DIR else []
_HAS_SKILL_DIRS = len(_SKILL_DIRS) > 0
_FIRST_SKILL_DIR = _SKILL_DIRS[0] if _HAS_SKILL_DIRS else None
_SKILL_DIR_FILES = [f for f in _FIRST_SKILL_DIR.iterdir() if f.is_file()] if _FIRST_SKILL_DIR is not None else []
_HAS_SKILL_FILES = len(_SKILL_DIR_FILES) > 0

_HAS_AGENTS_DIR = _AGENTS_DIR.is_dir()
_AGENT_FILES = list(_AGENTS_DIR.glob("*.md")) if _HAS_AGENTS_DIR else []
_HAS_AGENT_FILES = len(_AGENT_FILES) > 0


class TestBootstrapDryRunBranches:
    """Cover dry_run branches in update_project that require specific file states."""

    def _make_trw_target(self, tmp_path: Path) -> Path:
        """Create a minimal target dir with .trw/ so update_project doesn't error."""
        target = tmp_path / "target"
        target.mkdir()
        (target / ".git").mkdir()  # update_project now requires a real git repo
        (target / ".trw").mkdir()
        (target / ".claude" / "hooks").mkdir(parents=True)
        (target / ".claude" / "skills").mkdir(parents=True)
        (target / ".claude" / "agents").mkdir(parents=True)
        return target

    @pytest.mark.skipif(not _HAS_HOOKS_DIR, reason="No hooks in bundled data")
    @pytest.mark.skipif(not _HAS_HOOK_FILES, reason="No .sh files in hooks")
    def test_dry_run_hook_identical_file_skips_update(self, tmp_path: Path) -> None:
        """Line 272: dry_run with identical hook — no 'would update' added."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        hooks_source = bs._DATA_DIR / "hooks"
        hook_files = [f for f in hooks_source.iterdir() if f.suffix == ".sh"]

        hook_src = hook_files[0]
        dest_hook = target / ".claude" / "hooks" / hook_src.name
        shutil.copy2(hook_src, dest_hook)

        result = bs.update_project(target, dry_run=True)
        would_update_names = [s for s in result.get("updated", []) if hook_src.name in s and "would update" in s]
        assert len(would_update_names) == 0, (
            f"Identical file should not appear in dry_run updated list: {would_update_names}"
        )

    @pytest.mark.skipif(not _HAS_HOOKS_DIR, reason="No hooks in bundled data")
    @pytest.mark.skipif(not _HAS_HOOK_FILES, reason="No .sh files in hooks")
    def test_dry_run_hook_different_content_flags_would_update(self, tmp_path: Path) -> None:
        """Line 272: dry_run with modified hook — appends 'would update'."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        hooks_source = bs._DATA_DIR / "hooks"
        hook_files = [f for f in hooks_source.iterdir() if f.suffix == ".sh"]

        hook_src = hook_files[0]
        dest_hook = target / ".claude" / "hooks" / hook_src.name
        dest_hook.write_text("#!/bin/bash\necho 'old version'\n", encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        would_update = [s for s in result.get("updated", []) if "would update" in s]
        assert any(hook_src.name in s for s in would_update)

    @pytest.mark.skipif(not _HAS_SKILLS_DIR, reason="No skills in bundled data")
    @pytest.mark.skipif(not _HAS_SKILL_DIRS, reason="No skill directories")
    @pytest.mark.skipif(not _HAS_SKILL_FILES, reason="No files in skill dir")
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
        would_update = [s for s in result.get("updated", []) if "would update" in s]
        assert not any(skill_file.name in s for s in would_update), (
            f"Identical skill file should not be flagged: {would_update}"
        )

    @pytest.mark.skipif(not _HAS_SKILLS_DIR, reason="No skills in bundled data")
    @pytest.mark.skipif(not _HAS_SKILL_DIRS, reason="No skill directories")
    @pytest.mark.skipif(not _HAS_SKILL_FILES, reason="No files in skill dir")
    def test_dry_run_skill_file_different_flags_would_update(self, tmp_path: Path) -> None:
        """Line 305 alt path: dry_run skill with different content."""
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
        dest_file.write_text("# old content that differs", encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        would_update = [s for s in result.get("updated", []) if "would update" in s]
        assert any(skill_file.name in s for s in would_update)

    @pytest.mark.skipif(not _HAS_SKILLS_DIR, reason="No skills in bundled data")
    @pytest.mark.skipif(not _HAS_SKILL_DIRS, reason="No skill directories")
    @pytest.mark.skipif(not _HAS_SKILL_FILES, reason="No files in skill dir")
    def test_dry_run_new_skill_file_would_create(self, tmp_path: Path) -> None:
        """Line 315 (else branch): skill file doesn't exist → would create."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        result = bs.update_project(target, dry_run=True)
        would_create = result.get("created", [])
        assert any("would create" in s for s in would_create)

    @pytest.mark.skipif(not _HAS_AGENTS_DIR, reason="No agents in bundled data")
    @pytest.mark.skipif(not _HAS_AGENT_FILES, reason="No .md agents")
    def test_dry_run_agent_file_identical_not_flagged(self, tmp_path: Path) -> None:
        """Line 330: dry_run agent identical — no 'would update' added."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        agents_source = bs._DATA_DIR / "agents"
        agent_files = [f for f in agents_source.iterdir() if f.suffix == ".md"]
        agent_file = agent_files[0]
        dest_agent = target / ".claude" / "agents" / agent_file.name
        shutil.copy2(agent_file, dest_agent)

        result = bs.update_project(target, dry_run=True)
        would_update = [s for s in result.get("updated", []) if "would update" in s]
        assert not any(agent_file.name in s for s in would_update)

    @pytest.mark.skipif(not _HAS_AGENTS_DIR, reason="No agents in bundled data")
    @pytest.mark.skipif(not _HAS_AGENT_FILES, reason="No .md agents")
    def test_dry_run_agent_different_content_flags_would_update(self, tmp_path: Path) -> None:
        """Line 330 alt path: agent file with different content."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        agents_source = bs._DATA_DIR / "agents"
        agent_files = [f for f in agents_source.iterdir() if f.suffix == ".md"]
        agent_file = agent_files[0]
        dest_agent = target / ".claude" / "agents" / agent_file.name
        dest_agent.write_text("# old agent content", encoding="utf-8")

        result = bs.update_project(target, dry_run=True)
        would_update = [s for s in result.get("updated", []) if "would update" in s]
        assert any(agent_file.name in s for s in would_update)

    @pytest.mark.skipif(not _HAS_AGENTS_DIR, reason="No agents in bundled data")
    @pytest.mark.skipif(not _HAS_AGENT_FILES, reason="No .md agents")
    def test_dry_run_new_agent_file_would_create(self, tmp_path: Path) -> None:
        """Line 340 (else branch): agent file doesn't exist → would create."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        result = bs.update_project(target, dry_run=True)
        would_create = result.get("created", [])
        assert any("would create" in s for s in would_create)

    def test_update_project_claude_md_write_failure(self, tmp_path: Path) -> None:
        """Lines 378-379: CLAUDE.md write fails → error appended."""
        from trw_mcp import bootstrap as bs

        target = self._make_trw_target(tmp_path)
        original_write_text = Path.write_text
        call_count = 0

        def patched_write_text(self: Path, content: str, encoding: str = "utf-8", **kw: Any) -> None:
            nonlocal call_count
            if self.name == "CLAUDE.md":
                call_count += 1
                raise OSError("permission denied")
            return original_write_text(self, content, encoding=encoding, **kw)

        with patch.object(Path, "write_text", patched_write_text):
            result = bs.update_project(target, dry_run=False)

        assert any("CLAUDE.md" in e for e in result["errors"])
        assert call_count >= 1
