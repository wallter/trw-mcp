"""Tests for OTEL wrapper — PRD-INFRA-029-FR05.

Covers: conditional import, span emission with mock tracer, error handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestEmitToolSpan:
    """Tests for emit_tool_span function."""

    def test_noop_when_otel_disabled(self) -> None:
        """When otel_enabled=False, emit_tool_span returns immediately."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        with patch("trw_mcp.models.config.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(otel_enabled=False)
            # Should not raise
            emit_tool_span("test_tool", 42.0, {"key": "val"})

    def test_noop_when_otel_not_installed(self) -> None:
        """When opentelemetry is not installed, logs debug and returns."""
        import builtins
        import sys

        from trw_mcp.state.otel_wrapper import emit_tool_span

        # Remove opentelemetry from sys.modules if present, and block import
        saved_modules = {}
        for key in list(sys.modules):
            if key.startswith("opentelemetry"):
                saved_modules[key] = sys.modules.pop(key)

        real_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("opentelemetry"):
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)

        try:
            with (
                patch("trw_mcp.models.config.get_config") as mock_cfg,
                patch("builtins.__import__", side_effect=mock_import),
            ):
                mock_cfg.return_value = MagicMock(otel_enabled=True)
                # Should not raise even though opentelemetry is missing
                emit_tool_span("test_tool", 10.0)
        finally:
            # Restore modules
            sys.modules.update(saved_modules)

    def test_span_emitted_with_mock_tracer(self) -> None:
        """When otel_enabled=True and opentelemetry available, emits a span."""
        import sys

        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(
            return_value=mock_span
        )
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(
            return_value=False
        )

        mock_trace = MagicMock()
        mock_trace.get_tracer.return_value = mock_tracer

        mock_otel = MagicMock()
        mock_otel.trace = mock_trace

        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch.dict(sys.modules, {
                "opentelemetry": mock_otel,
                "opentelemetry.trace": mock_trace,
            }),
        ):
            mock_cfg.return_value = MagicMock(otel_enabled=True)

            # Re-import to pick up the mocked opentelemetry
            import importlib

            import trw_mcp.state.otel_wrapper as otel_mod

            importlib.reload(otel_mod)

            otel_mod.emit_tool_span("my_tool", 55.5, {"agent_id": "agent-1"})

            mock_trace.get_tracer.assert_called_once_with("trw-mcp")
            mock_tracer.start_as_current_span.assert_called_once_with("tool.my_tool")

    def test_exception_in_emit_does_not_propagate(self) -> None:
        """Exceptions inside emit_tool_span are caught silently."""
        from trw_mcp.state.otel_wrapper import emit_tool_span

        with patch("trw_mcp.models.config.get_config") as mock_cfg:
            mock_cfg.side_effect = RuntimeError("config boom")
            # Should NOT raise
            emit_tool_span("test_tool", 1.0)
