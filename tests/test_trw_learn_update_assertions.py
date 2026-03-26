"""Tests for trw_learn_update assertions support (PRD-CORE-086 FR12).

Verifies that assertions can be added, replaced, or removed via trw_learn_update.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from trw_mcp.models.config import TRWConfig


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


SAMPLE_ASSERTIONS = [
    {"type": "grep_present", "pattern": "class MyClass", "target": "**/*.py"},
]

REPLACEMENT_ASSERTIONS = [
    {"type": "glob_exists", "pattern": "", "target": "src/new_module.py"},
    {"type": "grep_absent", "pattern": "TODO", "target": "**/*.py"},
]


class TestUpdateAddsAssertions:
    """FR12: trw_learn_update adds assertions to an entry."""

    def test_update_adds_assertions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When assertions are provided, they are validated and stored."""
        from trw_memory.models.memory import MemoryEntry, MemoryStatus

        mock_backend = MagicMock()
        mock_entry = MemoryEntry(
            id="L-test1",
            content="test summary",
            detail="test detail",
            assertions=[],
        )
        mock_backend.get.return_value = mock_entry

        monkeypatch.setattr(
            "trw_mcp.tools.learning.get_backend",
            lambda _: mock_backend,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.adapter_update",
            lambda trw_dir, **kw: {"learning_id": "L-test1", "status": "no_changes"},
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        result = update_fn(
            learning_id="L-test1",
            assertions=SAMPLE_ASSERTIONS,
        )

        # Backend.update should have been called with serialized assertions
        mock_backend.update.assert_called_once()
        update_call_kwargs = mock_backend.update.call_args
        assert update_call_kwargs[0][0] == "L-test1"
        assert "assertions" in update_call_kwargs[1]
        # The assertions passed should have 1 item
        assert len(update_call_kwargs[1]["assertions"]) == 1


class TestUpdateReplacesAssertions:
    """FR12: Providing new assertions replaces existing ones."""

    def test_update_replaces_assertions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing assertions are replaced with the new list."""
        from trw_memory.models.memory import Assertion, MemoryEntry

        existing_assertion = Assertion(
            type="grep_present", pattern="old_pattern", target="**/*.py"
        )
        mock_backend = MagicMock()
        mock_entry = MemoryEntry(
            id="L-test2",
            content="test summary",
            detail="test detail",
            assertions=[existing_assertion],
        )
        mock_backend.get.return_value = mock_entry

        monkeypatch.setattr(
            "trw_mcp.tools.learning.get_backend",
            lambda _: mock_backend,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.adapter_update",
            lambda trw_dir, **kw: {"learning_id": "L-test2", "status": "no_changes"},
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        result = update_fn(
            learning_id="L-test2",
            assertions=REPLACEMENT_ASSERTIONS,
        )

        # Backend should receive the replacement assertions
        update_call_kwargs = mock_backend.update.call_args
        assert len(update_call_kwargs[1]["assertions"]) == 2


class TestDeleteAssertionsWithEmptyList:
    """FR12: assertions=[] removes all assertions."""

    def test_delete_assertions_with_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing empty list clears all assertions."""
        from trw_memory.models.memory import Assertion, MemoryEntry

        existing_assertion = Assertion(
            type="grep_present", pattern="some_pattern", target="**/*.py"
        )
        mock_backend = MagicMock()
        mock_entry = MemoryEntry(
            id="L-test3",
            content="test summary",
            detail="test detail",
            assertions=[existing_assertion],
        )
        mock_backend.get.return_value = mock_entry

        monkeypatch.setattr(
            "trw_mcp.tools.learning.get_backend",
            lambda _: mock_backend,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.adapter_update",
            lambda trw_dir, **kw: {"learning_id": "L-test3", "status": "no_changes"},
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        result = update_fn(
            learning_id="L-test3",
            assertions=[],
        )

        # Backend.update should be called with empty assertions list
        update_call_kwargs = mock_backend.update.call_args
        assert update_call_kwargs[1]["assertions"] == []


class TestUpdateAssertionsNoneSkipsUpdate:
    """When assertions=None (not provided), no assertion update is done."""

    def test_none_assertions_skips_update(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When assertions is None, backend is not called for assertion update."""
        mock_backend = MagicMock()

        monkeypatch.setattr(
            "trw_mcp.tools.learning.get_backend",
            lambda _: mock_backend,
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.adapter_update",
            lambda trw_dir, **kw: {"learning_id": "L-test4", "status": "no_changes"},
        )
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        result = update_fn(
            learning_id="L-test4",
        )

        # Backend should NOT have been called for assertion update (only adapter_update)
        mock_backend.get.assert_not_called()
        mock_backend.update.assert_not_called()
