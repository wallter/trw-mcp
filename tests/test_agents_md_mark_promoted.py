"""PRD-CORE-165 FR-05: surfaced AGENTS.md learnings are marked promoted.

Behavioral tests for ``_inject_learnings_to_agents`` — verify that
``mark_promoted`` is called for each learning actually injected into the
``## Key Learnings`` bullet list, that filtered (empty-summary) entries are
NOT promoted, and that a ``mark_promoted`` failure cannot abort injection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig


def _recall_stub(*_args: object, **_kwargs: object) -> list[dict[str, object]]:
    """Return three learnings; the middle one has an empty summary."""
    return [
        {"id": "learn-1", "summary": "First real learning"},
        {"id": "learn-2", "summary": "   "},  # sanitizes to empty -> filtered out
        {"id": "learn-3", "summary": "Third real learning"},
    ]


def test_mark_promoted_called_for_each_injected_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each entry that lands in the bullet list is marked promoted; filtered one is not."""
    from trw_mcp.state.claude_md import _agents_md

    calls: list[str] = []

    def _fake_mark_promoted(_trw_dir: Path, learning_id: str) -> None:
        calls.append(learning_id)

    monkeypatch.setattr(_agents_md, "mark_promoted", _fake_mark_promoted)

    config = TRWConfig()
    result = _agents_md._inject_learnings_to_agents(
        Path("/tmp/does-not-matter"),
        config,
        recall_fn=_recall_stub,
    )

    # Both real summaries are injected; the whitespace-only one is filtered.
    assert "First real learning" in result
    assert "Third real learning" in result
    # Only the two injected entries are promoted — never the filtered one.
    assert calls == ["learn-1", "learn-3"]
    assert "learn-2" not in calls


def test_mark_promoted_skipped_for_missing_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """An injected entry with no id is added to the list but not promoted."""
    from trw_mcp.state.claude_md import _agents_md

    calls: list[str] = []
    monkeypatch.setattr(
        _agents_md,
        "mark_promoted",
        lambda _d, lid: calls.append(lid),
    )

    def _recall_no_id(*_a: object, **_k: object) -> list[dict[str, object]]:
        return [
            {"id": "", "summary": "Has summary but no id"},
            {"id": "learn-x", "summary": "Has both"},
        ]

    config = TRWConfig()
    result = _agents_md._inject_learnings_to_agents(
        Path("/tmp/x"),
        config,
        recall_fn=_recall_no_id,
    )

    assert "Has summary but no id" in result
    assert "Has both" in result
    # The id-less entry is injected but not promoted.
    assert calls == ["learn-x"]


def test_injection_survives_mark_promoted_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A raising mark_promoted is swallowed; injection still returns its string."""
    from trw_mcp.state.claude_md import _agents_md

    def _boom(_trw_dir: Path, _learning_id: str) -> None:
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(_agents_md, "mark_promoted", _boom)

    config = TRWConfig()
    result = _agents_md._inject_learnings_to_agents(
        Path("/tmp/y"),
        config,
        recall_fn=_recall_stub,
    )

    # Despite every mark_promoted raising, the bullet list is still produced.
    assert "## Key Learnings" in result
    assert "First real learning" in result
    assert "Third real learning" in result
