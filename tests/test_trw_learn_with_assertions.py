"""Tests for assertion threading through trw_learn pipeline (PRD-CORE-086 FR05).

Verifies that assertions flow from trw_learn parameters through LearningParams,
store_learning, and the delegated store into MemoryEntry.assertions.

``TestStoredEntryHasAssertions`` only cares that assertions round-trip through
``store_learning`` -> ``store.put`` -> ``MemoryEntry.assertions``, so it routes
through ``fake_memory_store``; ``TestStoreLearningThreadsAssertions`` reads them
back from the memory daemon.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.tools._learning_helpers import LearningParams


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


SAMPLE_ASSERTIONS = [
    {"type": "grep_present", "pattern": "def my_func", "target": "**/*.py"},
    {"type": "glob_exists", "pattern": "", "target": "src/main.py"},
]


class TestLearningParamsAssertionsField:
    """FR05: LearningParams accepts optional assertions."""

    def test_learning_params_with_assertions(self) -> None:
        """LearningParams stores assertions when provided."""
        params = LearningParams(
            summary="test",
            detail="detail",
            learning_id="L-test1",
            tags=["test"],
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="test",
            assertions=SAMPLE_ASSERTIONS,
        )
        assert params.assertions == SAMPLE_ASSERTIONS

    def test_learning_params_assertions_default_none(self) -> None:
        """LearningParams.assertions defaults to None."""
        params = LearningParams(
            summary="test",
            detail="detail",
            learning_id="L-test2",
            tags=[],
            evidence=[],
            impact=0.5,
            source_type="agent",
            source_identity="test",
        )
        assert params.assertions is None


class TestStoredEntryHasAssertions:
    """FR05: assertions given to a learning land on the stored entry.

    PRD-CORE-251 FR03 retargeted these from ``_learning_to_memory_entry`` (the
    retired hand builder) to the delegated write path. The dict-to-``Assertion``
    marshalling is covered in tests/test_store_arguments.py; these prove the
    objects reach SQLite.
    """

    def _store(self, tmp_path: Path, fake_memory_store: FakeMemoryStore, entry_id: str, **kwargs: Any) -> Any:
        from trw_mcp.state.memory_adapter import store_learning

        trw_dir = tmp_path / ".trw"
        (trw_dir / "memory").mkdir(parents=True, exist_ok=True)
        result = store_learning(trw_dir, entry_id, "test summary", "test detail", **kwargs)
        assert result["status"] == "recorded", result
        return fake_memory_store.get(entry_id)

    def test_memory_entry_has_assertions(self, tmp_path: Path, fake_memory_store: FakeMemoryStore) -> None:
        entry = self._store(tmp_path, fake_memory_store, "L-test3", assertions=SAMPLE_ASSERTIONS)

        assert entry is not None
        assert len(entry.assertions) == 2
        assert entry.assertions[0].type == "grep_present"
        assert entry.assertions[0].pattern == "def my_func"
        assert entry.assertions[1].type == "glob_exists"
        assert entry.assertions[1].target == "src/main.py"

    def test_memory_entry_no_assertions(self, tmp_path: Path, fake_memory_store: FakeMemoryStore) -> None:
        entry = self._store(tmp_path, fake_memory_store, "L-test4")

        assert entry is not None
        assert entry.assertions == []

    def test_memory_entry_assertions_none(self, tmp_path: Path, fake_memory_store: FakeMemoryStore) -> None:
        entry = self._store(tmp_path, fake_memory_store, "L-test5", assertions=None)

        assert entry is not None
        assert entry.assertions == []


class TestStoreLearningThreadsAssertions:
    """FR05: store_learning threads assertions into the store the daemon writes."""

    def test_store_learning_with_assertions(self, daemon_checkout: DaemonCheckout) -> None:
        """The assertions land on the daemon's row, validated, in the order given."""
        from trw_mcp.state import memory_adapter

        result = memory_adapter.store_learning(
            daemon_checkout.trw_dir,
            "L-test-assert",
            "test summary",
            "test detail",
            assertions=SAMPLE_ASSERTIONS,
        )

        assert result["status"] == "recorded"
        row = asyncio.run(daemon_checkout.client.get("L-test-assert", daemon_checkout.namespace))
        stored = row["entry"]["assertions"]
        assert [(a["type"], a["target"]) for a in stored] == [(raw["type"], raw["target"]) for raw in SAMPLE_ASSERTIONS]


class TestTrwLearnStoresAssertions:
    """FR05: trw_learn tool threads assertions to adapter_store."""

    def test_trw_learn_stores_assertions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The trw_learn tool passes assertions through to adapter_store."""
        from tests.conftest import extract_tool_fn, make_test_server

        stored_kwargs: dict[str, Any] = {}

        def mock_store(trw_dir: Any, **kwargs: Any) -> dict[str, Any]:
            stored_kwargs.update(kwargs)
            return {
                "learning_id": kwargs.get("learning_id", "L-mock"),
                "path": "sqlite://L-mock",
                "status": "recorded",
                "distribution_warning": "",
            }

        monkeypatch.setattr("trw_mcp.tools.learning.adapter_store", mock_store)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )
        # Mock dedup check to pass through
        monkeypatch.setattr(
            "trw_mcp.tools.learning.check_and_handle_dedup",
            lambda *a, **kw: None,
        )
        # Mock save_learning_entry
        monkeypatch.setattr(
            "trw_mcp.tools.learning.save_learning_entry",
            lambda *a, **kw: tmp_path / "entry.yaml",
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.update_analytics",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.list_active_learnings",
            lambda *a, **kw: [],
        )

        (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)

        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")

        result = learn_fn(
            summary="test assertion learning",
            detail="detail text",
            metadata={"assertions": SAMPLE_ASSERTIONS},
        )
        assert result["status"] == "recorded"
        assert stored_kwargs.get("assertions") == SAMPLE_ASSERTIONS
