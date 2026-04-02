"""Tests for anchor wiring in the trw_learn() flow — PRD-CORE-111."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_mcp.models.config import TRWConfig


def _make_config() -> TRWConfig:
    return TRWConfig()


class TestLearnCreatesAnchors:
    """Verify anchor generation is wired into execute_learn."""

    def test_learn_creates_anchors_when_git_diff_returns_py_files(
        self, tmp_path: Path
    ) -> None:
        """When git diff returns modified .py files, anchors are generated and passed to store."""
        from trw_mcp.tools._learn_impl import execute_learn

        # Create a real .py file that anchor_generation can read
        py_file = tmp_path / "mod.py"
        py_file.write_text("def my_func(): pass\n")

        captured_anchors: list[Any] = []

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            captured_anchors.extend(kwargs.get("anchors") or [])
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Mock git diff to return the py_file path
        git_stdout = str(py_file.relative_to(trw_dir.parent)) + "\n"

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = git_stdout

        with patch("trw_mcp.tools._learn_impl.subprocess.run", return_value=mock_result):
            # Run the actual flow with real file
            result = execute_learn(
                summary="Test anchor wiring",
                detail="Verifying anchors flow through execute_learn",
                trw_dir=trw_dir,
                config=_make_config(),
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        # Anchors should have been captured
        assert len(captured_anchors) >= 1
        assert captured_anchors[0]["symbol_name"] == "my_func"

    def test_learn_no_modified_files_empty_anchors(self, tmp_path: Path) -> None:
        """When git diff returns nothing, anchors passed to store are empty."""
        from trw_mcp.tools._learn_impl import execute_learn

        captured_anchors: list[Any] = [None]  # sentinel

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            captured_anchors[0] = kwargs.get("anchors", [])
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""  # no modified files

        with patch("trw_mcp.tools._learn_impl.subprocess.run", return_value=mock_result):
            execute_learn(
                summary="No anchors test",
                detail="Git returns empty diff",
                trw_dir=trw_dir,
                config=_make_config(),
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test2",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert captured_anchors[0] == []

    def test_anchors_not_in_tool_params(self) -> None:
        """execute_learn does not accept anchors from the caller — they are auto-generated."""
        import inspect

        from trw_mcp.tools._learn_impl import execute_learn

        sig = inspect.signature(execute_learn)
        # anchors must NOT be a parameter of execute_learn
        assert "anchors" not in sig.parameters, (
            "anchors should be auto-generated, not a caller-supplied parameter"
        )

    def test_learn_git_failure_still_records(self, tmp_path: Path) -> None:
        """If git subprocess fails, anchor generation is skipped but learn still records."""
        from trw_mcp.tools._learn_impl import execute_learn

        stored: list[dict[str, object]] = []

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            stored.append({"anchors": kwargs.get("anchors", [])})
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Simulate git failure
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("trw_mcp.tools._learn_impl.subprocess.run", return_value=mock_result):
            result = execute_learn(
                summary="Git fails gracefully",
                detail="Anchor failure must not block learning",
                trw_dir=trw_dir,
                config=_make_config(),
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test3",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        # Anchors should be empty (git failed, returncode != 0)
        assert stored[0]["anchors"] == []

    def test_learn_subprocess_exception_still_records(self, tmp_path: Path) -> None:
        """If subprocess.run raises, anchor generation is skipped but learn still records."""
        from trw_mcp.tools._learn_impl import execute_learn

        stored: list[dict[str, object]] = []

        def fake_store(
            _trw_dir: Path,
            *,
            learning_id: str = "",
            **kwargs: Any,
        ) -> dict[str, object]:
            stored.append({"anchors": kwargs.get("anchors", [])})
            return {"learning_id": learning_id, "path": "sqlite://x", "status": "recorded", "distribution_warning": ""}

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with patch(
            "trw_mcp.tools._learn_impl.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["git"], 5),
        ):
            result = execute_learn(
                summary="Subprocess timeout",
                detail="Timeout must not block learning",
                trw_dir=trw_dir,
                config=_make_config(),
                _adapter_store=fake_store,
                _generate_learning_id=lambda: "L-test4",
                _save_learning_entry=lambda *a, **kw: tmp_path / "entry.yaml",
                _update_analytics=lambda *a, **kw: None,
                _list_active_learnings=lambda *a, **kw: [],
                _check_and_handle_dedup=lambda *a, **kw: None,
            )

        assert result.get("status") == "recorded"
        assert stored[0]["anchors"] == []


class TestAnchorYamlRoundTrip:
    """Verify anchors survive through store_learning / _memory_transforms."""

    def test_learning_to_memory_entry_with_anchors(self, tmp_path: Path) -> None:
        """Anchors in dict form are converted to Anchor objects in MemoryEntry."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        # Use relative path (Anchor model requires it)
        anchor_dict: dict[str, object] = {
            "file": "src/mod.py",
            "symbol_name": "my_func",
            "symbol_type": "function",
            "signature": "def my_func(): pass",
            "line_range": (1, 1),
        }

        entry = _learning_to_memory_entry(
            "L-test",
            "test summary",
            "test detail",
            anchors=[anchor_dict],
        )

        assert len(entry.anchors) == 1
        assert entry.anchors[0].symbol_name == "my_func"
        assert entry.anchors[0].symbol_type == "function"
        assert entry.anchors[0].file == "src/mod.py"

    def test_learning_to_memory_entry_absolute_path_converted(self, tmp_path: Path) -> None:
        """Absolute file paths in anchor dicts are converted to relative paths."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        # Create a real file so we can get a meaningful relative path
        anchor_dict: dict[str, object] = {
            "file": "/home/user/project/src/mod.py",
            "symbol_name": "abs_func",
            "symbol_type": "function",
            "signature": "def abs_func(): pass",
            "line_range": (1, 1),
        }

        entry = _learning_to_memory_entry(
            "L-abs",
            "absolute path test",
            "test detail",
            anchors=[anchor_dict],
        )

        # Entry should have an anchor with a relative path
        assert len(entry.anchors) == 1
        assert not entry.anchors[0].file.startswith("/")

    def test_learning_to_memory_entry_no_anchors(self) -> None:
        """When no anchors provided, entry.anchors is empty."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        entry = _learning_to_memory_entry(
            "L-noanchor",
            "no anchor summary",
            "detail",
        )
        assert entry.anchors == []

    def test_learning_to_memory_entry_malformed_anchor_skipped(self) -> None:
        """Malformed anchor dicts are silently skipped."""
        from trw_mcp.state._memory_transforms import _learning_to_memory_entry

        anchor_dict: dict[str, object] = {
            # Missing required 'symbol_name' key — Anchor model will reject
            "file": "src/mod.py",
            "symbol_name": "",  # empty — fails Anchor's min_length=1
            "symbol_type": "function",
            "signature": "",
        }

        entry = _learning_to_memory_entry(
            "L-bad",
            "malformed anchor",
            "detail",
            anchors=[anchor_dict],
        )
        # Malformed anchor is skipped — no crash
        assert entry.anchors == []
