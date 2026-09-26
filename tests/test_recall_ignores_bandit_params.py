"""No backend bandit weight reaches recall, and no bandit code ships publicly.

trw-mcp 6.1.0 took the cached backend ``bandit_params`` weight out of recall
ranking. PRD-CORE-303 (7.0.0) then removed the last reader: RecallContext no
longer carries the intelligence cache, and the nudge selector ranks in recall
order. These tests pin both, and FR06's guard keeps the word out of the two
public src trees (trw-mcp and trw-memory).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.scoring._recall import RecallContext, rank_targeted_by_utility

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_SRC_TREES = (_REPO_ROOT / "trw-mcp" / "src", _REPO_ROOT / "trw-memory" / "src")
# The retired-key list names removed config keys so an old config.yaml loads
# with a warning (FR03). It records what is gone; it is not bandit code.
_ALLOWED = frozenset({_REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "config-retired-keys.json"})


def _entry(entry_id: str, summary: str, impact: float) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": summary,
        "detail": "",
        "tags": ["test"],
        "impact": impact,
        "type": "pattern",
        "status": "active",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "domain": ["auth"],
        "phase_affinity": [],
        "team_origin": "",
        "anchor_validity": 1.0,
    }


_ENTRIES = [
    _entry("L-high", "auth token refresh", 0.9),
    _entry("L-mid", "auth token refresh", 0.6),
    _entry("L-low", "auth token refresh", 0.3),
]


def test_recall_context_carries_no_intel_cache() -> None:
    assert "intel_cache" not in {field.name for field in dataclasses.fields(RecallContext)}
    with pytest.raises(TypeError):
        RecallContext(intel_cache=object())  # type: ignore[call-arg]


@pytest.mark.parametrize("query_tokens", [["auth", "token"], []], ids=["targeted", "wildcard"])
def test_recall_ranks_by_utility_alone(query_tokens: list[str]) -> None:
    context = RecallContext(inferred_domains={"auth"})
    ranked = rank_targeted_by_utility([dict(e) for e in _ENTRIES], query_tokens, 0.3, context=context)
    assert [e["id"] for e in ranked] == ["L-high", "L-mid", "L-low"]


def _bandit_mentions(roots: tuple[Path, ...], allowed: frozenset[Path]) -> list[str]:
    hits: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path in allowed:
                continue
            if b"bandit" in path.read_bytes().lower():
                hits.append(str(path))
    return hits


def test_public_src_trees_carry_no_bandit_code() -> None:
    """PRD-CORE-303 FR06: case-insensitive, comments and docstrings included."""
    assert _bandit_mentions(_PUBLIC_SRC_TREES, _ALLOWED) == []


def test_the_guard_names_a_planted_file(tmp_path: Path) -> None:
    planted = tmp_path / "pkg" / "planted.py"
    planted.parent.mkdir()
    planted.write_text("# a Bandit arm\n", encoding="utf-8")
    (tmp_path / "pkg" / "clean.py").write_text("x = 1\n", encoding="utf-8")

    assert _bandit_mentions((tmp_path,), frozenset()) == [str(planted)]
