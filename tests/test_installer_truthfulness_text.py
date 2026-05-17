"""Installer-facing truthfulness wording regression tests."""

from __future__ import annotations

from pathlib import Path


def test_installer_tip_pairs_deliver_with_build_check() -> None:
    template = Path("scripts/install-trw.template.py").read_text(encoding="utf-8")

    assert "Run trw_build_check() before trw_deliver()" in template
    assert "acceptable failures" in template
    assert "Run trw_deliver() at session end to persist your discoveries" not in template
