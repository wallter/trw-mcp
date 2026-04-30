"""Delivery gate integration tests for instruction manifest parity."""

from __future__ import annotations

from pathlib import Path


class TestDeliveryGateR08Wiring:
    """The R-08 gate is wired into check_delivery_gates."""

    def test_gate_returns_warning_on_mismatch(self, tmp_path: Path) -> None:
        """_check_instruction_tool_parity_gate returns warning when tools mismatch."""
        from unittest.mock import MagicMock, patch

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        run_path = trw_dir / "runs" / "test-run"
        run_path.mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "core"
        mock_config.tool_exposure_list = []

        with patch(
            "trw_mcp.models.config.get_config",
            return_value=mock_config,
        ):
            from trw_mcp.tools._delivery_helpers import _check_instruction_tool_parity_gate

            result = _check_instruction_tool_parity_gate(run_path)
            assert result is not None
            assert "trw_build_check" in result

    def test_gate_returns_none_for_all_mode(self, tmp_path: Path) -> None:
        """R-08 gate is a no-op when mode is 'all'."""
        from unittest.mock import MagicMock, patch

        run_path = tmp_path / ".trw" / "runs" / "test"
        run_path.mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "all"

        with patch(
            "trw_mcp.models.config.get_config",
            return_value=mock_config,
        ):
            from trw_mcp.tools._delivery_helpers import _check_instruction_tool_parity_gate

            result = _check_instruction_tool_parity_gate(run_path)
            assert result is None


class TestDeliveryGateFullIntegration:
    """Test R-08 gate through check_delivery_gates."""

    def test_instruction_parity_wired_in_check_delivery_gates(self, tmp_path: Path) -> None:
        """check_delivery_gates includes instruction_parity_warning when mismatch exists."""
        from unittest.mock import MagicMock, patch

        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        run_path = trw_dir / "runs" / "test-run"
        (run_path / "meta").mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "core"
        mock_config.tool_exposure_list = []

        reader = FileStateReader()

        with patch("trw_mcp.models.config.get_config", return_value=mock_config):
            result = check_delivery_gates(run_path, reader)

        assert "instruction_parity_warning" in result
        assert "trw_build_check" in result["instruction_parity_warning"]

    def test_no_warning_when_all_mode(self, tmp_path: Path) -> None:
        """check_delivery_gates has no instruction_parity_warning in 'all' mode."""
        from unittest.mock import MagicMock, patch

        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import check_delivery_gates

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        agents = tmp_path / "AGENTS.md"
        agents.write_text("Use trw_build_check() for validation.\n")

        run_path = trw_dir / "runs" / "test-run"
        (run_path / "meta").mkdir(parents=True)

        mock_config = MagicMock()
        mock_config.effective_tool_exposure_mode = "all"

        reader = FileStateReader()

        with patch("trw_mcp.models.config.get_config", return_value=mock_config):
            result = check_delivery_gates(run_path, reader)

        assert "instruction_parity_warning" not in result
