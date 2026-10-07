"""update-project's report is rendered from one record per path (feedback #158, #142, #138)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._template_updater import _merge_settings_json
from trw_mcp.bootstrap._version_migration_clients import client_skill_lists
from trw_mcp.server._update_report import attention_count, report_kept, report_removed


def _result(**overrides: list[str]) -> dict[str, list[str]]:
    base: dict[str, list[str]] = {"updated": [], "created": [], "preserved": [], "errors": [], "warnings": []}
    base.update(overrides)
    return base


def _out(result: dict[str, list[str]], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> str:
    report_removed(result, detailed=False, quiet=False)
    report_kept(result, tmp_path, detailed=False, quiet=False)
    return capsys.readouterr().out


# ── #138: AGENTS.md block, client-curation reason ─────────────────────────


@pytest.mark.parametrize(("key", "verb"), [("updated", "refreshed"), ("created", "created")])
def test_an_agents_md_write_gets_a_per_path_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], key: str, verb: str
) -> None:
    out = _out(_result(**{key: ["AGENTS.md"]}), tmp_path, capsys)
    assert f"AGENTS.md: {verb} the TRW block" in out


def test_no_agents_md_line_when_it_was_not_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "AGENTS.md:" not in _out(_result(updated=[".trw/config.yaml"]), tmp_path, capsys)


def test_a_skill_curated_out_of_a_client_is_removed_with_that_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.bootstrap._utils import _DATA_DIR

    bundled = {p.name for p in (_DATA_DIR / "skills").iterdir() if p.is_dir()}
    pair = next(
        (
            (root, name)
            for root, listed in client_skill_lists().items()
            if listed is not None
            for name in sorted(bundled - listed)
        ),
        None,
    )
    if pair is None:
        pytest.skip("every bundled skill reaches every client")
    path = f"{pair[0]}/{pair[1]}/SKILL.md"

    out = _out(_result(retired=[path]), tmp_path, capsys)

    assert f"Removed retired TRW file: {path} (TRW no longer ships this skill to this client)" in out


def test_a_wholly_retired_skill_has_no_curation_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _out(_result(retired=[".agents/skills/trw-not-a-skill/SKILL.md"]), tmp_path, capsys)
    assert "Removed retired TRW file: .agents/skills/trw-not-a-skill/SKILL.md\n" in out


# ── #158: structured facts replace prose matching ────────────────────────


def test_a_git_recovered_removal_is_not_also_a_removed_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = ".claude/hooks/lib-x.sh"
    result = _result(retired=[path], retired_described=[path], warnings=[f"{path}: removed; committed in git"])
    assert "Removed retired TRW file" not in _out(result, tmp_path, capsys)


def test_a_warning_that_merely_starts_with_the_path_does_not_hide_a_removal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = ".claude/hooks/lib-x.sh"
    result = _result(retired=[path], warnings=[f"{path}: some unrelated note"])
    assert f"Removed retired TRW file: {path}" in _out(result, tmp_path, capsys)


# ── #142: one summary, from the same records ──────────────────────────────


def test_attention_counts_each_kept_or_left_in_place_path_once(tmp_path: Path) -> None:
    result = _result(
        preserved=[
            "a.sh (not_installer_owned)",
            ".claude/agent-memory/trw-x (not_installer_owned)",
            "b.sh (uncommitted_changes)",
        ],
        retired_present=[".claude/agent-memory/trw-x"],
        retired=[".claude/old.md"],
    )
    assert attention_count(result, tmp_path) == 3  # a.sh, b.sh, and the retired-present dir once


def test_nothing_needing_attention_counts_zero(tmp_path: Path) -> None:
    assert attention_count(_result(retired=[".claude/old.md"], updated=["x"]), tmp_path) == 0


# ── #158: the settings.json hook-timeout rewrite is reported ──────────────


def test_a_rewritten_legacy_hook_timeout_is_reported(tmp_path: Path) -> None:
    command = "sh .claude/hooks/x.sh"
    src = tmp_path / "bundled.json"
    src.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 20}]}]}})
    )
    dest = tmp_path / ".claude" / "settings.json"
    dest.parent.mkdir()
    dest.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 10000}]}]}})
    )
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

    _merge_settings_json(src, dest, result)

    assert json.loads(dest.read_text())["hooks"]["Stop"][0]["hooks"][0]["timeout"] == 20
    assert any("hook timeout" in n and "set 1 legacy" in n for n in result.get("notes", []))


def test_a_user_raised_timeout_is_not_reported(tmp_path: Path) -> None:
    command = "sh .claude/hooks/x.sh"
    src = tmp_path / "bundled.json"
    src.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 20}]}]}})
    )
    dest = tmp_path / ".claude" / "settings.json"
    dest.parent.mkdir()
    dest.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 45}]}]}})
    )
    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

    _merge_settings_json(src, dest, result)

    assert not [n for n in result.get("notes", []) if "hook timeout" in n]
