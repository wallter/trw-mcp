"""Only a trw-* agent with positive retirement provenance gets deletion advice (feedback #139, review P1).

A user-authored ``trw-custom.md`` is "not shipped by TRW": reported, never advised away. A truly retired agent
(named in TRW's retired list, or recorded as written by TRW) gets an ``rm`` line. A missing bundle reports nothing.
"""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _utils
from trw_mcp.bootstrap._retired_artifacts import retired_artifact_notices, retired_artifact_row
from trw_mcp.bootstrap._utils import _DATA_DIR
from trw_mcp.server._doctor_agent_parity import agent_parity_report

_RETIRED = "trw-tester"  # a real TRW agent that was shipped once and withdrawn
_CUSTOM = "trw-custom"


def _project(tmp_path: Path, *extra: str) -> Path:
    """A claude-code project holding every bundled agent, plus the *extra* agent files."""
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    for source in (_DATA_DIR / "agents").glob("*.md"):
        (agents / source.name).write_bytes(source.read_bytes())
    for name in extra:
        (agents / name).write_text("# mine\n", encoding="utf-8")
    return tmp_path


def test_a_truly_retired_agent_is_listed_with_its_rm_command(tmp_path: Path) -> None:
    root = _project(tmp_path, f"{_RETIRED}.md")

    (notice,) = [n for n in retired_artifact_notices(root) if _RETIRED in n]

    advice = notice.split("remove it manually: ", 1)[1]
    assert shlex.split(advice) == ["rm", str((root / ".claude/agents" / f"{_RETIRED}.md").resolve())]
    assert retired_artifact_row(root)[0] == "WARN"


def test_a_recorded_agent_missing_from_the_bundle_has_provenance(tmp_path: Path) -> None:
    root = _project(tmp_path, "trw-once-shipped.md")
    from trw_mcp.bootstrap import _version_manifest

    (root / ".trw").mkdir()
    (root / ".trw" / _version_manifest._MANIFEST_FILE).write_text(
        "version: 2\ncontent_hashes:\n  trw-once-shipped.md: abc\n", encoding="utf-8"
    )

    assert [n for n in retired_artifact_notices(root) if "trw-once-shipped" in n]


def test_a_user_authored_trw_agent_gets_no_deletion_advice(tmp_path: Path) -> None:
    root = _project(tmp_path, f"{_CUSTOM}.md")

    assert not [n for n in retired_artifact_notices(root) if _CUSTOM in n]
    assert _CUSTOM not in retired_artifact_row(root)[1]


@pytest.mark.parametrize("name", ["my-own-agent.md", "trw-notes.txt"])
def test_a_user_agent_or_a_non_agent_file_is_not_flagged(tmp_path: Path, name: str) -> None:
    assert not [n for n in retired_artifact_notices(_project(tmp_path, name)) if name in n]


def test_an_unavailable_bundle_makes_no_installed_agent_look_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    bundle = tmp_path / "bundle-without-agents"
    (bundle / "skills").mkdir(parents=True)
    monkeypatch.setattr(_utils, "_DATA_DIR", bundle)

    assert not [n for n in retired_artifact_notices(root) if ".claude/agents" in n]


def test_a_project_holding_only_bundled_agents_has_no_notice(tmp_path: Path) -> None:
    assert not [n for n in retired_artifact_notices(_project(tmp_path)) if ".claude/agents" in n]


def test_agent_parity_calls_an_unknown_trw_agent_not_shipped_with_no_rm(tmp_path: Path) -> None:
    root = _project(tmp_path, f"{_CUSTOM}.md", "my-own-agent.md")

    status, message, rows = agent_parity_report(root)

    assert status == "WARN"
    assert f"{_CUSTOM}.md is not shipped by TRW" in message
    assert "rm " not in message and "my-own-agent" not in message
    assert any(_CUSTOM in str(row.get("unexpected", "")) for row in rows)


def test_agent_parity_points_a_retired_agent_at_the_retired_artifacts_check(tmp_path: Path) -> None:
    status, message, _rows = agent_parity_report(_project(tmp_path, f"{_RETIRED}.md"))
    assert status == "WARN"
    assert f"{_RETIRED}.md is retired" in message


def test_agent_parity_passes_when_nothing_is_unexpected(tmp_path: Path) -> None:
    assert agent_parity_report(_project(tmp_path))[0] == "PASS"


# ── Round 3 ──────────────────────────────────────────────────────────────

_REVIEWERS = (
    "reviewer-correctness",
    "reviewer-integration",
    "reviewer-performance",
    "reviewer-security",
    "reviewer-spec-compliance",
    "reviewer-style",
    "reviewer-test-quality",
)


def test_an_active_channel_owned_agent_is_never_called_retired(tmp_path: Path) -> None:
    """Review P1: trw-distill-explorer.md comes from an active channel, not data/agents, though the manifest records it."""
    from trw_mcp.bootstrap import _version_manifest

    root = _project(tmp_path, "trw-distill-explorer.md")
    (root / ".trw").mkdir()
    (root / ".trw" / _version_manifest._MANIFEST_FILE).write_text(
        "version: 2\ncontent_hashes:\n  trw-distill-explorer.md: abc\n", encoding="utf-8"
    )

    assert not [n for n in retired_artifact_notices(root) if "trw-distill-explorer" in n]
    status, message, _rows = agent_parity_report(root)
    assert "trw-distill-explorer" not in message
    assert status == "PASS"


@pytest.mark.parametrize("stem", _REVIEWERS)
def test_a_withdrawn_reviewer_agent_is_detected_without_the_trw_prefix(tmp_path: Path, stem: str) -> None:
    root = _project(tmp_path, f"{stem}.md")

    (notice,) = [n for n in retired_artifact_notices(root) if stem in n]

    assert shlex.split(notice.split("remove it manually: ", 1)[1])[0] == "rm"


def test_a_user_reviewer_agent_with_another_name_is_not_flagged(tmp_path: Path) -> None:
    assert not [n for n in retired_artifact_notices(_project(tmp_path, "reviewer-mine.md")) if "reviewer-mine" in n]


def test_a_hostile_unexpected_agent_name_cannot_drive_the_terminal_through_parity(tmp_path: Path) -> None:
    hostile = "trw-custom\x1b[2J\n[PASS] forged.md"
    root = _project(tmp_path)
    (root / ".claude" / "agents" / hostile).write_text("# mine\n", encoding="utf-8")

    _status, message, _rows = agent_parity_report(root)

    assert "\x1b" not in message
    assert not any(line.startswith("[PASS]") for line in message.splitlines())
    assert "trw-custom\\x1b[2J\\n[PASS] forged.md" in message
