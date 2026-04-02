"""Tests for P1 quality fixes: DRY helpers, __all__, type safety, TypedDict."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Fix 1: Shared phase auto-detection helper (DRY)
# ---------------------------------------------------------------------------


class TestDetectCurrentPhaseUsedInLearnImpl:
    """_learn_impl uses _paths.detect_current_phase instead of inline glob."""

    def test_learn_impl_calls_detect_current_phase(self, tmp_path: Path) -> None:
        """Verify that execute_learn delegates to detect_current_phase."""
        from trw_mcp.tools._learn_impl import execute_learn

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)

        with (
            patch("trw_mcp.state._paths.detect_current_phase", return_value="IMPLEMENT") as mock_detect,
            patch("trw_mcp.state.memory_adapter.store_learning"),
            patch("trw_mcp.state.analytics.generate_learning_id", return_value="L-test1234"),
            patch("trw_mcp.state.analytics.save_learning_entry", return_value=tmp_path / "entry.yaml"),
            patch("trw_mcp.state.analytics.update_analytics"),
            patch("trw_mcp.state.memory_adapter.list_active_learnings", return_value=[]),
            patch("trw_mcp.tools._learning_helpers.check_and_handle_dedup", return_value=None),
        ):
            from trw_mcp.models.config import TRWConfig

            cfg = TRWConfig()
            result = execute_learn(
                "test summary",
                "test detail",
                trw_dir,
                cfg,
            )
            # detect_current_phase should be called since phase_origin was not provided
            mock_detect.assert_called_once()
            assert result["status"] == "recorded"


class TestDetectCurrentPhaseUsedInRecallImpl:
    """_recall_impl uses _paths.detect_current_phase instead of inline glob."""

    def test_build_recall_context_delegates_to_detect_current_phase(
        self, tmp_path: Path
    ) -> None:
        """Verify build_recall_context uses detect_current_phase from _paths."""
        from trw_mcp.tools._recall_impl import build_recall_context

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        with (
            patch(
                "trw_mcp.state._paths.detect_current_phase", return_value="validate"
            ) as mock_detect,
            patch("subprocess.run") as mock_git,
        ):
            mock_git.return_value.returncode = 1  # No git output
            ctx = build_recall_context(trw_dir, "test query")

        mock_detect.assert_called_once()
        assert ctx is not None
        # detect_current_phase returns lowercase, build_recall_context uppercases
        assert ctx.current_phase == "VALIDATE"


# ---------------------------------------------------------------------------
# Fix 2: Shared nudge_line truncation helper
# ---------------------------------------------------------------------------


class TestTruncateNudgeLine:
    """truncate_nudge_line helper extracted from _learn_impl inline logic."""

    def test_short_text_unchanged(self) -> None:
        from trw_mcp.tools._learning_helpers import truncate_nudge_line

        assert truncate_nudge_line("Short text") == "Short text"

    def test_exactly_80_chars_unchanged(self) -> None:
        from trw_mcp.tools._learning_helpers import truncate_nudge_line

        text = "a" * 80
        assert truncate_nudge_line(text) == text

    def test_long_text_truncated_at_word_boundary(self) -> None:
        from trw_mcp.tools._learning_helpers import truncate_nudge_line

        # 90 chars with spaces
        text = "word " * 18  # 90 chars
        result = truncate_nudge_line(text)
        assert len(result) <= 81  # 80 + ellipsis char
        assert result.endswith("\u2026")

    def test_long_text_no_space_in_window(self) -> None:
        from trw_mcp.tools._learning_helpers import truncate_nudge_line

        text = "a" * 100  # No spaces at all
        result = truncate_nudge_line(text)
        assert len(result) == 80  # Hard cut at 80

    def test_custom_max_length(self) -> None:
        from trw_mcp.tools._learning_helpers import truncate_nudge_line

        text = "a" * 50
        result = truncate_nudge_line(text, max_length=30)
        assert len(result) == 30


# ---------------------------------------------------------------------------
# Fix 3: __all__ on anchor_generation.py
# ---------------------------------------------------------------------------


class TestAnchorGenerationAll:
    """anchor_generation exports __all__ with the expected symbols."""

    def test_all_exports(self) -> None:
        from trw_mcp.state import anchor_generation

        assert hasattr(anchor_generation, "__all__")
        assert "MARKER_PATTERN" in anchor_generation.__all__
        assert "extract_marker_ids" in anchor_generation.__all__
        assert "generate_anchors" in anchor_generation.__all__


# ---------------------------------------------------------------------------
# Fix 4: _memory_transforms type safety
# ---------------------------------------------------------------------------


class TestMemoryTransformsTypeSafety:
    """Anchor and Assertion are imported directly, not via try/except."""

    def test_anchor_import_is_unconditional(self) -> None:
        """The Anchor import should not be behind a try/except."""
        import inspect

        from trw_mcp.state import _memory_transforms

        source = inspect.getsource(_memory_transforms)
        # The old pattern had "Anchor = cast" as a fallback — should be gone
        assert "Anchor = cast" not in source

    def test_assertion_import_is_unconditional(self) -> None:
        """The Assertion import should not be behind a try/except."""
        import inspect

        from trw_mcp.state import _memory_transforms

        source = inspect.getsource(_memory_transforms)
        assert "Assertion = cast" not in source

    def test_anchor_objects_typed_as_anchor(self) -> None:
        """anchor_objects should be typed as list[Anchor], not list[Any]."""
        import inspect

        from trw_mcp.state import _memory_transforms

        source = inspect.getsource(_memory_transforms)
        assert "list[Anchor]" in source

    def test_assertion_objects_typed_as_assertion(self) -> None:
        """assertion_objects should be typed as list[Assertion], not list[Any]."""
        import inspect

        from trw_mcp.state import _memory_transforms

        source = inspect.getsource(_memory_transforms)
        assert "list[Assertion]" in source


# ---------------------------------------------------------------------------
# Fix 5: AnchorDict TypedDict
# ---------------------------------------------------------------------------


class TestAnchorDictTypedDict:
    """generate_anchors returns list[AnchorDict] with typed structure."""

    def test_anchor_dict_type_exists(self) -> None:
        from trw_mcp.state.anchor_generation import AnchorDict

        # TypedDict should have the expected keys
        annotations = AnchorDict.__annotations__
        assert "file" in annotations
        assert "symbol_name" in annotations
        assert "symbol_type" in annotations
        assert "signature" in annotations
        assert "line_range" in annotations

    def test_generate_anchors_returns_anchor_dicts(self, tmp_path: Path) -> None:
        from trw_mcp.state.anchor_generation import generate_anchors

        f = tmp_path / "mod.py"
        f.write_text("def hello(): pass\n")
        result = generate_anchors([str(f)], {})
        assert len(result) == 1
        # Each item matches AnchorDict shape
        anchor = result[0]
        assert isinstance(anchor["file"], str)
        assert isinstance(anchor["symbol_name"], str)
        assert isinstance(anchor["symbol_type"], str)
        assert isinstance(anchor["signature"], str)
        assert isinstance(anchor["line_range"], tuple)


# ---------------------------------------------------------------------------
# Fix 6: PurePosixPath at module level
# ---------------------------------------------------------------------------


class TestPurePosixPathModuleLevel:
    """PurePosixPath is imported at module level in scoring/_recall.py."""

    def test_pure_posix_path_at_module_level(self) -> None:
        import ast

        source_path = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "scoring" / "_recall.py"
        tree = ast.parse(source_path.read_text())

        # Check for module-level import of PurePosixPath
        found_module_level = False
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
                for alias in node.names:
                    if alias.name == "PurePosixPath":
                        found_module_level = True
                        break

        assert found_module_level, "PurePosixPath should be imported at module level"

    def test_no_function_level_posix_import(self) -> None:
        """PurePosixPath should NOT be imported inside function bodies."""
        import ast

        source_path = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "scoring" / "_recall.py"
        tree = ast.parse(source_path.read_text())

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if isinstance(child, ast.ImportFrom) and child.module == "pathlib":
                        for alias in child.names:
                            assert alias.name != "PurePosixPath", (
                                f"PurePosixPath imported inside function {node.name}"
                            )
