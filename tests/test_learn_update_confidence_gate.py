"""PRD-CORE-244-FR02 on the UPDATE surface — a promotion needs a basis too.

FR02 put the substantiation rule in ``validate_entry_payload``, the single
chokepoint every *store* surface passes through. ``trw_learn_update`` is not a
store: it edits an existing row through ``backend.update(namespace="default")`` and never re-enters
the pipeline. So ``trw_learn_update(learning_id, fields={"confidence":
"verified"})`` promoted an evidence-less entry to ``verified`` — the exact
defect FR02 closes, reopened one surface over. An independent review found it.

Every test here drives the REAL registered ``trw_learn_update`` tool function.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from trw_memory.models.memory import Anchor, Assertion, AssertionType, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend


def _learn_update_fn() -> object:
    from fastmcp import FastMCP

    from trw_mcp.tools.learning import register_learning_tools

    server = FastMCP("test")
    register_learning_tools(server)

    async def _get() -> object:
        for t in await server.list_tools():
            if t.name == "trw_learn_update":
                return t.fn
        raise KeyError("trw_learn_update not found")

    return asyncio.run(_get())


@pytest.fixture()
def trw_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trw = tmp_path / ".trw"
    (trw / "learnings" / "entries").mkdir(parents=True)
    (trw / "memory").mkdir(parents=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    from trw_mcp.state.memory_adapter import reset_backend

    reset_backend()
    return tmp_path


def _seed(trw_dir: Path, entry_id: str, **overrides: object) -> SQLiteBackend:
    from trw_mcp.state.memory_adapter import get_backend

    backend = get_backend(trw_dir)
    backend.store(
        MemoryEntry(id=entry_id, content=f"content {entry_id}", namespace="default").model_copy(update=overrides)
    )
    return backend


class TestConfidencePromotionNeedsABasis:
    def test_promotion_without_evidence_is_refused(self, trw_project: Path) -> None:
        """The bypass. Fails against the pre-fix tree, where status was 'updated'."""
        trw_dir = trw_project / ".trw"
        backend = _seed(trw_dir, "L-bare")
        fn = _learn_update_fn()

        result = fn(learning_id="L-bare", fields={"confidence": "verified"})

        assert result["status"] == "invalid"
        assert result["reason"] == "unsubstantiated_verified"
        stored = backend.get("L-bare", namespace="default")
        assert stored is not None
        assert stored.confidence == "unverified", "the refused promotion must not have landed"

    def test_promotion_with_existing_evidence_proceeds(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        backend = _seed(trw_dir, "L-cited", evidence=["measured against memory.db on 2026-09-03"])
        fn = _learn_update_fn()

        result = fn(learning_id="L-cited", fields={"confidence": "verified"})

        assert result["status"] == "updated"
        stored = backend.get("L-cited", namespace="default")
        assert stored is not None
        assert stored.confidence == "verified"

    def test_existing_anchors_substantiate_the_promotion(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        _seed(trw_dir, "L-anchored", anchors=[Anchor(symbol_name="update_learning", file="state/_memory_update.py")])
        fn = _learn_update_fn()

        assert fn(learning_id="L-anchored", fields={"confidence": "verified"})["status"] == "updated"

    def test_assertions_supplied_by_the_SAME_call_substantiate_it(self, trw_project: Path) -> None:
        """The gate reads the POST-update entry, not the pre-update one.

        An author adding the assertion that proves the claim, in the same call
        that raises the confidence, is the good path — refusing it would push
        people to make two calls or to stop asserting at all.
        """
        trw_dir = trw_project / ".trw"
        backend = _seed(trw_dir, "L-together")
        fn = _learn_update_fn()

        result = fn(
            learning_id="L-together",
            fields={
                "confidence": "verified",
                "assertions": [{"type": "glob_exists", "target": "pyproject.toml"}],
            },
        )

        assert result["status"] == "updated"
        stored = backend.get("L-together", namespace="default")
        assert stored is not None
        assert stored.confidence == "verified"
        assert len(stored.assertions) == 1

    @pytest.mark.parametrize("confidence", ["unverified", "low", "medium", "high"])
    def test_only_the_verified_claim_needs_a_basis(self, trw_project: Path, confidence: str) -> None:
        """Demotion, and every non-verified level, are unaffected."""
        trw_dir = trw_project / ".trw"
        _seed(trw_dir, f"L-{confidence}")
        fn = _learn_update_fn()

        assert fn(learning_id=f"L-{confidence}", fields={"confidence": confidence})["status"] == "updated"

    def test_a_verified_entry_can_still_be_demoted(self, trw_project: Path) -> None:
        """Removing a claim never needs substantiation."""
        trw_dir = trw_project / ".trw"
        backend = _seed(trw_dir, "L-demote", confidence="verified", evidence=["a citation"])
        fn = _learn_update_fn()

        assert fn(learning_id="L-demote", fields={"confidence": "unverified"})["status"] == "updated"
        stored = backend.get("L-demote", namespace="default")
        assert stored is not None
        assert stored.confidence == "unverified"

    def test_refusal_does_not_apply_the_other_fields_in_the_call(self, trw_project: Path) -> None:
        """The whole update is refused, so a rejected promotion cannot half-land."""
        trw_dir = trw_project / ".trw"
        backend = _seed(trw_dir, "L-partial")
        fn = _learn_update_fn()

        result = fn(learning_id="L-partial", fields={"confidence": "verified", "task_type": "coding"})

        assert result["status"] == "invalid"
        stored = backend.get("L-partial", namespace="default")
        assert stored is not None
        assert stored.task_type == ""


class TestOtherConfidenceSurfacesReachTheGate:
    """The rule must hold for every surface that can set ``confidence``."""

    def test_store_surface_still_refuses(self, trw_project: Path) -> None:
        from trw_memory.exceptions import SchemaValidationError
        from trw_memory.models.config import MemoryConfig
        from trw_memory.security.write_gate import guarded_store

        from trw_mcp.state.memory_adapter import get_backend

        trw_dir = trw_project / ".trw"
        backend = get_backend(trw_dir)
        config = MemoryConfig(storage_path=str(trw_dir / "memory"))
        entry = MemoryEntry(id="L-store", content="claim", namespace="default").model_copy(
            update={"confidence": "verified"}
        )

        with pytest.raises(SchemaValidationError) as excinfo:
            guarded_store(backend, entry, config=config)
        assert excinfo.value.reason == "unsubstantiated_verified"

    def test_the_two_surfaces_share_one_implementation(self) -> None:
        """A copied rule is free to drift; assert the update path imports the store one."""
        import inspect

        from trw_mcp.state import _memory_update

        source = inspect.getsource(_memory_update._reject_unverifiable_promotion)
        assert "from trw_memory.security.poisoning import reject_unsubstantiated_verified" in source

    def test_offline_cli_learn_routes_through_the_store_gate(self) -> None:
        """``trw-mcp local learn`` calls execute_learn, which stores via guarded_store."""
        import inspect

        from trw_mcp.services import orchestration_service

        source = inspect.getsource(orchestration_service.write_local_learning)
        assert "execute_learn" in source
        # It never passes a confidence, so it cannot promote anything.
        assert "confidence" not in source

    def test_sync_pull_routes_through_the_store_gate(self) -> None:
        """A pulled peer entry passes prepare_entry_for_store, which runs the gate."""
        import inspect

        from trw_mcp.sync import pull

        assert "prepare_entry_for_store" in inspect.getsource(pull)


def _assertion(target: str) -> Assertion:
    return Assertion(type=AssertionType.GLOB_EXISTS, target=target)
