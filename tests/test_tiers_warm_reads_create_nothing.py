"""trw-mcp's warm-tier reads and deletes leave the directory alone; a write still creates the sidecar."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._tiers_test_support import make_tier_manager

pytestmark = pytest.mark.unit


def _listing(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.mark.parametrize("read", ["warm_search", "warm_remove"])
def test_a_warm_read_on_a_fresh_trw_dir_creates_nothing(tmp_path: Path, read: str) -> None:
    manager = make_tier_manager(tmp_path)
    before = _listing(tmp_path)

    if read == "warm_search":
        assert manager.warm_search(["anything"]) == []
    else:
        manager.warm_remove("missing")

    assert _listing(tmp_path) == before


def test_a_warm_write_still_creates_the_sidecar_and_a_search_finds_it(tmp_path: Path) -> None:
    manager = make_tier_manager(tmp_path)
    manager.warm_add("entry-1", {"summary": "keyword sidecar row", "tags": ["alpha"]})

    assert (tmp_path / ".trw" / "memory" / "warm.jsonl").is_file()
    assert [hit["id"] for hit in manager.warm_search(["keyword"])] == ["entry-1"]
