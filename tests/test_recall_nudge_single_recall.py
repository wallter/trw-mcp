"""The nudge riding on a ``trw_recall`` response never recalls on its own.

The response already carries the call's one recall, so a learning drawn by the
nudge would repeat it at the cost of a second recall over every namespace: the
rule PRD-CORE-294 FR02 set for ``trw_session_start``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._memory_fixtures import DaemonCheckout
from tests._tools_learning_shared import _get_tools
from trw_mcp.models.config import _reset_config
from trw_mcp.scoring._recall_context import RecallContext
from trw_mcp.state._daemon_store import DaemonMemoryStore


@pytest.mark.parametrize("messenger", ["standard", "contextual"])
def test_a_recall_response_runs_one_recall(
    monkeypatch: pytest.MonkeyPatch, daemon_checkout: DaemonCheckout, messenger: str
) -> None:
    trw_dir = daemon_checkout.trw_dir
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_NUDGE_MESSENGER", messenger)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    tools = _get_tools()
    tools["trw_learn"].fn(summary="Database pool exhausts under load", detail="Raise the pool size.", impact=0.9)
    config = trw_dir / "config.yaml"
    config.write_text(
        f"{config.read_text(encoding='utf-8')}nudge_enabled: true\nceremony_mode: full\nnudge_messenger: {messenger}\n",
        encoding="utf-8",
    )
    _reset_config()
    recalls: list[str] = []
    real_recall = DaemonMemoryStore.recall

    def _counted(store: DaemonMemoryStore, spec):  # type: ignore[no-untyped-def]
        recalls.append(spec.query)
        return real_recall(store, spec)

    # Both learning-drawing nudge paths forced open: the pool picks learnings
    # whenever its weight allows, a modified file anchors the contextual watch-out.
    touched = RecallContext(modified_files=["src/pool.py"])
    with (
        patch.object(DaemonMemoryStore, "recall", _counted),
        patch(
            "trw_mcp.state.ceremony_nudge._select_nudge_pool",
            side_effect=lambda _state, weights, *_a, **_k: "learnings" if weights.learnings else "workflow",
        ),
        patch("trw_mcp.state.recall_context.build_recall_context", return_value=touched),
    ):
        result = tools["trw_recall"].fn(query="database")

    assert result["learnings"], result
    assert recalls == ["database"], f"trw_recall ran {len(recalls)} recalls: {recalls}"
