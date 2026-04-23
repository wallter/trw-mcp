"""Lint test enforcing SKILL.md effective-LOC budgets (PRD-QUAL-074 FR07).

Effective LOC = non-blank lines that are NOT purely single-line HTML comments.
Default threshold 350 LOC; per-skill overrides live in ``LOC_OVERRIDES``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Paths to scan. Bundled skills under trw-mcp/src/trw_mcp/data/skills/ mirror
# the installed .claude/skills tree; both are subject to the same budget.
_SKILL_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / ".claude" / "skills",
    _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills",
)

DEFAULT_LOC_THRESHOLD = 350
LOC_OVERRIDES: dict[str, int] = {
    "trw-team-playbook": 300,
    "trw-sprint-team": 280,
    # ALLOW-LIST JUSTIFICATION (PRD-QUAL-074): non-TRW design-system skills
    # carry large reference tables (color tokens, component catalogs) inline;
    # they are out-of-scope for this PRD and tracked separately. Raise the
    # per-skill limit rather than truncate reference material.
    "tailwind-design-system": 800,
    "ui-component-patterns": 500,
}

_HTML_COMMENT_ONLY = re.compile(r"^\s*<!--.*-->\s*$")


def _effective_loc(path: Path) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    count = 0
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        if _HTML_COMMENT_ONLY.match(ln):
            continue
        count += 1
    return count


def _iter_skill_files() -> list[tuple[str, Path]]:
    """Yield (skill_name, SKILL.md path) for every skill under the known dirs."""
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for base in _SKILL_DIRS:
        if not base.is_dir():
            continue
        for skill_md in base.glob("*/SKILL.md"):
            name = skill_md.parent.name
            # Prefer the .claude/skills copy if both exist; both must pass
            # but we only emit one (name, path) per skill to avoid duplicate
            # failure rows on identical content.
            key = f"{name}@{base.name}"
            if key in seen:
                continue
            seen.add(key)
            out.append((name, skill_md))
    return out


def test_skill_loc_within_threshold() -> None:
    offenders: list[str] = []
    for name, path in _iter_skill_files():
        limit = LOC_OVERRIDES.get(name, DEFAULT_LOC_THRESHOLD)
        loc = _effective_loc(path)
        if loc > limit:
            offenders.append(f"{path.relative_to(_REPO_ROOT)}: {loc} LOC > limit {limit}")
    assert not offenders, "SKILL.md files exceeding LOC budget:\n  " + "\n  ".join(offenders)


def test_bloated_skill_fails(tmp_path: Path) -> None:
    """Negative control: synthetic 500-line SKILL.md exceeds the default threshold."""
    fake = tmp_path / "SKILL.md"
    fake.write_text("# Fake\n" + "content line\n" * 500, encoding="utf-8")
    assert _effective_loc(fake) > DEFAULT_LOC_THRESHOLD
