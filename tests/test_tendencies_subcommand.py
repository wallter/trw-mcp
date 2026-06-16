"""Tests for the `trw-mcp tendencies` CLI subcommand (PRD-QUAL-109 FR-03)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS, _run_tendencies


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _dirty(root: Path) -> None:
    for c in range(1, 7):
        _write(root, f"handoff-archive/cycle-{c:02d}.md", f"# Cycle {c}\nBundle: 6 PRDs this cycle.\n")


def _args(corpus: Path | None, *, as_json: bool = False) -> argparse.Namespace:
    return argparse.Namespace(corpus=str(corpus) if corpus else None, as_json=as_json)


def test_tendencies_subcommand_registered() -> None:
    assert "tendencies" in SUBCOMMAND_HANDLERS


def test_tendencies_subcommand_registered_and_advisory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """End-to-end over a fixture corpus: exit 0 on dirty corpus, no writes, --json valid."""
    _dirty(tmp_path)
    before = {p for p in tmp_path.rglob("*") if p.is_file()}

    with pytest.raises(SystemExit) as exc:
        _run_tendencies(_args(tmp_path))
    assert exc.value.code == 0

    after = {p for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after  # advisory: nothing written

    out = capsys.readouterr().out
    assert "QUOTA_GAMING" in out


def test_tendencies_subcommand_clean_corpus_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "handoff-archive/c.md", "# Cycle 1\nShipped FR-1 with behavior tests.\n")
    with pytest.raises(SystemExit) as exc:
        _run_tendencies(_args(tmp_path))
    assert exc.value.code == 0
    assert "no tendencies detected" in capsys.readouterr().out.lower()


def test_tendencies_subcommand_json_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _dirty(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_tendencies(_args(tmp_path, as_json=True))
    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "findings" in payload
    for f in payload["findings"]:
        assert {"tendency", "evidence", "countermeasure"} <= set(f.keys())


def test_tendencies_subcommand_default_corpus_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no --corpus, the default-root resolution runs and still exits 0."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        _run_tendencies(_args(None))
    assert exc.value.code == 0
