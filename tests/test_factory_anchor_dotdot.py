"""FACTORY-ANCHOR-DOTDOT: the no-follow walk stays below its anchor, and cross-run reads anchor at the runs dir.

``O_NOFOLLOW`` refuses a symlinked component, but ``..`` is a real directory entry: before this, a relative
path carrying ``..`` climbed out of the anchor, and every caller's safety rested on its own validation. A
cross-run receipt reference was resolved (following links) and then opened as a symlink-following anchor, so
a symlink in the run tree was read through.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

_BAD = ["../outside.json", "a/../../outside.json", "/etc/hosts", "./x.json", "a/./x.json"]


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    anchor = tmp_path / "anchor"
    (anchor / "a").mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": 1}', encoding="utf-8")
    return anchor, outside


@pytest.mark.parametrize("relative", _BAD)
def test_open_under_refuses_dot_and_absolute_parts(tmp_path: Path, relative: str) -> None:
    from trw_mcp._checkout_access import open_under

    anchor, _ = _tree(tmp_path)
    with pytest.raises(ValueError, match="below the anchor"):
        open_under(anchor, relative)


def test_read_bounded_reports_a_climbing_path_as_path_escape(tmp_path: Path) -> None:
    from trw_mcp.state._factory_read import read_bounded

    anchor, _ = _tree(tmp_path)
    assert read_bounded(anchor, "../outside.json", 1024) == (None, "path_escape")


def test_iter_lines_refuses_a_climbing_path_before_any_yield(tmp_path: Path) -> None:
    from trw_mcp.state._factory_read import iter_lines

    anchor, _ = _tree(tmp_path)
    with pytest.raises(ValueError):
        next(iter_lines(anchor, "../outside.json"))


def test_delete_regular_file_under_never_deletes_above_its_anchor(tmp_path: Path) -> None:
    from trw_mcp._checkout_access import delete_regular_file_under

    anchor, outside = _tree(tmp_path)
    with pytest.raises(ValueError):
        delete_regular_file_under(anchor, "../outside.json")
    assert outside.is_file()


def test_code_index_read_refuses_a_climbing_directory_entry(tmp_path: Path) -> None:
    from trw_mcp.code_index.discovery import read_indexed_file

    anchor, _ = _tree(tmp_path)
    with pytest.raises(ValueError):
        read_indexed_file(anchor, "a/../../outside.json", 1024)


@pytest.mark.parametrize(
    ("run_path", "expected"),
    [
        (".trw/runs/t/b", "t/b"),
        (".trw/runs/t/../../../x", None),
        ("/abs/.trw/runs/t/b", None),
        (".trw/runs", None),
        ("other/t/b", None),
        (".trw/runs/./t/b", None),
    ],
)
def test_run_relative_is_lexical_and_contained(tmp_path: Path, run_path: str, expected: str | None) -> None:
    from trw_mcp.state._factory_read import run_relative

    root = tmp_path / "p"
    assert run_relative(root / ".trw" / "runs", root, run_path) == expected


def _runs(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "p"
    run_a = root / ".trw" / "runs" / "t" / "a"
    (run_a / "meta").mkdir(parents=True)
    real_b = root / ".trw" / "runs" / "t" / "real-b"
    (real_b / "meta" / "receipts" / "build").mkdir(parents=True)
    (real_b / "meta" / "receipts" / "build" / "r1.json").write_text("{}", encoding="utf-8")
    os.symlink(real_b, root / ".trw" / "runs" / "t" / "b")  # a link a peer (or a swap after resolve) planted
    return root, run_a


def test_a_symlinked_cross_run_path_is_refused_not_read_through(tmp_path: Path) -> None:
    """Resolve-then-open accepted this (the link stays inside runs); the no-follow walk refuses it."""
    from trw_mcp.state._factory_status import _resolve

    _root, run_a = _runs(tmp_path)
    label, state = _resolve(run_a, "build", {"run_path": ".trw/runs/t/b", "receipt_id": "r1"}, None)
    assert (label, state) == ("build:r1", "path_escape")
    _label, ok_state = _resolve(run_a, "build", {"run_path": ".trw/runs/t/real-b", "receipt_id": "r1"}, None)
    assert ok_state != "path_escape"  # a plain cross-run reference still resolves


def test_the_verdict_reads_cross_run_receipts_from_the_runs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.state._factory_verdict as verdict

    root, run_a = _runs(tmp_path)
    calls: list[tuple[Path, str]] = []

    def fake_read(anchor: Path, relative: str, _limit: int) -> tuple[bytes | None, str]:
        calls.append((anchor, relative))
        return None, "missing"

    monkeypatch.setattr(verdict, "read_bounded", fake_read)
    ref: dict[str, Any] = {"run_path": ".trw/runs/t/b", "receipt_id": "r1"}
    assert verdict.verification_verdict(run_a, ref, root) == ("unverified", {})
    assert calls == [(root / ".trw" / "runs", "t/b/meta/receipts/verification/r1.json")]
    calls.clear()
    bad: dict[str, Any] = {"run_path": ".trw/runs/../../x", "receipt_id": "r1"}
    assert verdict.verification_verdict(run_a, bad, root) == ("unverified", {})
    assert calls == []  # refused before any open
