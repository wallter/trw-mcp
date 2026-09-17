"""PRD-FIX-137 — meta-tune is forced OFF on non-Linux hosts instead of aborting boot.

SAFE-001's sandbox is Linux-only. A ``.trw/config.yaml`` written on a Linux box
with ``meta_tune_enabled: true`` used to raise ``MetaTuneBootValidationError``
from ``_build_middleware`` on a macOS checkout, killing ``--version``, ``doctor``
and the MCP server. The property SAFE-001 protects — no meta-tune without a
sandbox — is preserved by disabling meta-tune; aborting boot was collateral.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.meta_tune.errors import MetaTuneBootValidationError
from trw_mcp.models.config._loader import apply_platform_meta_tune_gate
from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig

pytestmark = pytest.mark.unit


def _enabled() -> TRWConfig:
    return TRWConfig(meta_tune=MetaTuneConfig(enabled=True))


class TestApplyPlatformMetaTuneGate:
    def test_non_linux_forces_both_flags_off_and_warns(self) -> None:
        """FR01: Darwin + enabled -> disabled on the nested flag AND the legacy mirror, with a WARNING."""
        cfg = _enabled()
        with patch("trw_mcp.models.config._loader.logger") as log:
            out = apply_platform_meta_tune_gate(cfg, system="Darwin")
        assert out is cfg
        assert cfg.meta_tune.enabled is False
        assert cfg.meta_tune_enabled is False
        log.warning.assert_called_once()
        assert log.warning.call_args.args[0] == "meta_tune_disabled_unsupported_platform"
        assert log.warning.call_args.kwargs["platform"] == "Darwin"

    def test_linux_is_left_untouched_so_the_fail_loud_validator_still_runs(self) -> None:
        """FR02: Linux keeps the enabled flag; boot_checks remains the authority there."""
        cfg = _enabled()
        with patch("trw_mcp.models.config._loader.logger") as log:
            apply_platform_meta_tune_gate(cfg, system="Linux")
        assert cfg.meta_tune.enabled is True
        assert cfg.meta_tune_enabled is True
        log.warning.assert_not_called()

    def test_already_disabled_is_a_silent_no_op_on_any_platform(self) -> None:
        cfg = TRWConfig(meta_tune=MetaTuneConfig(enabled=False))
        with patch("trw_mcp.models.config._loader.logger") as log:
            apply_platform_meta_tune_gate(cfg, system="Darwin")
        assert cfg.meta_tune.enabled is False
        log.warning.assert_not_called()

    def test_defaults_to_the_running_platform(self) -> None:
        cfg = _enabled()
        with patch("trw_mcp.models.config._loader.platform.system", return_value="Windows"):
            apply_platform_meta_tune_gate(cfg)
        assert cfg.meta_tune.enabled is False


class TestGateIsWiredIntoEveryConfigConstructionPath:
    def test_build_config_applies_the_gate(self) -> None:
        """FR01: ``_build_config`` (the get_config singleton source) returns the gated config."""
        from trw_mcp.models.config import _loader

        with (
            patch.object(_loader, "_build_config_unguarded", return_value=_enabled()),
            patch("trw_mcp.models.config._loader.platform.system", return_value="Darwin"),
        ):
            cfg = _loader._build_config()
        assert cfg.meta_tune.enabled is False

    def test_doctor_target_config_applies_the_gate(self, tmp_path: Path) -> None:
        """FR03: the doctor reports the config the server would RUN with."""
        from trw_mcp.server._subcommands_doctor import _resolve_target_config

        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("meta_tune_enabled: true\n", encoding="utf-8")
        with patch("trw_mcp.models.config._loader.platform.system", return_value="Darwin"):
            cfg = _resolve_target_config(tmp_path)
        assert cfg.meta_tune.enabled is False

    def test_gated_config_no_longer_trips_the_boot_validator(self) -> None:
        """End to end: the ungated config still aborts boot; the gated one boots through.

        Asserted on the OUTCOME both ways rather than on whether the validator
        was called — "assert_not_called" passes just as happily if the hook were
        deleted outright. The validator is stubbed with the exact error the real
        SAFE-001 check raises on a sandbox-less host, so the contrast holds on
        Linux too, where the real validator may well pass.
        """
        from trw_mcp.server._app import _run_meta_tune_boot_validation

        with patch(
            "trw_mcp.server._app.validate_meta_tune_defaults",
            side_effect=MetaTuneBootValidationError("SAFE-001 boot validation failed (1 issue(s))"),
        ):
            with pytest.raises(MetaTuneBootValidationError):
                _run_meta_tune_boot_validation(_enabled())

            gated = apply_platform_meta_tune_gate(_enabled(), system="Darwin")
            _run_meta_tune_boot_validation(gated)  # FR01: no raise — boot proceeds
            assert gated.meta_tune.enabled is False
