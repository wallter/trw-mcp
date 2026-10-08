"""AHR rc.2 ``objective.paths``: the bounded glob matcher and the ``handoff check`` scope warning.

Patterns are untrusted record content, so matching must stay bounded; scope compares against the record's
own baseline and counts committed as well as uncommitted changes (independent review findings 7-10).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.handoff._cli_support import _check, _fill, _filled_sealed, _git, _new, _run, repo  # noqa: F401
from trw_mcp.handoff import load
from trw_mcp.handoff._glob import covers


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/*.py", "src/a.py", True),
        ("src/*.py", "src/a.py/private.txt", False),
        ("src/*.py", "src/deep/a.py", False),
        ("src/**", "src/deep/a.py", True),
        ("src/**", "src/odd\nname.py", True),
        ("**/x.py", "x.py", True),
        ("**/x.py", "a/b/x.py", True),
        ("docs", "docs/a.md", True),
        ("docs", "docsx/a.md", False),
        ("a?c.txt", "abc.txt", True),
        ("a?c.txt", "a/c.txt", False),
    ],
)
def test_covers_semantics(pattern: str, path: str, expected: bool) -> None:
    assert covers(pattern, path) is expected


def test_an_adversarial_pattern_is_matched_in_bounded_time() -> None:
    started = time.monotonic()
    assert covers("*a" * 200 + "b", "a" * 400 + "c") is False
    assert time.monotonic() - started < 0.5


def test_a_committed_out_of_scope_change_is_reported(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, sealed = _filled_sealed(capsys, "--tier", "standard", "--next-read", "a.txt", "--path", "src/**")
    (repo / "elsewhere.txt").write_text("committed outside scope\n", encoding="utf-8")
    _git(repo, "add", "elsewhere.txt")
    _git(repo, "commit", "-q", "-m", "outside")
    _code, report = _check(capsys, path, "--digest", sealed)
    assert report["scope"] == {"status": "checked", "outside": ["elsewhere.txt"], "outside_count": 1}


def test_a_dirty_record_without_its_sidecar_is_not_checked(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (repo / "a.txt").write_text("edited before the handoff\n", encoding="utf-8")
    path = _new(capsys, "--tier", "standard", "--subject", "s", "--next-read", "a.txt", "--path", "src/**")
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, err = _run(capsys, "seal", str(path))
    assert code == 0, out + err
    for sidecar in path.parent.glob("*.changed-paths.txt"):
        sidecar.unlink()
    _code, report = _check(capsys, path)
    assert report["scope"]["status"] == "not_checked"
    assert "outside" not in report["scope"]
