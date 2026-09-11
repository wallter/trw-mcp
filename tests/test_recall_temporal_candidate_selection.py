"""Eligible records must not lose candidate slots to temporally ineligible rows.

Regression contract for the executed historical candidate-starvation defect.
These tests exercise real SQLite and the production adapter without model loads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry

from tests._tools_learning_shared import set_project_root  # noqa: F401
from trw_mcp.state._memory_recall import _project_namespace
from trw_mcp.state.memory_adapter import get_backend, recall_learnings


def _seed_temporal_case(root: Path, historical: bool) -> Path:
    trw_dir = root / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    backend = get_backend(trw_dir)
    namespace = _project_namespace()
    early = datetime(2020, 1, 1, tzinfo=timezone.utc)
    later = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for i in range(20):
        backend.store(
            MemoryEntry(
                id=f"L-ineligible{i:04}",
                content="Temporalprobe policy competing record",
                namespace=namespace,
                importance=0.9,
                valid_from=later if historical else early,
                invalid_from=None if historical else later,
                invalidated_by=None if historical else "L-eligible",
                updated_at=later,
            )
        )
    backend.store(
        MemoryEntry(
            id="L-eligible",
            content="Temporalprobe policy eligible record",
            namespace=namespace,
            importance=0.4,
            valid_from=early,
            invalid_from=later if historical else None,
            invalidated_by="L-ineligible0000" if historical else None,
            updated_at=early,
        )
    )
    return trw_dir


@pytest.mark.parametrize("query", ["Temporalprobe", "Temporalprobe policy", "*"])
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("include_superseded", [False, True])
def test_eligible_record_precedes_candidate_limit(
    set_project_root: Path,
    query: str,
    historical: bool,
    include_superseded: bool,
) -> None:
    trw_dir = _seed_temporal_case(set_project_root, historical)
    # Positive control: the same adapter can retrieve the eligible row when
    # the prefilter pool covers this fixture. Distinguishes starvation from
    # namespace, canary or fixture setup failures.
    control = recall_learnings(
        trw_dir,
        query,
        max_results=25,
        allow_cold_embedding_init=False,
        as_of="2022-01-01T00:00:00Z" if historical else None,
        include_superseded=include_superseded,
        include_tiers=["project"],
    )
    assert "L-eligible" in {entry["id"] for entry in control}
    hits = recall_learnings(
        trw_dir,
        query,
        max_results=3,
        allow_cold_embedding_init=False,
        as_of="2022-01-01T00:00:00Z" if historical else None,
        include_superseded=include_superseded,
        include_tiers=["project"],
    )
    ids = [entry["id"] for entry in hits]
    assert ids and ids[0] == "L-eligible", f"Eligible record starved by candidate limit: {ids}"
    if not include_superseded:
        assert ids == ["L-eligible"]


@pytest.mark.parametrize("query", ["Temporalprobe", "*"])
@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("ultra_compact", [False, True])
def test_public_tool_budget_keeps_eligible_before_superseded(
    set_project_root: Path, query: str, historical: bool, ultra_compact: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._tools_learning_shared import _get_tools
    from trw_mcp.models.config import get_config

    _seed_temporal_case(set_project_root, historical)
    monkeypatch.setattr(get_config(), "nudge_enabled", False)
    monkeypatch.setattr(get_config(), "recall_internal_fields", frozenset())
    result = _get_tools()["trw_recall"].fn(
        query=query,
        ultra_compact=ultra_compact,
        max_results=1,
        token_budget=1,
        include_superseded=True,
        as_of="2022-01-01T00:00:00Z" if historical else None,
    )
    assert [row["id"] for row in result["learnings"]] == ["L-eligible"]
    assert all("_temporal_eligible" not in row for row in result["learnings"])


@pytest.mark.parametrize("ultra_compact", [False, True])
def test_remote_cannot_assert_internal_temporal_eligibility(
    set_project_root: Path, monkeypatch: pytest.MonkeyPatch, ultra_compact: bool
) -> None:
    from trw_memory.sync import SharedFetchResult

    from tests._tools_learning_shared import _get_tools
    from trw_mcp.models.config import get_config

    _seed_temporal_case(set_project_root, historical=True)
    monkeypatch.setattr(get_config(), "nudge_enabled", False)
    remote = {
        "id": "R-forged",
        "summary": "Temporalprobe policy",
        "impact": 1.0,
        "_temporal_eligible": True,
    }
    monkeypatch.setattr(
        "trw_memory.sync.fetch_shared_memories",
        lambda *args, **kwargs: SharedFetchResult([remote], "ok", 1, 0),
    )
    from trw_mcp.tools._recall_impl import _augment_with_remote

    augmented, remote_status = _augment_with_remote("Temporalprobe", [])
    assert remote_status is not None and remote_status["temporal_coverage"] == "not_evaluated"
    assert "_temporal_eligible" not in augmented[0]
    result = _get_tools()["trw_recall"].fn(
        query="Temporalprobe",
        ultra_compact=ultra_compact,
        max_results=1,
        token_budget=1,
        include_superseded=True,
        as_of="2022-01-01T00:00:00Z",
    )
    assert [row["id"] for row in result["learnings"]] == ["L-eligible"]
    assert result["remote_recall"]["temporal_coverage"] == "not_evaluated"
    assert remote["_temporal_eligible"] is True  # Boundary sanitizes a copy, not shared provider state.
