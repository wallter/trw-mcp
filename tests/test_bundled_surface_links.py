"""No bundled instruction surface links to a file that does not exist (feedback #117, sub_eDmsPPObvJTeQYPg)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.state.claude_md.sections import _memory_routing

_SURFACES = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "surfaces"
_LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")


def _relative_targets(text: str) -> list[str]:
    targets = [t.split("#", 1)[0] for t in _LINK.findall(text)]
    return [t for t in targets if t and "://" not in t and not t.startswith("mailto:")]


@pytest.mark.parametrize("surface", sorted(p.name for p in _SURFACES.glob("*.md")))
def test_every_relative_link_in_a_bundled_surface_resolves(surface: str) -> None:
    text = (_SURFACES / surface).read_text(encoding="utf-8")
    missing = [t for t in _relative_targets(text) if not (_SURFACES / t).exists()]
    assert missing == []


def test_the_inline_fallback_copy_has_no_dangling_link() -> None:
    missing = [t for t in _relative_targets(_memory_routing._FALLBACK_MEMORY_ROUTING) if not (_SURFACES / t).exists()]
    assert missing == []


def test_link_extractor_sees_a_dangling_link() -> None:
    assert _relative_targets("see [x](nope.md#a) and [y](https://e.com/z) and [z](#top)") == ["nope.md"]
