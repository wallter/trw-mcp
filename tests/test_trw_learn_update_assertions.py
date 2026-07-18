"""Tests for trw_learn_update assertions support (PRD-CORE-086 FR12).

Verifies that assertions can be added, replaced, or removed via trw_learn_update.
"""

from __future__ import annotations

from pathlib import Path

import pytest


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

    def test_update_adds_assertions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When assertions are provided, they are validated and stored."""
        captured: dict[str, object] = {}

        def _update(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"learning_id": "L-test1", "status": "updated"}

        monkeypatch.setattr("trw_mcp.tools.learning.adapter_update", _update)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        update_fn(
            learning_id="L-test1",
            assertions=SAMPLE_ASSERTIONS,
        )

        saved = captured["assertions"]
        assert isinstance(saved, list) and len(saved) == 1


class TestUpdateReplacesAssertions:
    """FR12: Providing new assertions replaces existing ones."""

    def test_update_replaces_assertions(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Existing assertions are replaced with the new list."""
        captured: dict[str, object] = {}

        def _update(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"learning_id": "L-test2", "status": "updated"}

        monkeypatch.setattr("trw_mcp.tools.learning.adapter_update", _update)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        update_fn(
            learning_id="L-test2",
            assertions=REPLACEMENT_ASSERTIONS,
        )

        saved = captured["assertions"]
        assert isinstance(saved, list) and len(saved) == 2

    def test_update_replaces_assertions_in_yaml_backup(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """YAML backup assertions are kept in sync with the validated replacement list."""
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        trw_dir = tmp_path / ".trw"
        entry_path = trw_dir / "learnings" / "entries" / "L-test2.yaml"
        entry_path.parent.mkdir(parents=True)
        FileStateWriter().write_yaml(
            entry_path,
            {
                "id": "L-test2",
                "summary": "test summary",
                "detail": "test detail",
                "assertions": [{"type": "grep_present", "pattern": "old_pattern", "target": "**/*.py"}],
            },
        )

        monkeypatch.setattr(
            "trw_mcp.tools.learning.adapter_update",
            lambda trw_dir, **kw: {"learning_id": "L-test2", "status": "updated"},
        )
        monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: trw_dir)
        monkeypatch.setattr("trw_mcp.state.analytics.resync_learning_index", lambda *_args, **_kwargs: None)

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        update_fn(
            learning_id="L-test2",
            assertions=REPLACEMENT_ASSERTIONS,
        )

        updated = FileStateReader().read_yaml(entry_path)
        saved_assertions = updated["assertions"]
        assert len(saved_assertions) == 2
        assert saved_assertions[0]["type"] == "glob_exists"
        assert saved_assertions[1]["pattern"] == "TODO"


class TestDeleteAssertionsWithEmptyList:
    """FR12: assertions=[] removes all assertions."""

    def test_delete_assertions_with_empty_list(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Passing empty list clears all assertions."""
        captured: dict[str, object] = {}

        def _update(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"learning_id": "L-test3", "status": "updated"}

        monkeypatch.setattr("trw_mcp.tools.learning.adapter_update", _update)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        update_fn(
            learning_id="L-test3",
            assertions=[],
        )

        assert captured["assertions"] == []


class TestUpdateAssertionsNoneSkipsUpdate:
    """When assertions=None (not provided), no assertion update is done."""

    def test_none_assertions_skips_update(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When assertions is None, the adapter receives no replacement."""
        captured: dict[str, object] = {}

        def _update(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
            captured.update(kwargs)
            return {"learning_id": "L-test4", "status": "no_changes"}

        monkeypatch.setattr("trw_mcp.tools.learning.adapter_update", _update)
        monkeypatch.setattr(
            "trw_mcp.tools.learning.resolve_trw_dir",
            lambda: tmp_path / ".trw",
        )

        from tests.conftest import extract_tool_fn, make_test_server

        server = make_test_server("learning")
        update_fn = extract_tool_fn(server, "trw_learn_update")

        update_fn(learning_id="L-test4")

        assert captured["assertions"] is None
