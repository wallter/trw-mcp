"""Dedup handle result TypedDict (_learning_helpers.py)."""

from __future__ import annotations

from typing import TypedDict


class DedupHandleResult(TypedDict, total=False):
    """Return shape of ``check_and_handle_dedup()`` on skip or merge.

    ``status`` is always present.  Returned when a duplicate is found;
    ``None`` is returned when no duplicate is detected and normal storage
    should proceed.
    """

    status: str  # "skipped" | "merged"
    learning_id: str
    duplicate_of: str
    similarity: float | str  # float on skip path, str (pre-formatted) on merge path
    message: str
    # merge-specific keys
    merged_into: str
    new_id: str
