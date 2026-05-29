"""Shared helpers for split recall integration tests."""

from __future__ import annotations


def _make_entry(entry_id: str = "L-001", **kwargs: object) -> dict[str, object]:
    return {
        "id": entry_id,
        "summary": "test learning",
        "impact": 0.5,
        "created": "2026-01-01T00:00:00Z",
        **kwargs,
    }


def _make_config() -> object:
    """Return a minimal TRWConfig-like object."""
    from trw_mcp.models.config import get_config

    return get_config()


def _make_sized_entry(entry_id: str, word_count: int) -> dict[str, object]:
    """Entry with known content size for budget testing."""
    return {
        "id": entry_id,
        "summary": f"Learning {entry_id}",
        "content": " ".join(f"word{i}" for i in range(word_count)),
        "detail": "",
        "tags": [],
        "impact": 0.5,
        "created": "2026-01-01T00:00:00Z",
    }
