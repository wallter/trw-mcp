"""A file name holding terminal control characters is escaped everywhere the CLI echoes it (KEPT-REASON-ESCAPE).

``printable`` (bootstrap/_utils.py) is the one helper; the sweep escapes names where it builds a reason, and the
CLI escapes at the print boundary too, so a name or message any source missed still cannot drive the terminal.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.bootstrap._utils import printable
from trw_mcp.server import _subcommands
from trw_mcp.server._uninstall_report import display
from trw_mcp.server._update_report import print_retired

pytestmark = pytest.mark.unit

ESC = "\x1b"
HOSTILE = f"skills/x{ESC}[2Jy{ESC}]0;pwned\x07.md"


def _no_raw_controls(text: str) -> None:
    assert not any(ord(c) < 32 and c not in "\n" for c in text), repr(text)
    assert "\x7f" not in text


def test_the_warning_block_escapes_control_characters_and_keeps_the_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    _subcommands._print_warning_block([f"{HOSTILE} (not TRW's unchanged bytes): kept", "plain: kept"])

    out = capsys.readouterr().out
    _no_raw_controls(out)
    assert "WARNING: skills/x\\x1b[2Jy" in out, out
    assert "WARNING: plain: kept" in out, "an ordinary warning is unchanged"


def test_the_retired_listing_escapes_control_characters(capsys: pytest.CaptureFixture[str]) -> None:
    print_retired([HOSTILE, "docs/ok.md"])

    out = capsys.readouterr().out
    _no_raw_controls(out)
    assert "Removed retired TRW file: docs/ok.md" in out


def test_display_escapes_and_leaves_printable_paths_byte_identical(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    hostile = target / f"a{ESC}[31mb"
    assert display(hostile, target) == "a\\x1b[31mb"
    assert display(Path("/elsewhere") / f"c{ESC}d", target) == "/elsewhere/c\\x1bd"
    ordinary = target / "skïll" / "SKILL.md"  # printable non-ASCII is left alone
    assert display(ordinary, target) == "skïll/SKILL.md"


def test_update_project_prints_a_hostile_kept_name_escaped_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = {
        "created": [],
        "updated": [],
        "preserved": [],
        "cleaned": [],
        "errors": [],
        "warnings": [f"{HOSTILE} (not TRW's unchanged bytes): kept"],
        "retired": [HOSTILE],
    }
    monkeypatch.setattr("trw_mcp.bootstrap.update_project", lambda *a, **k: result)
    args = argparse.Namespace(target_dir=str(tmp_path), pip_install=False, dry_run=False, ide=None, reprovision=None)
    monkeypatch.setattr(_subcommands, "_is_detailed_cli", lambda _a: False)
    monkeypatch.setattr(_subcommands, "_is_quiet_cli", lambda _a: False)

    with pytest.raises(SystemExit) as finished:
        _subcommands._run_update_project(args)
    assert finished.value.code in (0, None)

    out = capsys.readouterr()
    _no_raw_controls(out.out + out.err)
    assert out.out.count("\\x1b[2J") >= 2, "both the warning and the retired line name the file, escaped"


def test_the_helper_is_the_one_from_untracked_trw_entries() -> None:
    """Reuse, not a second implementation: the uninstall listing and the CLI share ``bootstrap._utils.printable``."""
    from trw_mcp.server import _uninstall_corpus

    assert _uninstall_corpus.printable is printable
    assert printable("plain") == "plain"
    assert printable("a\nb") == "a\\nb"


def test_escaping_is_idempotent_so_a_name_the_source_already_escaped_is_not_escaped_twice(
    capsys: pytest.CaptureFixture[str],
) -> None:
    once = printable(HOSTILE)
    assert printable(once) == once

    _subcommands._print_warning_block([f"{once} (not TRW's unchanged bytes): kept"])

    out = capsys.readouterr().out
    assert "\\\\x1b" not in out, "the boundary escaped an already-escaped name a second time"
    assert out.count("\\x1b") == 2, out  # exactly one escape sequence per ESC in the name


def test_a_real_kept_reason_from_the_sweep_passes_the_boundary_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """W1's source-level escape (556760161c) and this boundary compose: one escape, never two."""
    from trw_mcp.bootstrap._retire import retire_tree

    root = tmp_path / "proj"
    skill = root / ".claude" / "skills" / "old-skill"
    skill.mkdir(parents=True)
    (skill / f"notes{ESC}[2J.md").write_text("mine", encoding="utf-8")

    kept = [f"{p} ({why})" for p, why in retire_tree(skill, root, lambda _f: set()).kept]

    assert kept, "non-vacuity: an unlisted file is kept and named"
    assert all("\x1b" not in reason for reason in kept), "the source already escaped the name"
    _subcommands._print_warning_block(kept)
    out = capsys.readouterr().out
    assert out.count("\\x1b[2J") == len(kept) and "\\\\x1b" not in out, out
    assert (skill / f"notes{ESC}[2J.md").read_text(encoding="utf-8") == "mine", "the kept file's bytes survive"


def test_refusal_text_escapes_a_guard_reason_that_names_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.server import _uninstall_report

    hostile = tmp_path / "cfg.json"
    monkeypatch.setitem(_uninstall_report.REFUSAL_REASONS, hostile, f"could not read {tmp_path}/a{ESC}[31mb")

    text = _uninstall_report.refusal_text(hostile, tmp_path)

    _no_raw_controls(text)
    assert "a\\x1b[31mb" in text
