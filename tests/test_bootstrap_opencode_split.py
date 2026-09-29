"""Split bootstrap OpenCode tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._opencode import (
    generate_agents_md,
    generate_opencode_config,
    install_opencode_commands,
    install_opencode_skills,
    load_opencode_skill_inventory,
    merge_opencode_json,
)
from trw_mcp.state.claude_md._instructions_link import (
    INSTRUCTIONS_RELPATH,
    LINK_BODY,
    render_instructions_body,
    render_instructions_file,
)


def _expected_instructions(project_root: Path) -> str:
    """The one file every writer produces: the generated header plus the shared body."""
    return render_instructions_file(render_instructions_body(project_root))


pytestmark = pytest.mark.usefixtures("no_memory_daemon")


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
        # No --debug: log verbosity is protocol, not per-client surface density.
        # See tests/test_bootstrap_debug_flag_parity.py.
        assert config["mcp"]["trw"]["command"] == ["trw-mcp"]
        assert "args" not in config["mcp"]["trw"]

    def test_fr11_agents_md_created(self, tmp_path: Path) -> None:
        result = generate_agents_md(tmp_path)
        assert "AGENTS.md" in result["created"]
        content = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content
        # PRD-CORE-341: AGENTS.md holds the link; the section lives in the instructions file.
        assert LINK_BODY in content
        assert (tmp_path / INSTRUCTIONS_RELPATH).read_text() == _expected_instructions(tmp_path)

    def test_fr11_agents_md_same_markers(self, tmp_path: Path) -> None:
        generate_agents_md(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text()
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content

    def test_fr11_agents_md_updates_existing(self, tmp_path: Path) -> None:
        # Write initial
        generate_agents_md(tmp_path)
        # Update
        generate_agents_md(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text()
        instructions = (tmp_path / INSTRUCTIONS_RELPATH).read_text()
        assert instructions == _expected_instructions(tmp_path)  # a rewrite leaves the same file
        assert LINK_BODY in content
        assert content.count("<!-- trw:start -->") == 1

    def test_fr11_agents_md_preserves_user_content(self, tmp_path: Path) -> None:
        # Write file with user content + markers
        (tmp_path / "AGENTS.md").write_text(
            "# My Project\n\nUser content here\n\n"
            "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
            "<!-- trw:start -->\nOld TRW\n<!-- trw:end -->\n\n"
            "More user content\n"
        )
        generate_agents_md(tmp_path)
        content = (tmp_path / "AGENTS.md").read_text()
        assert "User content here" in content
        assert "More user content" in content
        assert LINK_BODY in content
        assert "Old TRW" not in content
        assert (tmp_path / INSTRUCTIONS_RELPATH).read_text() == _expected_instructions(tmp_path)

    def test_opencode_commands_installed(self, tmp_path: Path) -> None:
        result = install_opencode_commands(tmp_path)
        assert ".opencode/commands/trw-deliver.md" in result["created"]
        assert (tmp_path / ".opencode" / "commands" / "trw-prd-ready.md").exists()
        assert (tmp_path / ".opencode" / "commands" / "trw-reflect.md").exists()

    def test_opencode_agents_installed(self, tmp_path: Path) -> None:
        """PRD-CORE-252-FR04: the source is the shared bundle, not a stub directory.

        ``install_opencode_agents`` and ``data/opencode/agents`` are retired;
        the same two properties this always asserted — subagent mode and a
        deny-write permission map for a read-only specialist — now hold for
        every bundled agent rather than for three hand-written ones.
        """
        from trw_mcp.bootstrap._init_project_skills import _install_agents

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
        _install_agents(tmp_path, force=False, result=result, clients=["opencode"])

        assert str(tmp_path / ".opencode" / "agents" / "trw-researcher.md") in result["created"]
        content = (tmp_path / ".opencode" / "agents" / "trw-reviewer.md").read_text(encoding="utf-8")
        assert "mode: subagent" in content
        # The deny is now expressed in opencode v2's documented ORDERED ARRAY of
        # {action, resource, effect} rules. The old `write: deny` assertion
        # matched TRW's retired keyed-map form, whose action vocabulary
        # (`bash`/`write`) that harness does not define -- see
        # tests/test_agent_materialization_per_client.py::
        # test_opencode_permissions_are_an_ordered_array_of_rules.
        assert "- action: edit" in content
        assert "effect: deny" in content

    def test_opencode_skills_inventory_curated(self) -> None:
        inventory = load_opencode_skill_inventory()
        assert inventory["trw-deliver"]["disposition"] == "portable"
        # Retired 2026-09-12: the agent-team planning command surface was removed,
        # so no shipped entry carries the "exclude" disposition any more. The
        # inventory is still a CURATED subset rather than a mirror of the bundle --
        # `trw-audit` is bundled but deliberately absent here -- and the exclude
        # vocabulary itself is kept as a live control, proved by
        # test_exclude_disposition_is_still_honored below.
        assert "trw-sprint-team" not in inventory
        assert "trw-audit" not in inventory
        assert inventory and all(cfg["disposition"] in {"portable", "exclude"} for cfg in inventory.values())

    def test_exclude_disposition_is_still_honored(self, tmp_path: Path) -> None:
        """The `exclude` curation control outlives the last shipped entry using it.

        `trw-sprint-team` was the only bundled skill marked `exclude`, and it was
        retired with the agent-team planning command surface on 2026-09-12. The
        branch that honors the disposition is a curation control, not dead code:
        deleting it because its current population is empty would silently turn a
        future `exclude` entry into an install. This exercises it through the
        `data_dir` seam the installer already exposes.

        Canonical skills now live at ``data_dir.parent / "skills"`` (the one
        shared corpus, PRD-CORE-291-FR04), not under ``data_dir`` itself --
        opencode dropped its own fork -- so the fixture's fake tree mirrors
        that: ``fake_parent/skills/<name>`` for the canonical bodies and
        ``fake_parent/opencode/skills_inventory.yaml`` for the inventory.
        """
        fake_parent = tmp_path / "fake-data"
        data_dir = fake_parent / "opencode"
        (fake_parent / "skills" / "keep-me").mkdir(parents=True)
        (fake_parent / "skills" / "keep-me" / "SKILL.md").write_text("kept", encoding="utf-8")
        (fake_parent / "skills" / "drop-me").mkdir(parents=True)
        (fake_parent / "skills" / "drop-me" / "SKILL.md").write_text("dropped", encoding="utf-8")
        data_dir.mkdir(parents=True)
        (data_dir / "skills_inventory.yaml").write_text(
            "version: 1\nskills:\n  keep-me:\n    disposition: portable\n  drop-me:\n    disposition: exclude\n",
            encoding="utf-8",
        )

        target = tmp_path / "project"
        target.mkdir()
        result = install_opencode_skills(target, data_dir=data_dir)

        assert not result["errors"]
        assert (target / ".opencode" / "skills" / "keep-me" / "SKILL.md").is_file()
        assert not (target / ".opencode" / "skills" / "drop-me").exists()

    def test_opencode_skills_installed_curated_subset(self, tmp_path: Path) -> None:
        result = install_opencode_skills(tmp_path)
        assert ".opencode/skills/trw-deliver/SKILL.md" in result["created"]
        assert (tmp_path / ".opencode" / "skills" / "trw-prd-ready" / "SKILL.md").exists()
        assert not (tmp_path / ".opencode" / "skills" / "trw-sprint-team").exists()
        assert not (tmp_path / ".opencode" / "skills" / "trw-audit").exists()
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
            "command": ["trw-mcp"],
        }
        result = merge_opencode_json(existing, trw)
        assert result["mcp"]["trw"]["command"] == ["trw-mcp"]

    def test_fr16_fresh_install_full_template(self, tmp_path: Path) -> None:
        result = generate_opencode_config(tmp_path)
        config = json.loads((tmp_path / "opencode.json").read_text())
        assert "permission" in config
        assert "mcp" in config
        assert "trw" in config["mcp"]

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


def test_ready_installs_resolvable_shared_contracts(tmp_path: Path) -> None:
    """ND3: installed adapter reaches exact shared owners, not monorepo paths."""
    data = Path(__file__).resolve().parents[1] / "src/trw_mcp/data"
    result = install_opencode_skills(tmp_path)
    assert not result["errors"]
    install_opencode_commands(tmp_path)
    ready = tmp_path / ".opencode/skills/trw-prd-ready"
    adapter = (ready / "SKILL.md").read_text()
    command = (tmp_path / ".opencode/commands/trw-prd-ready.md").read_text()
    assert ".opencode/skills/trw-prd-ready/SKILL.md" in command
    assert "original `$ARGUMENTS`" in command
    # trw-prd-ready is not its own contract: a self-copy beside SKILL.md duplicated
    # ~16 KB of prompt surface and nothing referenced it.
    assert not (ready / "trw-prd-ready-contract.md").exists()
    for phase in ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"):
        name = f"{phase}-contract.md"
        # CANONICAL-SKILL CONTENT GAP CLOSED (PRD-CORE-291-FR04) for the three
        # DELEGATED phases: the canonical body (src/trw_mcp/data/skills/
        # trw-prd-ready/SKILL.md) now names each sibling contract by its
        # installed filename as one of three equally valid resolution paths
        # ("the packaged internal `trw-prd-groom` contract (the `trw-prd-groom`
        # skill, or `trw-prd-groom-contract.md` beside this skill) (inline if
        # unavailable)"). An opencode agent reading the installed SKILL.md now
        # has a textual pointer to the sibling files install_opencode_skills
        # materializes beside it.
        assert name in adapter
        assert (ready / name).read_bytes() == (data / "skills" / phase / "SKILL.md").read_bytes()
    for filename in ("SKILL.md", "trw-prd-groom-contract.md", "trw-exec-plan-contract.md"):
        installed = (ready / filename).read_text()
        assert "up to two NEEDS WORK repair cycles" in installed
        assert "within the original user scope" in installed
        assert "fresh author-independent review" in installed
    # CANONICAL-SKILL CONTENT GAP (PRD-CORE-291-FR04), same root cause as
    # above: the deleted opencode fork stated "If a required contract is
    # missing, stop and report the missing installed path;" explicitly. The
    # canonical body covers the concept more loosely ("inline if
    # unavailable", "If it remains unavailable, stop at the current gate")
    # but never this literal sentence. Not fixed here (out of scope).
    assert "If a required contract is missing, stop" not in adapter, (
        "canonical trw-prd-ready/SKILL.md unexpectedly restates the opencode fork's literal missing-contract "
        "sentence -- if this now passes, tighten this assertion back to the positive form"
    )
    assert "trw_prd_create()" not in adapter
    assert "EXECUTION-PLAN-{PRD-ID}.md" not in command


def test_ready_contract_install_preserves_user_edits(tmp_path: Path) -> None:
    install_opencode_skills(tmp_path)
    path = tmp_path / ".opencode/skills/trw-prd-ready/trw-exec-plan-contract.md"
    assert path.is_file()
    path.write_text("Operator-owned local contract\n")
    result = install_opencode_skills(tmp_path)
    assert path.read_text() == "Operator-owned local contract\n"
    assert str(path.relative_to(tmp_path)) in result["preserved"]


def test_retired_opencode_contract_survives_update_and_doctor_reports_it(tmp_path: Path) -> None:
    """PRD-INFRA-200 FR05 (redesigned per lead direction after 3 fix-delta rounds

    surfaced deletion-safety races): update-project NEVER deletes the retired
    OpenCode ``trw-prd-ready-contract.md`` self-copy (0f5fba2cb stopped writing
    it). It survives every update untouched; update-project prints one-line
    notice naming the file and its manual removal command, and doctor reports
    the same thing as a WARN row. No unlink, no allowlist, no symlink logic.
    """
    from trw_mcp.bootstrap import init_project, update_project
    from trw_mcp.bootstrap._retired_artifacts import RETIRED_OPENCODE_CONTRACT
    from trw_mcp.server._doctor_retired_artifacts import retired_artifact_row

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    assert not init_project(repo, ide="claude-code")["errors"]
    assert not update_project(repo)["errors"]

    # Absent case: no notice, doctor PASS.
    status, message = retired_artifact_row(repo)
    assert status == "PASS"
    assert RETIRED_OPENCODE_CONTRACT not in message

    # Plant the legacy artifact an older TRW version wrote.
    retired_path = repo / RETIRED_OPENCODE_CONTRACT
    retired_path.parent.mkdir(parents=True, exist_ok=True)
    retired_path.write_text("legacy self-copy of trw-prd-ready/SKILL.md\n", encoding="utf-8")

    result = update_project(repo)

    assert retired_path.is_file(), "update-project must never delete the retired artifact -- report only"
    notices = [w for w in result["warnings"] if RETIRED_OPENCODE_CONTRACT in w]
    assert len(notices) == 1, f"expected exactly one notice, got {result['warnings']}"
    assert "rm " in notices[0], "the notice must name the manual removal command"
    # codex sol fix-delta round 1: a bare relative path in the removal command is
    # ambiguous outside the inspected project (`doctor /projects/B` run with cwd
    # in project A would delete A's same-named file if followed literally) --
    # the command must be rooted at THIS target_dir, not left to the operator's cwd.
    assert str(retired_path) in notices[0], "the removal command must use an absolute, target_dir-rooted path"

    status, message = retired_artifact_row(repo)
    assert status == "WARN"
    assert RETIRED_OPENCODE_CONTRACT in message
    assert "rm " in message
    assert str(retired_path) in message, "the doctor row's removal command must use an absolute, target_dir-rooted path"


def test_retired_artifact_removal_command_is_shell_quoted(tmp_path: Path) -> None:
    """PRD-INFRA-200 FR05, codex sol fix-delta round 2 on lane-infra-200-b: a

    project path containing a single quote must never let the printed
    ``rm`` command break out of its quoting -- a bare ``f"rm '{path}'"`` lets
    ``/tmp/project' ; printf INJECTED ; #`` tokenize as two separate shell
    commands if the operator follows the advice literally.
    """
    import shlex

    from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices

    repo = tmp_path / "it's-a-project"
    retired_path = repo / ".opencode" / "skills" / "trw-prd-ready" / "trw-prd-ready-contract.md"
    retired_path.parent.mkdir(parents=True)
    retired_path.write_text("legacy self-copy\n", encoding="utf-8")

    (notice,) = retired_artifact_notices(repo)
    command = notice.split("remove it manually: ", 1)[1]
    tokens = shlex.split(command)
    assert tokens == ["rm", str(retired_path.resolve())], (
        f"the removal command must shell-quote the path as a single operand, got: {tokens}"
    )
