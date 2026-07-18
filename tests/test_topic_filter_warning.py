"""Tests for F17: topic_filter_warning surfaced when topic filter is silently ignored.

Drives the real recall path via execute_recall so the full call-site is exercised,
not just the helper in isolation.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests._recall_integration_support import _make_entry
from tests._structlog_capture import captured_structlog  # noqa: F401


def _enter_standard_patches(stack: ExitStack, trw_dir: Path, entries: list[dict[str, object]] | None = None) -> None:
    """Enter the boilerplate patches for execute_recall into an ExitStack."""
    if entries is None:
        entries = [_make_entry("L-001"), _make_entry("L-002")]
    stack.enter_context(patch("trw_mcp.tools._recall_impl.build_recall_context", return_value=None))
    stack.enter_context(patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=list(entries)))
    stack.enter_context(patch("trw_mcp.state.memory_adapter.update_access_tracking"))
    stack.enter_context(patch("trw_mcp.state.recall_search.search_patterns", return_value=[]))
    stack.enter_context(patch("trw_mcp.state.recall_search.collect_context", return_value={}))
    stack.enter_context(patch("trw_mcp.tools._recall_impl._track_recall"))
    stack.enter_context(patch("trw_mcp.tools._recall_impl._augment_with_remote", side_effect=lambda q, m: list(m)))


# ---------------------------------------------------------------------------
# Scenario 1: clusters.json is missing entirely
# ---------------------------------------------------------------------------


def test_topic_filter_warning_clusters_missing(
    tmp_path: Path,
    captured_structlog: list[dict],  # type: ignore[type-arg]
) -> None:
    """topic_filter requested but clusters.json is absent -> non-empty warning + log."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()
    # Do NOT create clusters.json — it must be missing.

    with ExitStack() as stack:
        _enter_standard_patches(stack, trw_dir)
        result = execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            topic="security",
            max_results=5,
            _rank_by_utility=lambda matches, *_a, **_kw: matches,
        )

    assert result.get("topic_filter_ignored") is True
    warning = result.get("topic_filter_warning", "")
    assert warning, "topic_filter_warning must be non-empty when filter is ignored"
    assert "clusters" in warning.lower() or "missing" in warning.lower()

    warning_events = [e for e in captured_structlog if e.get("event") == "topic_filter_ignored"]
    assert warning_events, "logger.warning('topic_filter_ignored') must be emitted"
    assert warning_events[0].get("topic") == "security"
    assert warning_events[0].get("reason") == "clusters_missing"


# ---------------------------------------------------------------------------
# Scenario 2: clusters.json exists but the requested slug is absent
# ---------------------------------------------------------------------------


def test_topic_filter_warning_slug_absent(
    tmp_path: Path,
    captured_structlog: list[dict],  # type: ignore[type-arg]
) -> None:
    """topic requested but slug not in clusters.json -> non-empty warning + log."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()

    # Create clusters.json with a different slug.
    clusters_dir = trw_dir / config.knowledge_output_dir
    clusters_dir.mkdir(parents=True, exist_ok=True)
    clusters_path = clusters_dir / "clusters.json"
    clusters_path.write_text(json.dumps({"other-topic": ["L-999"]}), encoding="utf-8")

    with ExitStack() as stack:
        _enter_standard_patches(stack, trw_dir)
        result = execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            topic="nonexistent-slug",
            max_results=5,
            _rank_by_utility=lambda matches, *_a, **_kw: matches,
        )

    assert result.get("topic_filter_ignored") is True
    warning = result.get("topic_filter_warning", "")
    assert warning, "topic_filter_warning must be non-empty when slug is absent"
    assert "nonexistent-slug" in warning

    warning_events = [e for e in captured_structlog if e.get("event") == "topic_filter_ignored"]
    assert warning_events, "logger.warning('topic_filter_ignored') must be emitted"
    assert warning_events[0].get("topic") == "nonexistent-slug"
    assert warning_events[0].get("reason") == "slug_absent"


# ---------------------------------------------------------------------------
# Scenario 3: topic filter applied normally — no warning
# ---------------------------------------------------------------------------


def test_topic_filter_no_warning_when_applied(
    tmp_path: Path,
    captured_structlog: list[dict],  # type: ignore[type-arg]
) -> None:
    """When topic filter applies normally -> topic_filter_warning is empty, no log."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()

    entries = [_make_entry("L-001"), _make_entry("L-002")]
    clusters_dir = trw_dir / config.knowledge_output_dir
    clusters_dir.mkdir(parents=True, exist_ok=True)
    clusters_path = clusters_dir / "clusters.json"
    # slug present, L-001 is in-cluster, L-002 is not.
    clusters_path.write_text(json.dumps({"security": ["L-001"]}), encoding="utf-8")

    with ExitStack() as stack:
        _enter_standard_patches(stack, trw_dir, entries=entries)
        result = execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            topic="security",
            max_results=5,
            _rank_by_utility=lambda matches, *_a, **_kw: matches,
        )

    assert result.get("topic_filter_ignored") is False
    assert result.get("topic_filter_warning", "") == ""

    warning_events = [e for e in captured_structlog if e.get("event") == "topic_filter_ignored"]
    assert not warning_events, "No warning should be emitted when filter applied successfully"

    # Filter actually reduced results to only the in-cluster entry.
    returned_ids = [str(e.get("id", "")) for e in result.get("learnings", [])]
    assert "L-001" in returned_ids
    assert "L-002" not in returned_ids


# ---------------------------------------------------------------------------
# Scenario 4: no topic requested — topic_filter_* fields are omitted entirely
# ---------------------------------------------------------------------------


def test_no_topic_no_warning_field(
    tmp_path: Path,
    captured_structlog: list[dict],  # type: ignore[type-arg]
) -> None:
    """When topic is not requested -> topic_filter_* fields are omitted."""
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._recall_impl import execute_recall

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = get_config()

    with ExitStack() as stack:
        _enter_standard_patches(stack, trw_dir)
        result = execute_recall(
            query="auth",
            trw_dir=trw_dir,
            config=config,
            # topic not passed
            max_results=5,
            _rank_by_utility=lambda matches, *_a, **_kw: matches,
        )

    assert "topic_filter_ignored" not in result
    assert "topic_filter_warning" not in result

    warning_events = [e for e in captured_structlog if e.get("event") == "topic_filter_ignored"]
    assert not warning_events
