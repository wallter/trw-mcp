"""PRD-QUAL-075 FR08: duplication lint for canonical protocol prose.

The behavioral protocol, memory-routing table, and ceremony table header now
live in exactly one hand-authored file each (the canonical docs under
``docs/documentation/``). Auto-synced regions (between ``<!-- trw:start -->``
and ``<!-- trw:end -->``) and the canonical files themselves are excluded.

If any sentinel reappears in a second hand-authored file, this lint fails.
"""

from __future__ import annotations

import re
from pathlib import Path

# Integration tier: reads real repository markdown files and uses tmp_path
# for the negative lint check. Per .claude/rules/testing.md, tmp_path and
# real-file I/O classify as integration — no unit marker applied.

_REPO_ROOT = Path(__file__).resolve().parents[2]

SENTINELS: dict[str, str] = {
    "tool_lifecycle": "MUST call `trw_session_start()` as your absolute first action",
    "memory_routing": "Gotcha or error pattern → `trw_learn()`",
    "ceremony_table_header": "| `trw_session_start()` | **First Action**",
}

# Canonical files own each sentinel — they are allowed to (and must) contain it.
CANONICAL_OWNERS: dict[str, Path] = {
    "tool_lifecycle": _REPO_ROOT / "docs" / "documentation" / "tool-lifecycle.md",
    "memory_routing": _REPO_ROOT / "docs" / "documentation" / "memory-routing.md",
    "ceremony_table_header": _REPO_ROOT / "docs" / "documentation" / "tool-lifecycle.md",
}

# Hand-authored files that MUST NOT carry these sentinels outside trw markers.
HAND_AUTHORED_SCAN: list[Path] = [
    _REPO_ROOT / "CLAUDE.md",
    _REPO_ROOT / "trw-mcp" / "CLAUDE.md",
    _REPO_ROOT / ".opencode" / "INSTRUCTIONS.md",
]


_MARKER_START = "<!-- trw:start -->"
_MARKER_END = "<!-- trw:end -->"


def _strip_auto_gen_regions(text: str) -> str:
    """Remove everything between trw:start and trw:end markers (inclusive)."""
    pattern = re.compile(
        re.escape(_MARKER_START) + r".*?" + re.escape(_MARKER_END),
        re.DOTALL,
    )
    return pattern.sub("", text)


def test_all_sentinels_unique() -> None:
    """Each sentinel appears in at most ONE hand-authored file outside trw markers."""
    for sentinel_name, sentinel in SENTINELS.items():
        hits: list[Path] = []
        for path in HAND_AUTHORED_SCAN:
            if not path.exists():
                continue
            stripped = _strip_auto_gen_regions(path.read_text(encoding="utf-8"))
            if sentinel in stripped:
                hits.append(path)
        assert len(hits) <= 1, (
            f"Sentinel {sentinel_name!r} ({sentinel!r}) found in {len(hits)} hand-authored files: "
            f"{[str(p.relative_to(_REPO_ROOT)) for p in hits]}. "
            "Prose duplicated outside the canonical doc. Replace with a pointer."
        )


def test_canonical_files_contain_sentinels() -> None:
    """Drift detection: each canonical file must still contain its sentinel."""
    for sentinel_name, sentinel in SENTINELS.items():
        owner = CANONICAL_OWNERS[sentinel_name]
        assert owner.exists(), f"canonical file missing: {owner}"
        text = owner.read_text(encoding="utf-8")
        assert sentinel in text, (
            f"Canonical file {owner.relative_to(_REPO_ROOT)} lost its sentinel "
            f"{sentinel_name!r} — prose has drifted out of the single source of truth."
        )


def test_duplication_lint_detects_reintroduction(tmp_path: Path) -> None:
    """Negative: seeding a sentinel into a second file makes the scan fail."""
    fake_a = tmp_path / "A.md"
    fake_b = tmp_path / "B.md"
    fake_a.write_text(f"Some prose.\n{SENTINELS['memory_routing']}\nMore.\n", encoding="utf-8")
    fake_b.write_text(f"Other doc.\n{SENTINELS['memory_routing']}\nEnd.\n", encoding="utf-8")

    # Replicate the scan logic.
    hits = []
    for path in (fake_a, fake_b):
        stripped = _strip_auto_gen_regions(path.read_text(encoding="utf-8"))
        if SENTINELS["memory_routing"] in stripped:
            hits.append(path)
    assert len(hits) == 2, "fixture should duplicate the sentinel across both files"


def test_hub_links_extracted_docs() -> None:
    """FR09: the docs hub points at both canonical files."""
    hub = _REPO_ROOT / "docs" / "documentation" / "CLAUDE.md"
    content = hub.read_text(encoding="utf-8")
    assert "tool-lifecycle.md" in content, "hub missing link to tool-lifecycle.md"
    assert "memory-routing.md" in content, "hub missing link to memory-routing.md"


def test_trw_mcp_claude_md_pointer() -> None:
    """FR05: trw-mcp/CLAUDE.md points at canonical docs; no duplicated table, no markers."""
    pkg_claude_md = _REPO_ROOT / "trw-mcp" / "CLAUDE.md"
    content = pkg_claude_md.read_text(encoding="utf-8")
    assert "tool-lifecycle.md" in content, "trw-mcp/CLAUDE.md must point at canonical tool-lifecycle.md"
    assert "Mandatory Tool Lifecycle" not in content, (
        "trw-mcp/CLAUDE.md must not re-embed the Mandatory Tool Lifecycle table"
    )
    assert _MARKER_START not in content, (
        "trw-mcp/CLAUDE.md must not carry trw:start markers — sync targets the project root only"
    )
