"""Tests for assertion threading through trw_learn pipeline (PRD-CORE-086 FR05).

Verifies that assertions flow from trw_learn parameters through LearningParams,
store_learning, and _learning_to_memory_entry into MemoryEntry.assertions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from trw_mcp.models.config import TRWConfig
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


class TestMemoryEntryHasAssertions:
    """FR05: _learning_to_memory_entry creates MemoryEntry with assertions."""

    def test_memory_entry_has_assertions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Assertions are validated and attached to the MemoryEntry."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        entry = _learning_to_memory_entry(
            "L-test3",
            "test summary",
            "test detail",
            assertions=SAMPLE_ASSERTIONS,
        )
        assert len(entry.assertions) == 2
        assert entry.assertions[0].type == "grep_present"
        assert entry.assertions[0].pattern == "def my_func"
        assert entry.assertions[1].type == "glob_exists"
        assert entry.assertions[1].target == "src/main.py"

    def test_memory_entry_no_assertions(self) -> None:
        """Without assertions, MemoryEntry.assertions is empty list."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        entry = _learning_to_memory_entry(
            "L-test4",
            "test summary",
            "test detail",
        )
        assert entry.assertions == []

    def test_memory_entry_assertions_none(self) -> None:
        """Passing None produces empty assertion list."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        entry = _learning_to_memory_entry(
            "L-test5",
            "test summary",
            "test detail",
            assertions=None,
        )
        assert entry.assertions == []


class TestStoreLearningThreadsAssertions:
    """FR05: store_learning threads assertions to _learning_to_memory_entry."""

    def test_store_learning_with_assertions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """store_learning passes assertions through to the memory entry."""
        from trw_mcp.state import memory_adapter

        # Track what gets stored
        stored_entry: dict[str, Any] = {}
        original_to_memory = memory_adapter._learning_to_memory_entry

        def tracking_to_memory(*args: Any, **kwargs: Any) -> Any:
            stored_entry["kwargs"] = kwargs
            return original_to_memory(*args, **kwargs)

        monkeypatch.setattr(
            "trw_mcp.state.memory_adapter._learning_to_memory_entry",
            tracking_to_memory,
        )

        # Mock the backend and embedding
        mock_backend = MagicMock()
        monkeypatch.setattr(
            "trw_mcp.state.memory_adapter.get_backend",
            lambda _: mock_backend,
        )
        monkeypatch.setattr(
            "trw_mcp.state.memory_adapter._embed_and_store",
            lambda *a, **kw: None,
        )
        # Mock infer_topic_tags (local import in store_learning)
        monkeypatch.setattr(
            "trw_mcp.state.analytics.infer_topic_tags",
            lambda *a, **kw: [],
        )

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)

        result = memory_adapter.store_learning(
            trw_dir,
            "L-test-assert",
            "test summary",
            "test detail",
            assertions=SAMPLE_ASSERTIONS,
        )
        assert result["status"] == "recorded"
        assert stored_entry["kwargs"]["assertions"] == SAMPLE_ASSERTIONS


class TestTrwLearnStoresAssertions:
    """FR05: trw_learn tool threads assertions to adapter_store."""

    def test_trw_learn_stores_assertions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        monkeypatch.setattr(
            "trw_mcp.tools.learning.adapter_store", mock_store
        )
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
            assertions=SAMPLE_ASSERTIONS,
        )
        assert result["status"] == "recorded"
        assert stored_kwargs.get("assertions") == SAMPLE_ASSERTIONS
