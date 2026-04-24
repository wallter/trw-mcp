"""PRD-QUAL-075 FR07: LOC budget + profile-count lints for CLAUDE.md files.

Guards against the regressions that produced this PRD in the first place:
  - root ``CLAUDE.md`` accreting past 200 effective lines.
  - ``trw-mcp/CLAUDE.md`` re-growing past 40 lines (duplicated prose).
  - ``.opencode/INSTRUCTIONS.md`` (when present) exceeding 100 lines.
  - Stale "Five built-in profiles" phrasing lingering after the profile
    registry expanded to 8 built-in profiles.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROOT_CLAUDE_MD = _REPO_ROOT / "CLAUDE.md"
_TRW_MCP_CLAUDE_MD = _REPO_ROOT / "trw-mcp" / "CLAUDE.md"
_OPENCODE_INSTRUCTIONS = _REPO_ROOT / ".opencode" / "INSTRUCTIONS.md"
_PROFILES_DIR = _REPO_ROOT / "trw-mcp" / "data" / "profiles"


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_root_claude_md_loc_budget() -> None:
    """FR07: the project-root CLAUDE.md must stay at or below 200 effective LOC."""
    assert _ROOT_CLAUDE_MD.exists(), f"missing {_ROOT_CLAUDE_MD}"
    loc = _line_count(_ROOT_CLAUDE_MD)
    assert loc <= 200, f"root CLAUDE.md is {loc} LOC; budget is 200. Extract prose into docs/documentation/."


def test_trw_mcp_claude_md_loc_budget() -> None:
    """FR07: the package-local trw-mcp/CLAUDE.md must stay at or below 40 LOC."""
    assert _TRW_MCP_CLAUDE_MD.exists(), f"missing {_TRW_MCP_CLAUDE_MD}"
    loc = _line_count(_TRW_MCP_CLAUDE_MD)
    assert loc <= 40, (
        f"trw-mcp/CLAUDE.md is {loc} LOC; budget is 40. Point at "
        "docs/documentation/tool-lifecycle.md instead of re-embedding prose."
    )


def test_opencode_instructions_loc_budget() -> None:
    """FR07: .opencode/INSTRUCTIONS.md stays <=100 LOC (skip if absent)."""
    if not _OPENCODE_INSTRUCTIONS.exists():
        pytest.skip(".opencode/INSTRUCTIONS.md not present")
    loc = _line_count(_OPENCODE_INSTRUCTIONS)
    assert loc <= 100, f".opencode/INSTRUCTIONS.md is {loc} LOC; budget is 100."


def test_profile_count_current() -> None:
    """FR12: stale 'Five built-in profiles' phrase must not appear in CLAUDE.md."""
    content = _ROOT_CLAUDE_MD.read_text(encoding="utf-8")
    assert "Five built-in profiles" not in content, (
        "CLAUDE.md still says 'Five built-in profiles'. Update to match the "
        "current profile registry count."
    )


def test_profile_count_matches_registry() -> None:
    """FR12: CLAUDE.md mentions the actual built-in profile count."""
    if not _PROFILES_DIR.is_dir():
        pytest.skip(f"profile registry not found at {_PROFILES_DIR}")
    count = sum(1 for p in _PROFILES_DIR.iterdir() if p.suffix == ".yaml")
    assert count > 0, "no profiles discovered"
    content = _ROOT_CLAUDE_MD.read_text(encoding="utf-8")

    spelled = {
        1: "One",
        2: "Two",
        3: "Three",
        4: "Four",
        5: "Five",
        6: "Six",
        7: "Seven",
        8: "Eight",
        9: "Nine",
        10: "Ten",
    }
    digit_phrase = f"{count} built-in profiles"
    spelled_phrase = f"{spelled.get(count, str(count))} built-in profiles"
    assert digit_phrase in content or spelled_phrase in content, (
        f"CLAUDE.md must state the current count ({count}) of built-in profiles; "
        f"expected '{digit_phrase}' or '{spelled_phrase}'."
    )


def test_loc_lint_fails_at_201(tmp_path: Path) -> None:
    """Negative: a synthetic 201-line file triggers the same budget check."""
    fake = tmp_path / "OVERSIZE.md"
    fake.write_text("\n".join(["x"] * 201), encoding="utf-8")
    loc = _line_count(fake)
    assert loc == 201
    # Mirror the production assertion; it must fail under the budget.
    with pytest.raises(AssertionError):
        assert loc <= 200, f"oversize is {loc} LOC"
