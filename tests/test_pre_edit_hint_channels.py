"""PRD-CORE-336-FR04 wiring: the Codex hook registration, the instruction fallback, the doctor row.

The matrix itself and the per-client hook output contract live in
``test_before_edit_hint_tool.py`` (the FR04 evidence artifact).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from trw_mcp.bootstrap._codex_distill_channels import install_codex_distill_channels
from trw_mcp.models.config import resolve_client_profile
from trw_mcp.models.config._pre_edit_channels import PRE_EDIT_HINT_INSTRUCTION, fallback_clients
from trw_mcp.models.config._profiles import builtin_client_ids

pytestmark = pytest.mark.usefixtures("stub_cli_version_probes")

_HINT_SCRIPT = ".claude/hooks/pre-tool-distill-hint.sh"


def _project(tmp_path: Path, *, enabled: bool) -> Path:
    (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "config.yaml").write_text(f"cc03_hook_enabled: {str(enabled).lower()}\n", encoding="utf-8")
    return tmp_path


def _pre_tool_groups(project: Path) -> list[dict[str, object]]:
    data = json.loads((project / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    return list(data.get("hooks", {}).get("PreToolUse", []))


def _hint_groups(project: Path) -> list[dict[str, object]]:
    return [g for g in _pre_tool_groups(project) if _HINT_SCRIPT in json.dumps(g)]


class TestCodexRegistration:
    def test_enabled_ships_the_script_and_registers_it_for_apply_patch(self, tmp_path: Path) -> None:
        project = _project(tmp_path, enabled=True)

        result = install_codex_distill_channels(project)

        assert (project / _HINT_SCRIPT).is_file()
        assert (project / ".claude" / "hooks" / "lib-distill-hint.sh").is_file()
        (group,) = _hint_groups(project)
        assert group["matcher"] == "apply_patch"
        (hook,) = group["hooks"]  # type: ignore[misc]
        assert hook["command"] == (  # type: ignore[index]
            f'TRW_HOOK_CLIENT=codex /bin/sh "$(git rev-parse --show-toplevel)/{_HINT_SCRIPT}"'
        )
        assert hook["timeout"] == 3  # type: ignore[index]
        assert str(group["description"]).startswith("TRW managed:"), "uninstall strips groups by this tag"
        assert result["errors"] == []

    def test_disabled_registers_nothing_and_ships_nothing(self, tmp_path: Path) -> None:
        project = _project(tmp_path, enabled=False)

        install_codex_distill_channels(project)

        assert not (project / _HINT_SCRIPT).exists()
        assert _hint_groups(project) == []

    def test_turning_it_off_withdraws_the_registration_and_keeps_user_groups(self, tmp_path: Path) -> None:
        project = _project(tmp_path, enabled=True)
        install_codex_distill_channels(project)
        hooks_path = project / ".codex" / "hooks.json"
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        user_group = {"matcher": "Bash", "hooks": [{"type": "command", "command": "./mine.sh"}]}
        data["hooks"]["PreToolUse"].insert(0, user_group)
        hooks_path.write_text(json.dumps(data), encoding="utf-8")

        _project(project, enabled=False)
        install_codex_distill_channels(project)

        assert _hint_groups(project) == []
        assert _pre_tool_groups(project) == [user_group]
        assert not (project / _HINT_SCRIPT).exists()

    def test_rerun_is_idempotent(self, tmp_path: Path) -> None:
        project = _project(tmp_path, enabled=True)
        install_codex_distill_channels(project)
        before = (project / ".codex" / "hooks.json").read_text(encoding="utf-8")

        second = install_codex_distill_channels(project)

        assert (project / ".codex" / "hooks.json").read_text(encoding="utf-8") == before
        assert len(_hint_groups(project)) == 1
        assert ".codex/hooks.json" not in second["updated"]

    def test_unreadable_hooks_json_is_left_untouched(self, tmp_path: Path) -> None:
        project = _project(tmp_path, enabled=True)
        hooks_path = project / ".codex" / "hooks.json"
        hooks_path.parent.mkdir(parents=True)
        hooks_path.write_bytes(b"\xff\xfe not json")
        from trw_mcp.bootstrap._codex_distill_channels import set_pre_edit_hint_registration

        assert set_pre_edit_hint_registration(project, present=True) is False
        assert hooks_path.read_bytes() == b"\xff\xfe not json"


def _carriers() -> dict[str, Callable[[], str]]:
    """The instruction carrier each fallback client reads, rendered by its production renderer."""
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section
    from trw_mcp.state.claude_md.renderers._review_and_opencode import render_antigravity_instructions
    from trw_mcp.state.claude_md.sections._behavioral_protocol import render_minimal_protocol
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_opencode_instructions

    def agents(client_id: str) -> Callable[[], str]:
        return lambda: render_agents_trw_section(client_profile=resolve_client_profile(client_id))

    return {
        "cursor-ide": agents("cursor-ide"),  # .cursor/rules/trw-ceremony.mdc
        "cursor-cli": render_minimal_protocol,  # light-mode AGENTS.md
        "copilot": agents("copilot"),  # .github/instructions/trw-ceremony.instructions.md
        "opencode": render_opencode_instructions,  # .opencode/INSTRUCTIONS.md
        "antigravity-cli": render_antigravity_instructions,  # ANTIGRAVITY.md
        "grok": agents("grok"),  # AGENTS.md
    }


def test_every_fallback_client_has_a_checked_carrier() -> None:
    assert set(fallback_clients(builtin_client_ids())) <= set(_carriers())


@pytest.mark.parametrize("client_id", sorted(_carriers()))
def test_fallback_carrier_tells_the_agent_to_call_the_hint(client_id: str) -> None:
    assert PRE_EDIT_HINT_INSTRUCTION in _carriers()[client_id]()


def test_the_instruction_names_the_real_tool_call() -> None:
    assert 'trw_code(mode="hint"' in PRE_EDIT_HINT_INSTRUCTION


class TestDoctorRow:
    @staticmethod
    def _select(project: Path, clients: list[str]) -> Path:
        """Select *clients* AND actually install the CC-03 hook for the wired ones.

        A real project always runs ``init-project``/``update-project`` before
        ``doctor``, so the ``hook_channel`` row's PASS/WARN/FAIL is measured
        against a real registration on disk, never against the flag alone
        (release-window fix, 2026-09-27 -- that gap is exactly what let a
        dropped ``.claude/settings.json`` registration read back as PASS).
        """
        (project / ".trw").mkdir(parents=True, exist_ok=True)
        (project / ".trw" / "config.yaml").write_text(
            f"target_platforms: {json.dumps(clients)}\ncc03_hook_enabled: true\n", encoding="utf-8"
        )
        if "claude-code" in clients:
            from trw_mcp.bootstrap._claude_code_distill_channels import install_claude_code_distill_channels

            claude_dir = project / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
            settings = claude_dir / "settings.json"
            if not settings.is_file():
                settings.write_text("{}\n", encoding="utf-8")
            install_claude_code_distill_channels(project)
        if "codex" in clients:
            install_codex_distill_channels(project)
        return project

    def test_disabled_hook_is_not_reported_as_delivered(self, tmp_path: Path) -> None:
        """cc03_hook_enabled off: nothing ships or registers, so wired clients are on the instruction fallback too."""
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        project = self._select(tmp_path, ["claude-code", "codex"])
        config = project / ".trw" / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace("enabled: true", "enabled: false"), encoding="utf-8"
        )

        status, message = hook_channel_row(project)

        assert status == "WARN"
        assert "delivered" not in message
        assert "cc03_hook_enabled" in message and "claude-code" in message and "codex" in message

    def test_only_wired_clients_pass(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        status, message = hook_channel_row(self._select(tmp_path, ["claude-code", "codex"]))

        assert status == "PASS"
        assert "additionalContext" in message

    def test_flag_on_but_registration_missing_fails_not_passes(self, tmp_path: Path) -> None:
        """Release-window repro: cc03_hook_enabled true, but the PreToolUse entry
        was dropped from .claude/settings.json (exactly what an uncommitted
        settings.json's ``preserve_uncommitted_changes`` used to do). Doctor must
        say FAIL, never PASS -- the false PASS this fix removes."""
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        project = self._select(tmp_path, ["claude-code"])
        settings = project / ".claude" / "settings.json"
        data = json.loads(settings.read_text(encoding="utf-8"))
        data.get("hooks", {}).pop("PreToolUse", None)
        settings.write_text(json.dumps(data), encoding="utf-8")

        status, message = hook_channel_row(project)

        assert status == "FAIL"
        assert "not wired" in message
        assert "update-project" in message and "cc03_hook_enabled" in message

    def test_flag_on_but_hook_script_not_executable_fails(self, tmp_path: Path) -> None:
        """The registration alone proves nothing if the script it names cannot run."""
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        project = self._select(tmp_path, ["claude-code"])
        script = project / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
        script.chmod(0o644)

        status, message = hook_channel_row(project)

        assert status == "FAIL"
        assert "not executable" in message or "not wired" in message

    @pytest.mark.parametrize("fallback", ["cursor-ide", "copilot", "grok"])
    def test_a_fallback_client_is_named(self, tmp_path: Path, fallback: str) -> None:
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        status, message = hook_channel_row(self._select(tmp_path, ["claude-code", fallback]))

        assert status == "WARN"
        assert fallback in message and 'trw_code(mode="hint")' in message
        assert "claude-code" not in message

    def test_a_client_without_a_decision_is_named(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        status, message = hook_channel_row(self._select(tmp_path, ["claude-code", "future-client"]))

        assert status == "WARN"
        assert "future-client" in message

    def test_unreadable_selection_is_not_measured(self, tmp_path: Path) -> None:
        from trw_mcp.server._doctor_hook_channel import hook_channel_row

        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("target_platforms: [unterminated\n", encoding="utf-8")

        status, message = hook_channel_row(tmp_path)

        assert status == "WARN"
        assert "not measured" in message

    def test_the_row_runs_in_doctor(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.server._subcommands_doctor import _doctor_core

        self._select(tmp_path, ["copilot"])

        rows = {row.name: row for row in _doctor_core(tmp_path, TRWConfig())}

        assert rows["hook_channel"].status == "WARN"
        assert "copilot" in rows["hook_channel"].message


class TestDistillDiscoverabilityLine:
    """2026-09-27 audit (touchpoint #4): `render_pre_edit_hint_instruction` appends a
    `trw-distill query|rca` line ONLY when the proprietary package is installed.
    """

    def test_appends_the_distill_line_when_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.models.config import _pre_edit_channels as mod

        monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)

        rendered = mod.render_pre_edit_hint_instruction()

        assert PRE_EDIT_HINT_INSTRUCTION in rendered
        assert "trw-distill query" in rendered
        assert "trw-distill rca" in rendered

    def test_omits_the_distill_line_when_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.models.config import _pre_edit_channels as mod

        monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: False)

        rendered = mod.render_pre_edit_hint_instruction()

        assert rendered == PRE_EDIT_HINT_INSTRUCTION
        assert "trw-distill" not in rendered
