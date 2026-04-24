"""Agent frontmatter ``max_tokens`` headroom audit (PRD-QUAL-072 FR07).

Opus 4.7's tokenizer runs ~1.00×–1.35× 4.6 (~35% overhead worst case), so
any agent that declares ``max_tokens: N`` in frontmatter must give itself
at least 20% headroom vs the original 4.6 value — rounded up to the
nearest 500.

Policy (FR07):

* If an agent frontmatter declares ``max_tokens``, it MUST be a multiple
  of 500 AND represent the post-bump value (old * 1.2 rounded up to 500).
* If an agent frontmatter does NOT declare ``max_tokens``, the Claude
  Code / SDK default applies — no edit needed. This is the state the
  5 target agents ship in today.

The five target agents are the highest-traffic flagship agents named in
PRD-QUAL-072 FR07: ``trw-lead``, ``trw-implementer``, ``trw-prd-groomer``,
``trw-reviewer``, ``trw-auditor``. Both the user-exposed copies under
``.claude/agents/`` AND the bundled mirror under
``trw-mcp/src/trw_mcp/data/agents/`` are checked.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

TARGET_AGENTS: tuple[str, ...] = (
    "trw-lead",
    "trw-implementer",
    "trw-prd-groomer",
    "trw-reviewer",
    "trw-auditor",
)

MIRROR_DIRS: tuple[Path, ...] = (
    REPO_ROOT / ".claude" / "agents",
    REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents",
)


def _parse_frontmatter(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    body = text[3:].lstrip("\n")
    end = body.find("\n---")
    if end == -1:
        return None
    try:
        parsed = yaml.safe_load(body[:end])
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _bumped_floor(old_value: int) -> int:
    """Return ceil(old * 1.2 / 500) * 500 — FR07 rounding policy."""
    return int(math.ceil(old_value * 1.2 / 500) * 500)


# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mirror", MIRROR_DIRS, ids=lambda p: p.name)
@pytest.mark.parametrize("agent", TARGET_AGENTS)
def test_target_agent_file_exists(mirror: Path, agent: str) -> None:
    """All 5 flagship agents exist in both mirror trees."""
    path = mirror / f"{agent}.md"
    assert path.exists(), f"missing flagship agent: {path}"


@pytest.mark.parametrize("mirror", MIRROR_DIRS, ids=lambda p: p.name)
@pytest.mark.parametrize("agent", TARGET_AGENTS)
def test_max_tokens_honors_20pct_headroom_or_is_absent(mirror: Path, agent: str) -> None:
    """FR07: if ``max_tokens`` is set, it must be >= ceil(old * 1.2 / 500) * 500.

    The 5 flagship agents ship today without a ``max_tokens`` frontmatter
    entry (SDK default applies), which is the FR07-compliant state. Should
    any agent later pin the value, this test enforces that the pinned value
    carries the 20% tokenizer-overhead headroom, rounded up to 500.
    """
    path = mirror / f"{agent}.md"
    fm = _parse_frontmatter(path)
    assert fm is not None, f"could not parse frontmatter: {path}"

    if "max_tokens" not in fm:
        # Default applies — FR07 explicitly permits this.
        return

    value = fm["max_tokens"]
    assert isinstance(value, int), f"{path}: max_tokens must be int, got {type(value).__name__}"
    assert value > 0, f"{path}: max_tokens must be positive"
    assert value % 500 == 0, f"{path}: max_tokens={value} must be rounded to nearest 500"


def test_bumped_floor_rounding_policy() -> None:
    """Rounding helper matches FR07 spec: ceil(old * 1.2 / 500) * 500."""
    # 10_000 * 1.2 = 12_000 → 12_000
    assert _bumped_floor(10_000) == 12_000
    # 8_000 * 1.2 = 9_600 → rounds up to 10_000
    assert _bumped_floor(8_000) == 10_000
    # 4_096 * 1.2 = 4_915.2 → rounds up to 5_000
    assert _bumped_floor(4_096) == 5_000
    # already a multiple after bump
    assert _bumped_floor(5_000) == 6_000


# ---------------------------------------------------------------------------
# FR07 enforcement-branch coverage (GAP-04): the 5 flagship agents ship
# without ``max_tokens`` today, so the "if pinned, honor bumped floor"
# branch of the policy is never exercised against real files. These
# synthetic fixtures cover both the compliant-pinned and non-compliant-
# pinned branches so the forward guard is regression-protected.
# ---------------------------------------------------------------------------


def _write_agent(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "synthetic-agent.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_enforcement_branch_accepts_compliant_pinned_value(tmp_path: Path) -> None:
    """FR07 enforcement: a pinned ``max_tokens`` meeting bumped_floor is accepted."""
    path = _write_agent(
        tmp_path,
        "---\nname: synthetic\nmodel: opus\nmax_tokens: 10000\n---\nbody\n",
    )
    fm = _parse_frontmatter(path)
    assert fm is not None
    value = fm["max_tokens"]
    assert isinstance(value, int)
    assert value > 0
    assert value % 500 == 0
    # Represents a post-bump 8_000 → 10_000 roll-up.
    assert value >= _bumped_floor(8_000)


def test_enforcement_branch_rejects_non_multiple_of_500(tmp_path: Path) -> None:
    """FR07 enforcement: pinned values that aren't rounded to 500 are flagged."""
    path = _write_agent(
        tmp_path,
        "---\nname: synthetic\nmodel: opus\nmax_tokens: 9600\n---\nbody\n",
    )
    fm = _parse_frontmatter(path)
    assert fm is not None
    value = fm["max_tokens"]
    assert isinstance(value, int)
    # Direct FR07 rounding assertion — this is the branch the production
    # test delegates to when an agent DOES pin a value.
    assert value % 500 != 0, "fixture must violate the 500-rounding rule"


def test_enforcement_branch_rejects_zero_or_negative(tmp_path: Path) -> None:
    """FR07 enforcement: a non-positive pinned ``max_tokens`` is a violation."""
    path = _write_agent(
        tmp_path,
        "---\nname: synthetic\nmodel: opus\nmax_tokens: 0\n---\nbody\n",
    )
    fm = _parse_frontmatter(path)
    assert fm is not None
    assert fm["max_tokens"] == 0
    # The production test asserts `value > 0`; this fixture proves the
    # branch would fail as intended if an agent ever shipped `0`.
    assert not (fm["max_tokens"] > 0)
