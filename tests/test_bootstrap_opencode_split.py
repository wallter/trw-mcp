"""Split bootstrap OpenCode tests."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.bootstrap._opencode import (
    _parse_jsonc,
    generate_agents_md,
    generate_opencode_config,
    install_opencode_agents,
    install_opencode_commands,
    install_opencode_skills,
    load_opencode_skill_inventory,
    merge_opencode_json,
)


class TestOpenCodeBootstrap:
    """FR11: OpenCode Bootstrap Configuration."""

    def test_fr11_opencode_json_created(self, tmp_path: Path) -> None:
        result = generate_opencode_config(tmp_path)
        assert "opencode.json" in result["created"]
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert "trw" in config["mcp"]

    def test_fr11_opencode_json_permissions(self, tmp_path: Path) -> None:
        generate_opencode_config(tmp_path)
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["permission"]["bash"] == "ask"
        assert config["permission"]["write"] == "ask"
        assert config["permission"]["edit"] == "ask"

    def test_fr11_opencode_json_mcp_local(self, tmp_path: Path) -> None:
        generate_opencode_config(tmp_path)
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["mcp"]["trw"]["type"] == "local"
        assert config["mcp"]["trw"]["command"] == ["trw-mcp", "--debug"]
        assert "args" not in config["mcp"]["trw"]

    def test_fr11_agents_md_created(self, tmp_path: Path) -> None:
        result = generate_agents_md(tmp_path, "## TRW Section\nContent here")
        assert "AGENTS.md" in result["created"]
        content = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content

    def test_fr11_agents_md_same_markers(self, tmp_path: Path) -> None:
        generate_agents_md(tmp_path, "Test content")
        content = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content

    def test_fr11_agents_md_updates_existing(self, tmp_path: Path) -> None:
        # Write initial
        generate_agents_md(tmp_path, "Version 1")
        # Update
        generate_agents_md(tmp_path, "Version 2")
        content = (tmp_path / "AGENTS.md").read_text()
        assert "Version 2" in content
        assert "Version 1" not in content

    def test_fr11_agents_md_preserves_user_content(self, tmp_path: Path) -> None:
        # Write file with user content + markers
        (tmp_path / "AGENTS.md").write_text(
            "# My Project\n\nUser content here\n\n"
            "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
            "<!-- trw:start -->\nOld TRW\n<!-- trw:end -->\n\n"
            "More user content\n"
        )
        generate_agents_md(tmp_path, "New TRW content")
        content = (tmp_path / "AGENTS.md").read_text()
        assert "User content here" in content
        assert "More user content" in content
        assert "New TRW content" in content
        assert "Old TRW" not in content

    def test_opencode_commands_installed(self, tmp_path: Path) -> None:
        result = install_opencode_commands(tmp_path)
        assert ".opencode/commands/trw-deliver.md" in result["created"]
        assert (tmp_path / ".opencode" / "commands" / "trw-prd-ready.md").exists()
        assert (tmp_path / ".opencode" / "commands" / "trw-sprint-team.md").exists()

    def test_opencode_agents_installed(self, tmp_path: Path) -> None:
        result = install_opencode_agents(tmp_path)
        assert ".opencode/agents/trw-researcher.md" in result["created"]
        content = (tmp_path / ".opencode" / "agents" / "trw-reviewer.md").read_text(encoding="utf-8")
        assert "mode: subagent" in content
        assert "write: deny" in content

    def test_opencode_skills_inventory_curated(self) -> None:
        inventory = load_opencode_skill_inventory()
        assert inventory["trw-deliver"]["disposition"] == "portable"
        assert inventory["trw-sprint-team"]["disposition"] == "exclude"

    def test_opencode_skills_installed_curated_subset(self, tmp_path: Path) -> None:
        result = install_opencode_skills(tmp_path)
        assert ".opencode/skills/trw-deliver/SKILL.md" in result["created"]
        assert (tmp_path / ".opencode" / "skills" / "trw-prd-ready" / "SKILL.md").exists()
        assert not (tmp_path / ".opencode" / "skills" / "trw-sprint-team").exists()
        content = (tmp_path / ".opencode" / "skills" / "trw-deliver" / "SKILL.md").read_text(encoding="utf-8")
        assert "trw_claude_md_sync" not in content
        assert "TaskList" not in content


class TestOpenCodeJsonMerge:
    """FR16: opencode.json Smart Merge."""

    def test_fr16_merge_preserves_other_servers(self) -> None:
        existing: dict[str, object] = {"mcp": {"other-server": {"type": "remote", "url": "http://x"}}}
        trw: dict[str, object] = {"type": "local", "command": ["trw-mcp"]}
        result = merge_opencode_json(existing, trw)
        assert "other-server" in result["mcp"]
        assert "trw" in result["mcp"]

    def test_fr16_merge_preserves_user_permissions(self) -> None:
        existing: dict[str, object] = {"permission": {"bash": "never"}, "mcp": {}}
        trw: dict[str, object] = {"type": "local", "command": ["trw-mcp"]}
        result = merge_opencode_json(existing, trw)
        assert result["permission"]["bash"] == "never"

    def test_fr16_merge_preserves_model(self) -> None:
        existing: dict[str, object] = {
            "model": "ollama/qwen3-coder-next",
            "mcp": {},
        }
        trw: dict[str, object] = {"type": "local", "command": ["trw-mcp"]}
        result = merge_opencode_json(existing, trw)
        assert result["model"] == "ollama/qwen3-coder-next"

    def test_fr16_merge_adds_trw_entry(self) -> None:
        existing: dict[str, object] = {"mcp": {}}
        trw: dict[str, object] = {"type": "local", "command": ["trw-mcp"]}
        result = merge_opencode_json(existing, trw)
        assert result["mcp"]["trw"] == trw

    def test_fr16_merge_updates_existing_trw(self) -> None:
        existing: dict[str, object] = {"mcp": {"trw": {"type": "local", "command": ["old"]}}}
        trw: dict[str, object] = {
            "type": "local",
            "command": ["trw-mcp", "--debug"],
        }
        result = merge_opencode_json(existing, trw)
        assert result["mcp"]["trw"]["command"] == ["trw-mcp", "--debug"]

    def test_fr16_fresh_install_full_template(self, tmp_path: Path) -> None:
        result = generate_opencode_config(tmp_path)
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert "permission" in config
        assert "mcp" in config
        assert "trw" in config["mcp"]

    def test_fr16_jsonc_line_comments(self) -> None:
        jsonc = '{\n  // This is a comment\n  "key": "value"\n}'
        result = _parse_jsonc(jsonc)
        assert result["key"] == "value"

    def test_fr16_jsonc_block_comments(self) -> None:
        jsonc = '{\n  /* block\n  comment */\n  "key": "value"\n}'
        result = _parse_jsonc(jsonc)
        assert result["key"] == "value"

    def test_fr16_smart_merge_existing_file(self, tmp_path: Path) -> None:
        # Write existing opencode.json with another server
        (tmp_path / "opencode.json").write_text(
            json.dumps(
                {
                    "model": "ollama/qwen3-coder-next",
                    "mcp": {"other": {"type": "remote", "url": "http://x"}},
                }
            )
        )
        result = generate_opencode_config(tmp_path)
        assert "opencode.json" in result["updated"]
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["model"] == "ollama/qwen3-coder-next"
        assert "other" in config["mcp"]
        assert "trw" in config["mcp"]


class TestOpenCodeJsonReadHardening:
    """Hardening of the FR16 smart-merge read seam (_read_existing_opencode_config).

    The reader must fail closed and content-free: malformed/unreadable input
    returns an error result and leaves the user's file untouched, never crashes,
    and never leaks raw file bytes (e.g. secret markers) into the result.
    """

    def test_jsonc_with_comments_smart_merges_and_preserves_user_keys(self, tmp_path: Path) -> None:
        # Valid JSONC (line + block comments) must still smart-merge TRW while
        # preserving the user's model / permission / other-server keys.
        (tmp_path / "opencode.json").write_text(
            "{\n"
            "  // user picked a local model\n"
            '  "model": "ollama/qwen3-coder-next",\n'
            "  /* keep my strict perms */\n"
            '  "permission": { "bash": "never" },\n'
            '  "mcp": { "other": { "type": "remote", "url": "http://x" } }\n'
            "}\n"
        )
        result = generate_opencode_config(tmp_path)
        assert "opencode.json" in result["updated"]
        assert result["errors"] == []
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert config["model"] == "ollama/qwen3-coder-next"
        assert config["permission"]["bash"] == "never"
        assert "other" in config["mcp"]
        assert "trw" in config["mcp"]

    def test_non_utf8_existing_config_errors_and_leaves_bytes_unchanged(self, tmp_path: Path) -> None:
        config_path = tmp_path / "opencode.json"
        original = b'{"model": "\xff\xfe invalid utf-8"}'
        config_path.write_bytes(original)

        # Must not raise UnicodeDecodeError.
        result = generate_opencode_config(tmp_path)

        assert result["errors"] == ["Failed to read opencode.json: non_utf8"]
        assert result["updated"] == []
        assert result["created"] == []
        # Original bytes are preserved — the merge path never overwrote them.
        assert config_path.read_bytes() == original

    def test_top_level_non_object_errors_and_does_not_overwrite(self, tmp_path: Path) -> None:
        config_path = tmp_path / "opencode.json"
        original = "[1, 2, 3]\n"
        config_path.write_text(original, encoding="utf-8")

        result = generate_opencode_config(tmp_path)

        assert result["errors"] == ["Failed to read opencode.json: non_object"]
        assert result["updated"] == []
        # User's (array) document is left untouched — not overwritten with a template.
        assert config_path.read_text(encoding="utf-8") == original

    def test_malformed_json_with_secret_marker_is_content_free(self, tmp_path: Path) -> None:
        secret = "S3CRET-TOKEN-do-not-leak-9f2a"
        config_path = tmp_path / "opencode.json"
        # Unterminated object -> JSONDecodeError. The secret sits in the payload.
        config_path.write_text(f'{{ "api_key": "{secret}" ', encoding="utf-8")

        import structlog

        with structlog.testing.capture_logs() as logs:
            result = generate_opencode_config(tmp_path)

        assert result["errors"] == ["Failed to read opencode.json: malformed_json"]
        assert result["updated"] == []
        # The secret must not leak into the result errors or any captured log event.
        joined_errors = " ".join(result["errors"])
        assert secret not in joined_errors
        assert secret not in str(logs)

    def test_unreadable_existing_config_errors_without_crash(self, tmp_path: Path) -> None:
        # A directory at opencode.json makes read_bytes() raise OSError (IsADirectory).
        (tmp_path / "opencode.json").mkdir()

        result = generate_opencode_config(tmp_path)

        assert result["errors"] == ["Failed to read opencode.json: unreadable"]
        assert result["updated"] == []
        assert result["created"] == []
