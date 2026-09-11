"""CD1–4: actual registered capture is independent of corpus score quotas."""

from unittest.mock import Mock

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("forced", [False, True])
@pytest.mark.parametrize("corpus_impact", [0.2, 0.95])
@pytest.mark.parametrize("impact", [0.95, -0.1, 1.1])
def test_registered_capture_preserves_clamped_impact_and_existing_rows(
    tmp_path, monkeypatch, forced, corpus_impact, impact, replay
):
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp import scoring
    from trw_mcp.state import memory_adapter
    from trw_mcp.tools import _learning_helpers, learning, telemetry

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    config = TRWConfig(
        dedup_enabled=False,
        embeddings_enabled=False,
        telemetry_enabled=False,
        impact_forced_distribution_enabled=forced,
    )
    monkeypatch.setattr(learning, "get_config", lambda: config)
    monkeypatch.setattr(telemetry, "get_config", lambda: config)
    trw_dir = tmp_path / ".trw"
    backend = memory_adapter.get_backend(trw_dir)
    ids = [f"L-existing-{i}" for i in range(10)]
    for key in ids:
        backend.store(
            MemoryEntry(id=key, namespace="default", content=f"Existing unrelated fact {key}", importance=corpus_impact)
        )
    before = {key: backend.get(key, namespace="default").model_dump(mode="json") for key in ids}
    forbidden = Mock(side_effect=AssertionError("capture invoked distribution-only work"))
    for module, name in [
        (learning, "list_active_learnings"),
        (memory_adapter, "list_active_learnings"),
        (scoring, "enforce_tier_distribution"),
        (_learning_helpers, "check_soft_cap"),
        (_learning_helpers, "enforce_distribution"),
    ]:
        monkeypatch.setattr(module, name, forbidden)
    if replay:
        from trw_mcp.state import learn_journal
        from trw_mcp.tools._learn_journal_wiring import make_sweep_replay

        payload = {
            "summary": "New isolated engineering discovery",
            "detail": "A concrete independent fact.",
            "impact": impact,
            "scope": "project",
        }
        learn_journal.journal_pending(trw_dir, "L-replayed", payload)
        sweep = make_sweep_replay(trw_dir, config)
        status = sweep.replay("L-replayed", payload)
        assert sweep.flush()
        assert not sweep.degraded()
        assert list(learn_journal.iter_pending(trw_dir)) == []
        result = {"status": status, "learning_id": "L-replayed"}
    else:
        server = FastMCP("capture-quota-test")
        learning.register_learning_tools(server)
        result = get_tools_sync(server)["trw_learn"].fn(
            summary="New isolated engineering discovery",
            detail="A concrete independent fact.",
            impact=impact,
            scope="project",
            metadata={"client_profile": "", "model_id": ""},
        )
    assert result["status"] == "recorded"
    entry = backend.get(result["learning_id"], namespace="default")
    assert entry.importance == max(0, min(1, impact))
    assert {key: backend.get(key, namespace="default").model_dump(mode="json") for key in ids} == before
    forbidden.assert_not_called()
    assert not result.get("distribution_warning")
