"""Tests for server/_app.py middleware helper extraction (FR03).

Verifies each _try_init_* helper is independently callable and returns
None on failure (fail-open).
"""

from __future__ import annotations

from unittest.mock import patch


class TestMiddlewareHelpers:
    """FR03: Verify extracted middleware init helpers exist and are fail-open."""

    def test_try_init_ceremony_returns_middleware(self) -> None:
        from trw_mcp.server._app import _try_init_ceremony

        result = _try_init_ceremony()
        # CeremonyMiddleware should be constructible
        assert result is not None

    def test_try_init_ceremony_returns_none_on_error(self) -> None:
        from trw_mcp.server._app import _try_init_ceremony

        with patch(
            "trw_mcp.server._app.CeremonyMiddleware",
            side_effect=RuntimeError("boom"),
        ):
            result = _try_init_ceremony()
            assert result is None

    def test_try_load_config_returns_config(self) -> None:
        from trw_mcp.server._app import _try_load_config

        result = _try_load_config()
        assert result is not None

    def test_try_load_config_returns_none_on_error(self) -> None:
        from trw_mcp.server._app import _try_load_config

        with patch(
            "trw_mcp.models.config.get_config",
            side_effect=RuntimeError("boom"),
        ):
            result = _try_load_config()
            assert result is None

    def test_try_init_observation_masking_returns_none_when_disabled(self) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.server._app import _try_init_observation_masking

        config = TRWConfig()
        # observation_masking defaults to True, so let's test with disabled
        config_copy = config.model_copy(update={"observation_masking": False})
        result = _try_init_observation_masking(config_copy)
        assert result is None

    def test_try_init_response_optimizer_returns_middleware(self) -> None:
        from trw_mcp.server._app import _try_init_response_optimizer

        result = _try_init_response_optimizer()
        assert result is not None

    def test_try_init_response_optimizer_returns_none_on_error(self) -> None:
        from trw_mcp.server._app import _try_init_response_optimizer

        with patch(
            "trw_mcp.middleware.response_optimizer.ResponseOptimizerMiddleware",
            side_effect=RuntimeError("boom"),
        ):
            result = _try_init_response_optimizer()
            assert result is None

    def test_build_middleware_still_works(self) -> None:
        """_build_middleware still returns a list after refactor."""
        from trw_mcp.server._app import _build_middleware

        result = _build_middleware()
        assert isinstance(result, list)

    def test_meta_tune_boot_validation_runs_when_enabled(self) -> None:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.models.config._sub_models import MetaTuneConfig
        from trw_mcp.server._app import _run_meta_tune_boot_validation

        cfg = TRWConfig(meta_tune=MetaTuneConfig(enabled=True))

        with patch("trw_mcp.server._app.validate_meta_tune_defaults") as validate:
            _run_meta_tune_boot_validation(cfg)

        validate.assert_called_once()


class TestParseVersion:
    """_parse_version converts version strings to comparable integer tuples."""

    def test_three_part_version(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert _parse_version("0.9.5") == (0, 9, 5)

    def test_two_part_version(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert _parse_version("1.2") == (1, 2)

    def test_single_part_version(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert _parse_version("3") == (3,)

    def test_comparison_below_minimum(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert _parse_version("0.9.4") < _parse_version("0.9.5")

    def test_comparison_at_minimum(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert not (_parse_version("0.9.5") < _parse_version("0.9.5"))

    def test_comparison_above_minimum(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert not (_parse_version("0.9.6") < _parse_version("0.9.5"))

    def test_unparseable_version_returns_zero_tuple(self) -> None:
        from trw_mcp.server._app import _parse_version

        assert _parse_version("not-a-version") == (0,)


class TestCheckMemoryVersion:
    """_check_memory_version emits warnings for outdated trw-memory."""

    def test_warns_when_version_below_minimum(self) -> None:
        import structlog.testing

        from trw_mcp.server._app import _check_memory_version

        with (
            patch("trw_mcp.server._app.importlib.metadata.version", return_value="0.9.4"),
            structlog.testing.capture_logs() as logs,
        ):
            _check_memory_version()

        warning_events = [log["event"] for log in logs if log.get("log_level") == "warning"]
        assert "trw_memory_version_below_minimum" in warning_events

    def test_no_warning_when_version_meets_minimum(self) -> None:
        import structlog.testing

        from trw_mcp.server._app import _check_memory_version

        with (
            patch("trw_mcp.server._app.importlib.metadata.version", return_value="0.9.5"),
            structlog.testing.capture_logs() as logs,
        ):
            _check_memory_version()

        warning_events = [log["event"] for log in logs if log.get("log_level") == "warning"]
        assert "trw_memory_version_below_minimum" not in warning_events

    def test_no_warning_when_version_above_minimum(self) -> None:
        import structlog.testing

        from trw_mcp.server._app import _check_memory_version

        with (
            patch("trw_mcp.server._app.importlib.metadata.version", return_value="1.0.0"),
            structlog.testing.capture_logs() as logs,
        ):
            _check_memory_version()

        warning_events = [log["event"] for log in logs if log.get("log_level") == "warning"]
        assert "trw_memory_version_below_minimum" not in warning_events

    def test_warns_when_package_not_found(self) -> None:
        import importlib.metadata

        import structlog.testing

        from trw_mcp.server._app import _check_memory_version

        with (
            patch(
                "trw_mcp.server._app.importlib.metadata.version",
                side_effect=importlib.metadata.PackageNotFoundError("trw-memory"),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            _check_memory_version()

        warning_events = [log["event"] for log in logs if log.get("log_level") == "warning"]
        assert "trw_memory_version_check_failed" in warning_events

    def test_fail_open_on_unexpected_error(self) -> None:
        import structlog.testing

        from trw_mcp.server._app import _check_memory_version

        with (
            patch(
                "trw_mcp.server._app.importlib.metadata.version",
                side_effect=OSError("permission denied"),
            ),
            structlog.testing.capture_logs() as logs,
        ):
            _check_memory_version()  # Must not raise

        # Only a debug log; no exception propagated
        assert not any(log.get("log_level") == "error" for log in logs)
