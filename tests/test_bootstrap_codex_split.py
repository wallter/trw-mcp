"""Split bootstrap Codex tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomllib
from ruamel.yaml import YAML

from trw_mcp.bootstrap._codex import (
    codex_hooks_review_warning,
    codex_trw_hook_count,
    generate_codex_config,
    generate_codex_hooks,
    install_codex_skills,
    merge_codex_config,
)

_CODEX_SKILL_KEYS = {"name", "description", "allowed-tools", "license", "metadata"}


def _skill_frontmatter(path: Path) -> dict[str, object]:
    return _skill_frontmatter_text(path.read_text(encoding="utf-8"), str(path))


def _skill_frontmatter_text(content: str, label: str) -> dict[str, object]:
    assert content.startswith("---\n"), f"missing frontmatter: {label}"
    closing = content.find("\n---", 4)
    assert closing != -1, f"unterminated frontmatter: {label}"
    parsed = YAML(typ="safe").load(content[4:closing])
    assert isinstance(parsed, dict), f"frontmatter must be a mapping: {label}"
    return parsed


class TestCodexBootstrap:
    """Codex bootstrap configuration and smart-merge behavior."""

    def test_codex_config_created(self, tmp_path: Path) -> None:
        result = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in result["created"]
        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        # Absent means inherit the installed Codex default. TRW must not write a
        # flag that would switch off the hooks.json it installs (inert on 0.154.0,
        # live on older Codex). See _normalize_feature_flags.
        assert "hooks" not in config.get("features", {})
        assert "codex_hooks" not in config["features"]
        assert config["mcp_servers"]["trw"]["enabled"] is True
        assert "url" not in config["mcp_servers"]["trw"]
        assert config["mcp_servers"]["openaiDeveloperDocs"]["enabled"] is True
        assert "trw_session_start" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_build_check" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_checkpoint" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert config["model_instructions_file"] == "INSTRUCTIONS.md"
        assert ".codex/INSTRUCTIONS.md" not in config.get("project_doc_fallback_filenames", [])
        assert "AGENTS.md" not in config.get("project_doc_fallback_filenames", [])
        assert "AGENTS.override.md" not in config.get("project_doc_fallback_filenames", [])
        assert all(not entry["path"].endswith("/SKILL.md") for entry in config["skills"]["config"])
        assert any(entry["path"] == ".agents/skills/trw-deliver" for entry in config["skills"]["config"])

    def test_codex_hooks_json_created(self, tmp_path: Path) -> None:
        result = generate_codex_hooks(tmp_path)
        assert ".codex/hooks.json" in result["created"]
        hooks = json.loads((tmp_path / ".codex" / "hooks.json").read_text(encoding="utf-8"))
        assert "UserPromptSubmit" in hooks["hooks"]
        assert "PreToolUse" in hooks["hooks"]
        assert "PostToolUse" in hooks["hooks"]
        assert "Stop" in hooks["hooks"]

    def test_codex_hooks_review_warning_matches_current_review_gate(self) -> None:
        warning = codex_hooks_review_warning()

        assert codex_trw_hook_count() == 5
        assert "[features].hooks" in warning
        assert "[features].codex_hooks" in warning
        assert "Open /hooks" in warning
        assert "5 TRW-managed hooks" in warning
        assert "user-controlled Codex config" in warning

    def test_codex_hooks_merge_preserves_user_handlers(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [
                            {
                                "description": "user custom",
                                "hooks": [{"type": "command", "command": "echo custom"}],
                            }
                        ],
                        "Notification": [
                            {
                                "description": "notify",
                                "hooks": [{"type": "command", "command": "echo notify"}],
                            }
                        ],
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        result = generate_codex_hooks(tmp_path)
        assert ".codex/hooks.json" in result["updated"]
        hooks = json.loads((codex_dir / "hooks.json").read_text(encoding="utf-8"))
        assert hooks["hooks"]["Notification"][0]["description"] == "notify"
        assert hooks["hooks"]["UserPromptSubmit"][0]["description"] == "user custom"
        assert any(
            entry.get("description", "").startswith("TRW managed:") for entry in hooks["hooks"]["UserPromptSubmit"]
        )

    def test_codex_hooks_non_utf8_existing_fails_closed(self, tmp_path: Path) -> None:
        """A non-UTF-8 existing hooks.json must not crash and must be preserved.

        ``read_text(encoding="utf-8")`` raises ``UnicodeDecodeError`` (a
        ``ValueError``, not an ``OSError``), so the prior reader let it escape
        uncaught. The hardened reader fails closed: structural error, file
        bytes untouched so the user can recover their hooks.
        """
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        hooks_path = codex_dir / "hooks.json"
        original_bytes = b"\xff\xfe not valid utf-8 \x80\x81"
        hooks_path.write_bytes(original_bytes)

        result = generate_codex_hooks(tmp_path)

        assert ".codex/hooks.json" not in result["created"]
        assert ".codex/hooks.json" not in result.get("updated", [])
        assert any(".codex/hooks.json" in err for err in result["errors"])
        # File left byte-for-byte untouched (user hooks preserved for recovery).
        assert hooks_path.read_bytes() == original_bytes

    def test_codex_hooks_malformed_existing_fails_closed(self, tmp_path: Path) -> None:
        """A malformed existing hooks.json fails closed without leaking content."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        hooks_path = codex_dir / "hooks.json"
        secret = "SUPER_SECRET_TOKEN_should_not_leak"
        hooks_path.write_text('{"hooks": ' + secret, encoding="utf-8")

        result = generate_codex_hooks(tmp_path)

        assert any(".codex/hooks.json" in err for err in result["errors"])
        # Diagnostics are structural/content-free: the payload never leaks.
        assert all(secret not in err for err in result["errors"])
        assert hooks_path.read_text(encoding="utf-8") == '{"hooks": ' + secret

    def test_codex_hooks_non_object_existing_fails_closed(self, tmp_path: Path) -> None:
        """A top-level JSON array hooks.json fails closed and is preserved."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        hooks_path = codex_dir / "hooks.json"
        hooks_path.write_text("[1, 2, 3]\n", encoding="utf-8")

        result = generate_codex_hooks(tmp_path)

        assert any(".codex/hooks.json" in err for err in result["errors"])
        assert hooks_path.read_text(encoding="utf-8") == "[1, 2, 3]\n"

    def test_codex_hooks_force_overwrites_corrupt_existing(self, tmp_path: Path) -> None:
        """``force=True`` ignores the existing file and writes the TRW payload."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        hooks_path = codex_dir / "hooks.json"
        hooks_path.write_bytes(b"\xff\xfe garbage")

        result = generate_codex_hooks(tmp_path, force=True)

        assert ".codex/hooks.json" in result["updated"]
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        assert "Stop" in hooks["hooks"]

    # ``test_codex_agents_created`` / ``test_codex_agents_preserve_existing_edits_
    # without_force`` were deleted by PRD-CORE-252-FR04 with the
    # ``_CODEX_AGENT_TEMPLATES`` dictionary they exercised. Codex now receives
    # the bundled specialists as ``.codex/agents/*.toml``; destination,
    # preservation and TOML parseability are asserted in
    # ``tests/test_install_agents_destinations.py`` and
    # ``tests/test_agent_materialization_per_client.py``.

    def test_codex_skills_installed(self, tmp_path: Path) -> None:
        result = install_codex_skills(tmp_path)
        assert any(path.endswith("/SKILL.md") for path in result["created"])
        skill_path = tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md"
        assert skill_path.exists()
        content = skill_path.read_text(encoding="utf-8")
        assert "# TRW Deliver" in content
        assert "trw_deliver()" in content
        assert "allowed-tools:" not in content
        assert "disable-model-invocation:" not in content
        assert "user-invocable:" not in content
        assert "model: claude-" not in content

    def test_all_packaged_and_installed_codex_skills_use_supported_frontmatter(self, tmp_path: Path) -> None:
        """Every Codex skill uses only fields accepted by the current skill schema.

        Codex no longer forks the skill tree on disk (PRD-CORE-291-FR04); it
        renders the canonical corpus, so "packaged" is now the rendered text
        per ``skill_names("codex")`` rather than a glob over a deleted path.
        Three names (``trw-prd-groom``, ``trw-prd-review``, ``trw-exec-plan``)
        are internal readiness phases merged into a shared ``trw-prd-ready``
        directory instead of getting their own installed dir -- unchanged
        from before, just no longer expressed as "3 fewer packaged dirs".
        """
        from trw_mcp.bootstrap._client_skills import canonical_skills_dir, render_skill_md, skill_names

        names = skill_names("codex")
        assert names
        canonical_root = canonical_skills_dir()
        packaged_rendered = [
            (name, render_skill_md((canonical_root / name / "SKILL.md").read_text(encoding="utf-8"), "codex"))
            for name in names
        ]

        install_codex_skills(tmp_path)
        installed_root = tmp_path / ".agents" / "skills"
        installed = sorted(installed_root.glob("*/SKILL.md"))
        merged_phase_count = 3  # trw-prd-groom, trw-prd-review, trw-exec-plan fold into trw-prd-ready
        assert len(installed) == len(names) - merged_phase_count

        for name, content in packaged_rendered:
            unsupported = set(_skill_frontmatter_text(content, name)) - _CODEX_SKILL_KEYS
            assert not unsupported, f"{name} (rendered) has unsupported Codex skill keys: {sorted(unsupported)}"
        for path in installed:
            unsupported = set(_skill_frontmatter(path)) - _CODEX_SKILL_KEYS
            assert not unsupported, f"{path} has unsupported Codex skill keys: {sorted(unsupported)}"

    def test_repo_codex_skill_projection_matches_packaged_source(self) -> None:
        """The monorepo `.agents` projection stays byte-identical to the rendered Codex skill.

        Repo-root mirror parity: expected to fail until mirrors are
        regenerated (PRD-CORE-291-FR04 deleted the codex fork this test used
        to compare against; the canonical corpus plus its codex rendering is
        the new comparison basis, but the on-disk ``.agents/skills`` mirror
        itself still reflects the pre-migration bytes).
        """
        from trw_mcp.bootstrap._client_skills import canonical_skills_dir, render_skill_md, skill_names

        repo_root = Path(__file__).resolve().parents[2]
        installed_root = repo_root / ".agents" / "skills"
        if not installed_root.is_dir():
            pytest.skip("monorepo .agents projection not present")

        canonical_root = canonical_skills_dir()
        readiness_phases = {"trw-prd-groom", "trw-prd-review", "trw-exec-plan"}
        for name in skill_names("codex"):
            if name in readiness_phases:
                continue  # merged into trw-prd-ready/{name}-contract.md, not their own dir
            expected = render_skill_md(
                (canonical_root / name / "SKILL.md").read_text(encoding="utf-8"), "codex"
            ).encode("utf-8")
            installed = installed_root / name / "SKILL.md"
            assert installed.is_file(), f"missing Codex projection: {installed}"
            assert installed.read_bytes() == expected, f"Codex projection drift: {installed}"

    def test_codex_skills_preserve_existing_edits_without_force(self, tmp_path: Path) -> None:
        install_codex_skills(tmp_path)
        skill_path = tmp_path / ".agents" / "skills" / "trw-deliver" / "SKILL.md"
        skill_path.write_text("customized skill", encoding="utf-8")

        result = install_codex_skills(tmp_path)

        assert ".agents/skills/trw-deliver/SKILL.md" in result["preserved"]
        assert skill_path.read_text(encoding="utf-8") == "customized skill"

    def test_codex_merge_preserves_user_settings(self) -> None:
        merged = merge_codex_config(
            {
                "model": "gpt-5.4-mini",
                "model_reasoning_effort": "high",
                "sandbox_mode": "read-only",
                "approval_policy": "never",
                "features": {"some_feature": False},
                "mcp_servers": {"custom": {"command": "custom-mcp", "enabled": False}},
            }
        )
        assert merged["model"] == "gpt-5.4-mini"
        assert merged["model_reasoning_effort"] == "high"
        assert merged["sandbox_mode"] == "read-only"
        assert merged["approval_policy"] == "never"
        assert "hooks" not in merged.get("features", {})
        assert "codex_hooks" not in merged["features"]
        assert merged["features"]["some_feature"] is False
        assert "custom" in merged["mcp_servers"]
        assert merged["mcp_servers"]["custom"]["enabled"] is False
        assert merged["mcp_servers"]["trw"]["enabled"] is True
        assert "trw_session_start" in merged["mcp_servers"]["trw"]["enabled_tools"]

    def test_codex_merge_removes_primary_instruction_names_from_fallbacks(self) -> None:
        merged = merge_codex_config(
            {
                "project_doc_fallback_filenames": [
                    "AGENTS.md",
                    "AGENTS.override.md",
                    "CLAUDE.md",
                    "CLAUDE.md",
                ]
            }
        )

        assert merged["project_doc_fallback_filenames"] == ["CLAUDE.md"]

    def test_codex_merge_preserves_explicit_hook_opt_in(self) -> None:
        merged = merge_codex_config({"features": {"codex_hooks": True}})

        assert merged["features"]["hooks"] is True
        assert "codex_hooks" not in merged["features"]

    def test_codex_merge_preserves_current_hook_opt_in(self) -> None:
        merged = merge_codex_config({"features": {"hooks": True}})

        assert merged["features"]["hooks"] is True
        assert "codex_hooks" not in merged["features"]

    def test_codex_config_prefers_project_venv_command(self, tmp_path: Path) -> None:
        project_command = tmp_path / ".venv" / "bin" / "trw-mcp"
        project_command.parent.mkdir(parents=True)
        project_command.write_text("#!/bin/sh\n", encoding="utf-8")

        result = generate_codex_config(tmp_path)

        assert ".codex/config.toml" in result["created"]
        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        assert config["mcp_servers"]["trw"]["command"] == ".venv/bin/trw-mcp"
        # No --debug: log verbosity is protocol, not per-client surface density.
        # See tests/test_bootstrap_debug_flag_parity.py.
        assert config["mcp_servers"]["trw"]["args"] == []
        assert "url" not in config["mcp_servers"]["trw"]

    def test_codex_config_replaces_direct_trw_http_url_with_stdio_entry(self, tmp_path: Path) -> None:
        """A legacy direct-HTTP TRW entry is rewritten to the stdio launcher."""
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            """
[mcp_servers.trw]
url = "http://127.0.0.1:8100/mcp"
enabled = true
""".lstrip(),
            encoding="utf-8",
        )

        result = generate_codex_config(tmp_path)

        assert ".codex/config.toml" in result["updated"]
        config = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
        trw_server = config["mcp_servers"]["trw"]
        assert trw_server["enabled"] is True
        assert "url" not in trw_server
        assert trw_server["command"]
        assert trw_server["args"] == []

    def test_codex_config_smart_merge_existing_file(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            """
model = "gpt-5.4-mini"
project_doc_fallback_filenames = ["README.md"]

[features]
legacy_toggle = false

[mcp_servers.custom]
command = "custom-server"
enabled = false

[mcp_servers.trw]
enabled_tools = ["legacy_helper"]
disabled_tools = ["trw_old_tool"]

[mcp_servers.trw.tools.custom_helper]
enabled = true

[skills]
config = [
  { path = ".agents/skills/trw-deliver/SKILL.md", enabled = true },
]
""".strip()
            + "\n",
            encoding="utf-8",
        )

        result = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in result["updated"]
        config = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
        assert config["model"] == "gpt-5.4-mini"
        assert config["features"]["legacy_toggle"] is False
        assert "hooks" not in config["features"]
        assert "codex_hooks" not in config["features"]
        assert config["mcp_servers"]["custom"]["enabled"] is False
        assert config["mcp_servers"]["trw"]["enabled"] is True
        assert "trw_session_start" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "legacy_helper" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "custom_helper" in config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_old_tool" in config["mcp_servers"]["trw"]["disabled_tools"]
        assert "README.md" in config["project_doc_fallback_filenames"]
        assert ".codex/INSTRUCTIONS.md" not in config["project_doc_fallback_filenames"]
        assert config["model_instructions_file"] == "INSTRUCTIONS.md"
        skill_paths = [entry["path"] for entry in config["skills"]["config"]]
        assert ".agents/skills/trw-deliver" in skill_paths
        assert ".agents/skills/trw-deliver/SKILL.md" not in skill_paths

    def test_codex_config_reinstall_is_idempotent(self, tmp_path: Path) -> None:
        first = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in first["created"]

        second = generate_codex_config(tmp_path)
        assert ".codex/config.toml" in second["updated"]

        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        fallback_files = config.get("project_doc_fallback_filenames", [])
        assert ".codex/INSTRUCTIONS.md" not in fallback_files
        assert config["model_instructions_file"] == "INSTRUCTIONS.md"
        assert "codex_hooks" not in config["features"]

        trw_tools = config["mcp_servers"]["trw"]["enabled_tools"]
        assert "trw_session_start" in trw_tools
        assert trw_tools.count("trw_session_start") == 1

        skill_paths = [entry["path"] for entry in config["skills"]["config"]]
        assert len(skill_paths) == len(set(skill_paths))


class _FakeTool:
    """Minimal stand-in for a FastMCP tool exposing a ``name`` attribute."""

    def __init__(self, name: str) -> None:
        self.name = name


class TestCodexEnabledToolsCompleteness:
    """Bug fix: Codex enabled_tools must reflect the FULL eligible tool set, not
    the live server's per-session masked surface (PRD-CORE-218)."""

    def test_enabled_tools_complete_under_masked_server_surface(self, monkeypatch: object) -> None:
        """If the live server's list_tools is masked down to a tiny subset
        (simulating SurfaceAuthorityMiddleware masking a session to kernel-only),
        the Codex enabled_tools list must still contain the full eligible surface."""
        import trw_mcp.bootstrap._codex as codex
        from trw_mcp.server._surface_manifest_registry import eligible_tool_names

        # Simulate a server whose per-session surface mask removed everything but
        # a tiny subset.
        async def _masked_list_tools() -> list[_FakeTool]:
            return [_FakeTool("trw_session_start"), _FakeTool("trw_learn")]

        from trw_mcp.server._app import mcp

        monkeypatch.setattr(mcp, "list_tools", _masked_list_tools)  # type: ignore[attr-defined]

        names = codex._registered_trw_tool_names()

        # Every tool in the full eligible public surface must be present despite
        # the masked live server.
        full_surface = {n for n in eligible_tool_names() if n.startswith("trw_")}
        missing = full_surface - set(names)
        assert not missing, f"Codex enabled_tools dropped tools under a masked server: {missing}"
        # Sanity: privileged admin tools (e.g. trw_meta_tune_rollback) are included.
        assert "trw_meta_tune_rollback" in names


class TestCodexNoReviewerProfile:
    """PRD-SEC-015-FR10: the reviewer bound is applied at the CALL SITES, never
    as a repo-shipped Codex profile."""

    def test_no_reviewer_profile_table_is_emitted(self, tmp_path: Path) -> None:
        """Codex 0.134.0 REMOVED `[profiles.<name>]` tables — profiles are now
        user-scoped standalone $CODEX_HOME/<name>.config.toml files selected by
        `--profile`, so a repository cannot ship one at all. A generated config
        carrying a profiles table would be rejected by the client it targets."""
        generate_codex_config(tmp_path)
        raw = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
        config = tomllib.loads(raw)

        assert [key for key in config if key.startswith("profiles")] == []
        assert "[profiles." not in raw
        # The interactive allowlist is deliberately UNCHANGED (FR10 non-goal):
        # SurfaceAuthorityMiddleware narrows per session, so Codex mirrors Claude
        # Code's full-but-server-masked surface.
        assert "trw_deliver" in config["mcp_servers"]["trw"]["enabled_tools"]

    def test_the_docstring_records_why_no_reviewer_variant_is_generated(self) -> None:
        """The three reasons must travel with the code, not only with the PRD:
        a TOML comment would be dropped by the bootstrap's merge-and-rewrite, so
        the module docstring is the only durable place for them."""
        import trw_mcp.bootstrap._codex as codex

        doc = codex._registered_trw_tool_names.__doc__ or ""

        assert "0.134" in doc, "the profile-syntax removal must be named"
        assert "reviewer" in doc.lower()
        assert "TOML comments" in doc or "toml comments" in doc.lower()


class TestCodexInitScaffoldContainment:
    """PRD-CORE-262-FR05: a codex-only install scaffolds codex, and only codex.

    This class is an INSERT into a module that pre-existed PRD-CORE-262 (its 21
    other tests are untouched). Helpers live in the
    ``test_init_scaffold_containment`` sibling, which also carries the
    claude-code regression test proving containment removed only foreign
    surfaces.
    """

    def test_codex_init_creates_no_claude_scaffold(self, tmp_path: Path) -> None:
        """FR05/INFRA-192-FR09 attribution test: three properties, one run.

        Measured before the FR05 fix on 2026-09-04: 47 files under ``.claude``, a
        17-line root ``CLAUDE.md``, and a doctor profile row reading
        ``claude-code`` for a project whose ``target_platforms`` said ``codex``.
        Reverting either half of the fix turns this red.

        Updated for PRD-INFRA-192 FR09 (C7): ``.claude/hooks`` is now
        DELIBERATELY kept for an explicit codex-only install -- codex's own
        hook commands run scripts from there (``bootstrap/_codex_hooks.py``),
        so dropping it entirely was itself a bug (a codex project whose
        hooks.json pointed at scripts that were never copied). Only the
        claude-code-EXCLUSIVE surfaces (skills, agents, settings.json,
        .mcp.json) stay absent.
        """
        from tests.test_init_scaffold_containment import (
            claude_scaffold_paths,
            doctor_rows,
            init_single_client_project,
            recorded_target_platforms,
        )

        init_single_client_project(tmp_path, "codex")

        scaffold = claude_scaffold_paths(tmp_path)
        assert scaffold, "codex's shared .claude/hooks surface must still be created"
        assert all(p == ".claude/hooks" or p.startswith(".claude/hooks/") for p in scaffold), (
            f"a codex-only install scaffolded claude-code-exclusive surfaces: {scaffold}"
        )
        assert list((tmp_path / ".claude" / "hooks").glob("*.sh")), "codex's hooks.json needs these scripts on disk"
        assert not (tmp_path / ".claude" / "skills").exists()
        assert not (tmp_path / ".claude" / "agents").exists()
        assert not (tmp_path / ".claude" / "settings.json").exists()
        assert not (tmp_path / ".mcp.json").exists(), "a codex-only install must not create claude-code's .mcp.json"
        assert not (tmp_path / "CLAUDE.md").exists(), "a codex-only install scaffolded a root CLAUDE.md"

        platforms = recorded_target_platforms(tmp_path)
        assert platforms == ["codex"]

        rows = doctor_rows(tmp_path)
        profile_status, profile_message = rows["profile"]
        assert profile_message == f"profile: {platforms[0]}", profile_message
        assert profile_status == "PASS", profile_message
        # The two rows that name a client must agree with each other.
        assert platforms[0] in rows["agent_parity"][1], rows["agent_parity"][1]


class TestCodexToolApprovals:
    """PRD-CORE-277-FR06: the generated config must let a codex member CALL TRW tools.

    `enabled_tools` is the visibility axis. Under `approval_policy = "never"` a
    visible tool is still refused at call time, which left
    `--dangerously-bypass-approvals-and-sandbox` -- which also drops the OS
    sandbox -- as the only working path (sub_v4k1H2_XHdhKc0yQ, 2026-09-16).
    """

    @staticmethod
    def _trw_tools(tmp_path: Path) -> dict[str, dict[str, object]]:
        generate_codex_config(tmp_path)
        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        tools = config["mcp_servers"]["trw"].get("tools", {})
        assert isinstance(tools, dict)
        return tools

    def test_the_ceremony_core_is_approved(self, tmp_path: Path) -> None:
        tools = self._trw_tools(tmp_path)
        for tool_name in ("trw_session_start", "trw_init", "trw_checkpoint", "trw_recall", "trw_send"):
            assert tools.get(tool_name, {}).get("approval_mode") == "approve", tool_name

    def test_the_consequential_tools_stay_gated(self, tmp_path: Path) -> None:
        tools = self._trw_tools(tmp_path)
        for tool_name in ("trw_deliver", "trw_dispatch", "trw_instructions_sync", "trw_claude_md_sync"):
            assert tool_name not in tools, f"{tool_name} was granted an approval mode"

    def test_no_blanket_default_approval(self, tmp_path: Path) -> None:
        generate_codex_config(tmp_path)
        config = tomllib.loads((tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8"))
        # A blanket grant would cover trw_deliver the moment it is registered.
        assert "default_tools_approval_mode" not in config["mcp_servers"]["trw"]

    def test_only_registered_tool_names_are_granted(self, tmp_path: Path) -> None:
        from trw_mcp.server._surface_manifest_registry import eligible_tool_names

        registered = set(eligible_tool_names())
        assert set(self._trw_tools(tmp_path)) <= registered

    def test_a_user_approval_choice_wins(self, tmp_path: Path) -> None:
        # Someone who set "prompt" on a core tool asked to be asked.
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            '[mcp_servers.trw]\ncommand = "trw-mcp"\n\n[mcp_servers.trw.tools.trw_recall]\napproval_mode = "prompt"\n',
            encoding="utf-8",
        )
        tools = self._trw_tools(tmp_path)
        assert tools["trw_recall"]["approval_mode"] == "prompt"
        assert tools["trw_checkpoint"]["approval_mode"] == "approve"

    def test_the_writes_approval_value_survives(self, tmp_path: Path) -> None:
        # codex 0.154 accepts auto|prompt|writes|approve (measured 2026-09-16);
        # "writes" used to be filtered out by the normalizer.
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text(
            '[mcp_servers.trw]\ncommand = "trw-mcp"\n\n[mcp_servers.trw.tools.trw_learn]\napproval_mode = "writes"\n',
            encoding="utf-8",
        )
        assert self._trw_tools(tmp_path)["trw_learn"]["approval_mode"] == "writes"


class TestCodexManagedBlock:
    """PRD-CORE-277-FR07: regeneration rewrites the managed regions and nothing else."""

    @staticmethod
    def _user_region(path: Path) -> str:
        from trw_mcp.bootstrap._codex_toml import split_managed_block

        region = split_managed_block(path.read_text(encoding="utf-8"))
        assert region is not None, "the generated config carries no user-region markers"
        return region

    def test_two_regenerations_leave_the_user_region_byte_identical(self, tmp_path: Path) -> None:
        generate_codex_config(tmp_path)
        path = tmp_path / ".codex" / "config.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "# --- end of your settings ---",
                '# my own note\napproval_policy = "never"\nmodel = "gpt-5.6"\n\n'
                '[my_table]\nkey = "value"\n# --- end of your settings ---',
            ),
            encoding="utf-8",
        )
        generate_codex_config(tmp_path)
        first = path.read_text(encoding="utf-8")
        mine = self._user_region(path)
        generate_codex_config(tmp_path)
        second = path.read_text(encoding="utf-8")

        assert self._user_region(path) == mine, "the user region was rewritten"
        assert first == second, "regeneration is not idempotent"
        parsed = tomllib.loads(second)
        # The killer case: a bare key after a managed table header would bind to
        # that table. It must still be a ROOT key.
        assert parsed["approval_policy"] == "never"
        assert parsed["model"] == "gpt-5.6"
        assert parsed["my_table"] == {"key": "value"}
        assert "# my own note" in second

    def test_a_managed_key_is_restored_after_the_user_overwrites_it(self, tmp_path: Path) -> None:
        generate_codex_config(tmp_path)
        path = tmp_path / ".codex" / "config.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('model_instructions_file = "', 'clobbered = "'))

        generate_codex_config(tmp_path)

        assert "model_instructions_file" in tomllib.loads(path.read_text(encoding="utf-8"))

    def test_a_user_env_table_survives_regeneration(self, tmp_path: Path) -> None:
        """The exact workaround `update-project` used to delete."""
        generate_codex_config(tmp_path)
        path = tmp_path / ".codex" / "config.toml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "# --- end of your settings ---",
                '[mcp_servers.trw.env]\nTRW_COMMS_ENABLED = "true"\n# --- end of your settings ---',
            ),
            encoding="utf-8",
        )

        result = generate_codex_config(tmp_path)
        first = path.read_text(encoding="utf-8")
        generate_codex_config(tmp_path)

        env = tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["trw"]["env"]
        assert env == {"TRW_COMMS_ENABLED": "true"}
        assert path.read_text(encoding="utf-8") == first, "not idempotent once the key moved"
        # The move is disclosed, and as info rather than as an error.
        assert any("moved mcp_servers" in line for line in result.get("info", []))
        assert result["errors"] == []

    def test_the_assess_backend_env_is_forwarded_and_the_users_own_env_vars_are_kept(self, tmp_path: Path) -> None:
        """Codex passes a server only an allow-list of env; TRW_JEV_ENABLED=false must still reach it."""
        merged = merge_codex_config(
            {"mcp_servers": {"trw": {"env_vars": ["MY_TOKEN", "TRW_JEV_ENABLED"]}}}, target_dir=tmp_path
        )

        assert merged["mcp_servers"]["trw"]["env_vars"] == [
            "MY_TOKEN",
            "TRW_JEV_ENABLED",
            "OPENROUTER_API_KEY",
            "TRW_JEV_BASE_URL",
            "TRW_JEV_MODEL",
        ]

        generate_codex_config(tmp_path)
        path = tmp_path / ".codex" / "config.toml"
        first = path.read_text(encoding="utf-8")
        generate_codex_config(tmp_path)
        assert path.read_text(encoding="utf-8") == first, "regeneration is not idempotent"
        assert "TRW_JEV_ENABLED" in tomllib.loads(first)["mcp_servers"]["trw"]["env_vars"]

    def test_every_forwarded_name_is_one_the_assess_backend_reads(self) -> None:
        """``TRW_JEV_ENABLED`` resolution lives in ``_enablement``, everything else in ``_env``."""
        import inspect

        from trw_memory.decisions import _enablement, _env

        from trw_mcp.bootstrap._codex import _TRW_FORWARDED_ENV

        source = inspect.getsource(_env) + inspect.getsource(_enablement)
        assert [name for name in _TRW_FORWARDED_ENV if f'"{name}"' not in source] == []

    def test_a_markerless_config_keeps_the_users_own_keys_out_of_the_managed_block(self, tmp_path: Path) -> None:
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        (codex_dir / "config.toml").write_text('approval_policy = "never"\nmodel = "gpt-5.6"\n', encoding="utf-8")

        generate_codex_config(tmp_path)

        path = codex_dir / "config.toml"
        region = self._user_region(path)
        assert 'approval_policy = "never"' in region
        assert 'model = "gpt-5.6"' in region
        assert tomllib.loads(path.read_text(encoding="utf-8"))["approval_policy"] == "never"

    def test_a_user_region_that_redeclares_a_managed_table_is_absorbed_not_duplicated(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._codex_toml import render_config_text

        merged = merge_codex_config({}, target_dir=tmp_path)
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        text = render_config_text(
            merged,
            user_region="[features]\nhooks = true\n",
            result=result,  # type: ignore[arg-type]
        )

        # Emitting [features] twice would make the file unparseable for codex.
        assert text.count("[features]") == 1
        assert tomllib.loads(text)["mcp_servers"]["trw"]["command"]
        assert any("moved features" in line for line in result.get("info", []))

    def test_a_user_region_that_is_not_valid_toml_is_preserved_not_deleted(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._codex_toml import render_config_text

        merged = merge_codex_config({}, target_dir=tmp_path)
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        text = render_config_text(
            merged,
            user_region="this line is not toml",
            result=result,  # type: ignore[arg-type]
        )

        # Rewriting the file from the parsed config would silently DELETE what
        # the user typed. A file they broke stays theirs, and TRW says so.
        assert "this line is not toml" in text
        assert any("did not read back as valid TOML" in line for line in result.get("info", []))
