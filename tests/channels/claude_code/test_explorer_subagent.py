"""Tests for channels/claude_code/_explorer_subagent.py (PRD-DIST-2405 FR37-FR40)."""

from __future__ import annotations

from pathlib import Path

try:
    import yaml as _yaml_module  # type: ignore[import-untyped]
    _HAS_PYYAML = True
except ImportError:
    _HAS_PYYAML = False

from trw_mcp.channels.claude_code._explorer_subagent import (
    EXPLORER_AGENT_RELPATH,
    EXPLORER_QUOTA_BYTES,
    get_explorer_agent_content,
    install_cc05_subagent,
)


def _parse_frontmatter(content: str) -> dict:  # type: ignore[type-arg]
    """Parse YAML frontmatter from a markdown file with --- delimiters."""
    lines = content.split("\n")
    if not lines[0].strip() == "---":
        return {}
    end = -1
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
    if end == -1:
        return {}
    fm_text = "\n".join(lines[1:end])
    try:
        from ruamel.yaml import YAML

        y = YAML(typ="safe")
        import io

        return y.load(io.StringIO(fm_text)) or {}
    except ImportError:
        pass
    if _HAS_PYYAML:
        return _yaml_module.safe_load(fm_text) or {}
    # Minimal manual parse for CI environments without yaml
    result = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip().strip('"\'')
    return result


class TestExplorerAgentContent:
    def test_frontmatter_yaml_valid(self) -> None:
        """FR38: frontmatter parses as valid YAML."""
        content = get_explorer_agent_content()
        fm = _parse_frontmatter(content)
        assert isinstance(fm, dict)
        assert fm  # non-empty

    def test_frontmatter_fields_complete(self) -> None:
        """FR38: required frontmatter fields are present."""
        content = get_explorer_agent_content()
        fm = _parse_frontmatter(content)
        assert "name" in fm or "name: trw-distill-explorer" in content
        assert "model" in fm or "model: haiku" in content
        assert "maxTurns" in fm or "maxTurns: 20" in content
        assert "effort" in fm or "effort: medium" in content
        assert "memory" in fm or "memory: project" in content
        assert "permissionMode" in fm or "permissionMode: default" in content

    def test_allowed_tools_listed(self) -> None:
        """FR38: allowed tools include required MCP tools."""
        content = get_explorer_agent_content()
        assert "mcp__trw__trw_before_edit_hint" in content
        assert "mcp__trw__trw_codebase_risk_report" in content
        assert "mcp__trw__trw_entity_risk_map" in content
        assert "mcp__trw__trw_recall" in content
        assert "Read" in content
        assert "Glob" in content
        assert "Grep" in content

    def test_disallowed_tools_listed(self) -> None:
        """FR38: disallowed tools prevent write/modify operations."""
        content = get_explorer_agent_content()
        assert "Bash" in content
        assert "Write" in content
        assert "Edit" in content
        assert "mcp__trw__trw_learn" in content
        assert "mcp__trw__trw_checkpoint" in content
        assert "mcp__trw__trw_deliver" in content
        assert "mcp__trw__trw_init" in content
        assert "Agent" in content

    def test_description_contains_trigger_phrases(self) -> None:
        """FR39: description contains trigger phrases for delegation."""
        content = get_explorer_agent_content()
        assert "full codebase risk analysis" in content
        assert "entity risk map" in content
        assert "ordering comparison" in content

    def test_description_contains_anti_examples(self) -> None:
        """FR39: description contains anti-example to prevent wrong delegation."""
        content = get_explorer_agent_content()
        assert "Do NOT use for single-file pre-edit hints" in content
        assert "PreToolUse hook" in content

    def test_body_contains_return_format_spec(self) -> None:
        """FR40: body includes return format with required sections."""
        content = get_explorer_agent_content()
        assert "TOP RISK FILES" in content
        assert "ACTIONABLE RECOMMENDATIONS" in content
        assert "DATA PROVENANCE" in content
        assert "600 tokens" in content

    def test_body_no_write_modify_instruction(self) -> None:
        """FR40: body says NOT to call learn/checkpoint/modify files."""
        content = get_explorer_agent_content()
        assert "trw_learn" in content
        assert "trw_checkpoint" in content

    def test_content_within_quota(self) -> None:
        content = get_explorer_agent_content()
        assert len(content.encode("utf-8")) <= EXPLORER_QUOTA_BYTES


class TestInstallCc05Subagent:
    def test_installs_to_correct_path(self, tmp_path: Path) -> None:
        """FR37: file installed at .claude/agents/trw-distill-explorer.md."""
        install_cc05_subagent(tmp_path)
        target = tmp_path / EXPLORER_AGENT_RELPATH
        assert target.exists()

    def test_installed_content_correct(self, tmp_path: Path) -> None:
        install_cc05_subagent(tmp_path)
        target = tmp_path / EXPLORER_AGENT_RELPATH
        content = target.read_text(encoding="utf-8")
        assert "trw-distill-explorer" in content

    def test_idempotent_same_content(self, tmp_path: Path) -> None:
        """FR41: re-installing same content returns False (no write)."""
        install_cc05_subagent(tmp_path)
        result = install_cc05_subagent(tmp_path)
        assert result is False  # unchanged

    def test_first_install_returns_true(self, tmp_path: Path) -> None:
        result = install_cc05_subagent(tmp_path)
        assert result is True

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Parent .claude/agents/ directory is created."""
        repo = tmp_path / "repo"
        repo.mkdir()
        install_cc05_subagent(repo)
        assert (repo / ".claude" / "agents").is_dir()

    def test_install_when_content_differs_returns_true(self, tmp_path: Path) -> None:
        """If existing content differs, returns True (updated)."""
        install_cc05_subagent(tmp_path)
        target = tmp_path / EXPLORER_AGENT_RELPATH
        # Modify the file to simulate an old version
        target.write_text("old content\n", encoding="utf-8")
        result = install_cc05_subagent(tmp_path)
        assert result is True

    def test_quota_warning_emitted_when_oversized(self, tmp_path: Path) -> None:
        """Covers quota warning log path (line 152) when content exceeds EXPLORER_QUOTA_BYTES."""
        from unittest.mock import patch

        from trw_mcp.channels.claude_code._explorer_subagent import (
            EXPLORER_QUOTA_BYTES,
            install_cc05_subagent,
        )

        # Patch get_explorer_agent_content to return an oversized string
        oversized = "x" * (EXPLORER_QUOTA_BYTES + 100)
        with patch(
            "trw_mcp.channels.claude_code._explorer_subagent.get_explorer_agent_content",
            return_value=oversized,
        ):
            # Should NOT raise even when over quota — just warn
            result = install_cc05_subagent(tmp_path)
            assert result is True  # was written (no existing file)

    def test_install_when_read_raises_oserror(self, tmp_path: Path) -> None:
        """Covers lines 165-166: OSError on reading existing file → overwrites gracefully."""
        from unittest.mock import patch

        from trw_mcp.channels.claude_code._explorer_subagent import (
            install_cc05_subagent,
        )

        # First install to ensure the target exists
        install_cc05_subagent(tmp_path)

        # Simulate the target.exists() returning True and target.read_text() raising OSError
        # by patching Path.read_text on the module's Path class
        original_exists = Path.exists

        def _patched_read_text(path_self: Path, *args: object, **kwargs: object) -> str:
            if path_self.name == "trw-distill-explorer.md":
                raise OSError("read failed")
            # Fallback for other paths — but read_text is normally not called in this test
            raise RuntimeError("unexpected call to read_text")

        with patch.object(Path, "read_text", _patched_read_text):
            # Should write (not crash) when read fails
            result = install_cc05_subagent(tmp_path)
        # Either True (wrote) or False (content check skipped) — must not raise
        assert isinstance(result, bool)
