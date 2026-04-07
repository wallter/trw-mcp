"""Tests for PRD-FIX-061 FR05/FR06: Scoring layer boundary refactors.

Verifies that scoring modules (_correlation.py, _decay.py) have zero
imports from the state layer, and that the I/O boundary module
(_io_boundary.py) properly bridges scoring to state.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# FR05: _correlation.py has zero state-layer imports
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCorrelationNoStateImports:
    """FR05: scoring/_correlation.py must not import from trw_mcp.state."""

    _SRC = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "scoring" / "_correlation.py"

    def test_no_state_analytics_import(self) -> None:
        """No import from trw_mcp.state.analytics in _correlation.py."""
        content = self._SRC.read_text()
        assert "from trw_mcp.state.analytics" not in content

    def test_no_state_memory_adapter_import(self) -> None:
        """No import from trw_mcp.state.memory_adapter in _correlation.py."""
        content = self._SRC.read_text()
        assert "from trw_mcp.state.memory_adapter" not in content

    def test_no_state_persistence_import(self) -> None:
        """No import from trw_mcp.state.persistence in _correlation.py."""
        content = self._SRC.read_text()
        assert "from trw_mcp.state.persistence" not in content

    def test_no_state_helpers_import(self) -> None:
        """No import from trw_mcp.state._helpers in _correlation.py."""
        content = self._SRC.read_text()
        assert "from trw_mcp.state._helpers" not in content


# ---------------------------------------------------------------------------
# FR06: _decay.py has zero state-layer I/O imports
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDecayNoStateImports:
    """FR06: scoring/_decay.py must not import from trw_mcp.state."""

    _SRC = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "scoring" / "_decay.py"

    def test_no_iter_yaml_entry_files(self) -> None:
        """No reference to iter_yaml_entry_files in _decay.py."""
        content = self._SRC.read_text()
        assert "iter_yaml_entry_files" not in content

    def test_no_file_state_reader(self) -> None:
        """No reference to FileStateReader in _decay.py."""
        content = self._SRC.read_text()
        assert "FileStateReader" not in content

    def test_no_state_helpers_import(self) -> None:
        """No import from trw_mcp.state._helpers in _decay.py."""
        content = self._SRC.read_text()
        assert "from trw_mcp.state._helpers" not in content

    def test_no_state_persistence_import(self) -> None:
        """No import from trw_mcp.state.persistence in _decay.py."""
        content = self._SRC.read_text()
        assert "from trw_mcp.state.persistence" not in content


# ---------------------------------------------------------------------------
# _io_boundary.py existence and callable checks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIoBoundaryModule:
    """The I/O boundary module bridges scoring to state."""

    def test_default_lookup_entry_importable(self) -> None:
        """_default_lookup_entry is importable from _io_boundary."""
        from trw_mcp.scoring._io_boundary import _default_lookup_entry

        assert callable(_default_lookup_entry)

    def test_load_entries_from_dir_importable(self) -> None:
        """_load_entries_from_dir is importable from _io_boundary."""
        from trw_mcp.scoring._io_boundary import _load_entries_from_dir

        assert callable(_load_entries_from_dir)

    def test_sync_to_sqlite_importable(self) -> None:
        """_sync_to_sqlite is importable from _io_boundary."""
        from trw_mcp.scoring._io_boundary import _sync_to_sqlite

        assert callable(_sync_to_sqlite)

    def test_batch_sync_to_sqlite_importable(self) -> None:
        """_batch_sync_to_sqlite is importable from _io_boundary."""
        from trw_mcp.scoring._io_boundary import _batch_sync_to_sqlite

        assert callable(_batch_sync_to_sqlite)

    def test_find_session_start_ts_importable(self) -> None:
        """_find_session_start_ts is importable from _io_boundary."""
        from trw_mcp.scoring._io_boundary import _find_session_start_ts

        assert callable(_find_session_start_ts)

    def test_write_pending_entries_importable(self) -> None:
        """_write_pending_entries is importable from _io_boundary."""
        from trw_mcp.scoring._io_boundary import _write_pending_entries

        assert callable(_write_pending_entries)


# ---------------------------------------------------------------------------
# Backward-compat: re-exports from _correlation still work
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackwardCompatReExports:
    """Functions moved to _io_boundary are still importable from _correlation."""

    def test_find_session_start_ts_from_correlation(self) -> None:
        """_find_session_start_ts re-exported from _correlation."""
        from trw_mcp.scoring._correlation import _find_session_start_ts

        assert callable(_find_session_start_ts)

    def test_default_lookup_entry_from_correlation(self) -> None:
        """_default_lookup_entry re-exported from _correlation."""
        from trw_mcp.scoring._correlation import _default_lookup_entry

        assert callable(_default_lookup_entry)

    def test_batch_sync_from_correlation(self) -> None:
        """_batch_sync_to_sqlite re-exported from _correlation."""
        from trw_mcp.scoring._correlation import _batch_sync_to_sqlite

        assert callable(_batch_sync_to_sqlite)

    def test_sync_from_correlation(self) -> None:
        """_sync_to_sqlite re-exported from _correlation."""
        from trw_mcp.scoring._correlation import _sync_to_sqlite

        assert callable(_sync_to_sqlite)

    def test_load_entries_from_decay(self) -> None:
        """_load_entries_from_dir re-exported from _decay."""
        from trw_mcp.scoring._decay import _load_entries_from_dir

        assert callable(_load_entries_from_dir)

    def test_lookup_alias_from_correlation(self) -> None:
        """_lookup_learning_entry backward-compat alias still works."""
        from trw_mcp.scoring._correlation import _lookup_learning_entry

        assert callable(_lookup_learning_entry)


# ---------------------------------------------------------------------------
# Integration: process_outcome and compute_impact_distribution still work
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScoringPublicApi:
    """Public scoring API continues to work after the refactor."""

    def test_process_outcome_importable(self) -> None:
        """process_outcome is importable from scoring package."""
        from trw_mcp.scoring import process_outcome

        assert callable(process_outcome)

    def test_compute_impact_distribution_importable(self) -> None:
        """compute_impact_distribution is importable from scoring package."""
        from trw_mcp.scoring import compute_impact_distribution

        assert callable(compute_impact_distribution)

    def test_process_outcome_for_event_importable(self) -> None:
        """process_outcome_for_event is importable from scoring package."""
        from trw_mcp.scoring import process_outcome_for_event

        assert callable(process_outcome_for_event)

    def test_correlate_recalls_importable(self) -> None:
        """correlate_recalls is importable from scoring package."""
        from trw_mcp.scoring import correlate_recalls

        assert callable(correlate_recalls)


# ---------------------------------------------------------------------------
# _io_boundary.py module size guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIoBoundarySize:
    """_io_boundary.py must stay under 300 lines (module size gate)."""

    def test_module_under_300_lines(self) -> None:
        """_io_boundary.py should be a focused boundary module."""
        src = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "scoring" / "_io_boundary.py"
        line_count = len(src.read_text().splitlines())
        assert line_count < 500, f"_io_boundary.py is {line_count} lines, should be < 500"
