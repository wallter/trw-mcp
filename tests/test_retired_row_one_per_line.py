"""The doctor's retired-artifacts row names one artifact per line (feedback #141, sub_0IEkWHUJOsyTG9mE)."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.bootstrap._retired_artifacts import retired_artifact_row


def _plant(tmp_path: Path, *names: str) -> None:
    for name in names:
        (tmp_path / ".claude" / "agent-memory" / name).mkdir(parents=True)
        (tmp_path / ".claude" / "agent-memory" / name / "note.md").write_text("n\n", encoding="utf-8")


def test_each_retired_artifact_has_its_own_line(tmp_path: Path) -> None:
    _plant(tmp_path, "trw-implementer", "trw-reviewer", "trw-lead")

    status, message = retired_artifact_row(tmp_path)

    lines = message.splitlines()
    assert status == "WARN"
    assert len(lines) == 3
    for stem in ("trw-implementer", "trw-reviewer", "trw-lead"):
        assert sum(stem in line for line in lines) == 1
    assert "; .claude" not in message  # no semicolon-joined run-on


def test_a_single_artifact_is_one_line(tmp_path: Path) -> None:
    _plant(tmp_path, "trw-implementer")
    assert len(retired_artifact_row(tmp_path)[1].splitlines()) == 1


def test_a_hostile_name_cannot_forge_a_row_line(tmp_path: Path) -> None:
    _plant(tmp_path, "trw-custom\x1b[2J\n[PASS] forged")
    _status, message = retired_artifact_row(tmp_path)
    assert "\x1b" not in message
    assert not any(line.startswith("[PASS]") for line in message.splitlines())
