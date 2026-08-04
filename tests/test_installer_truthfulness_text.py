"""Installer-facing truthfulness wording regression tests."""

from __future__ import annotations

from pathlib import Path

#: Anchored to this file, not to the process cwd. These reads used to be bare
#: relative paths, so they resolved only when pytest happened to run with
#: ``cwd=trw-mcp/`` (what ``make test-python`` does) and raised
#: FileNotFoundError from the repo root. A test whose result depends on where it
#: was invoked from reports a defect that is not there — and, run the other way,
#: can hide one.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_installer_tip_pairs_deliver_with_build_check() -> None:
    template = (_PACKAGE_ROOT / "scripts" / "install-trw.template.py").read_text(encoding="utf-8")

    assert "Run trw_build_check() before trw_deliver()" in template
    assert "acceptable failures" in template
    assert "Run trw_deliver() at session end to persist your discoveries" not in template
