"""``parse_frontmatter`` parses each distinct frontmatter block once, and callers cannot reach the cache."""

from __future__ import annotations

import pytest

from trw_mcp.state import prd_utils
from trw_mcp.state.prd_utils import parse_frontmatter

pytestmark = pytest.mark.unit

_DOC = (
    "---\n"
    "prd:\n"
    "  id: PRD-TEST-001\n"
    "  status: draft\n"
    "  traceability:\n"
    "    enables:\n"
    "      - PRD-TEST-002\n"
    "title: kept beside the nested prd block\n"
    "---\n\nbody\n"
)


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    prd_utils._parse_frontmatter_block.cache_clear()


def test_a_second_parse_of_the_same_block_is_a_cache_hit_with_an_equal_result() -> None:
    first = parse_frontmatter(_DOC)
    second = parse_frontmatter(_DOC + "more body that is not frontmatter\n")

    info = prd_utils._parse_frontmatter_block.cache_info()
    assert (info.hits, info.misses) == (1, 1)
    assert first == second
    assert first["id"] == "PRD-TEST-001" and first["title"] == "kept beside the nested prd block"


def test_the_cached_result_equals_a_fresh_parse() -> None:
    warm = parse_frontmatter(_DOC)
    prd_utils._parse_frontmatter_block.cache_clear()
    assert parse_frontmatter(_DOC) == warm


def test_mutating_a_returned_dict_never_reaches_the_cache() -> None:
    first = parse_frontmatter(_DOC)
    first["id"] = "MUTATED"
    first["traceability"]["enables"].append("PRD-TEST-999")  # type: ignore[index]
    first["added"] = True

    again = parse_frontmatter(_DOC)

    assert again["id"] == "PRD-TEST-001"
    assert again["traceability"] == {"enables": ["PRD-TEST-002"]}
    assert "added" not in again
    assert again is not first


def test_malformed_and_absent_frontmatter_still_return_a_fresh_empty_dict() -> None:
    assert parse_frontmatter("no frontmatter here") == {}
    bad = parse_frontmatter("---\n: : [unbalanced\n---\n")
    assert bad == {}
    bad["x"] = 1
    assert parse_frontmatter("---\n: : [unbalanced\n---\n") == {}
