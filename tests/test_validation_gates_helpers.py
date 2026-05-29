"""Coverage tests for validation helper functions."""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation import (
    _coerce_v1_failures,
    derive_risk_level,
    get_risk_scaled_config,
)


class TestCoerceV1Failures:
    """_coerce_v1_failures converts raw input to ValidationFailure list."""

    def test_not_a_list_returns_empty(self) -> None:
        assert _coerce_v1_failures(None) == []
        assert _coerce_v1_failures("string") == []
        assert _coerce_v1_failures(42) == []
        assert _coerce_v1_failures({}) == []

    def test_list_of_validation_failures_passthrough(self) -> None:
        vf = ValidationFailure(
            field="test",
            rule="test_rule",
            message="msg",
            severity="warning",
        )
        result = _coerce_v1_failures([vf])
        assert len(result) == 1
        assert result[0] is vf

    def test_list_of_dicts_converted(self) -> None:
        raw: list[object] = [
            {
                "field": "some_field",
                "rule": "some_rule",
                "message": "a message",
                "severity": "error",
            }
        ]
        result = _coerce_v1_failures(raw)
        assert len(result) == 1
        assert result[0].field == "some_field"
        assert result[0].rule == "some_rule"
        assert result[0].message == "a message"
        assert result[0].severity == "error"

    def test_mixed_list_handles_both_types(self) -> None:
        vf = ValidationFailure(
            field="f1",
            rule="r1",
            message="m1",
            severity="warning",
        )
        raw_dict: dict[str, object] = {
            "field": "f2",
            "rule": "r2",
            "message": "m2",
            "severity": "info",
        }
        result = _coerce_v1_failures([vf, raw_dict])
        assert len(result) == 2
        assert result[0] is vf
        assert result[1].field == "f2"

    def test_dict_with_missing_keys_uses_defaults(self) -> None:
        raw: list[object] = [{}]
        result = _coerce_v1_failures(raw)
        assert len(result) == 1
        assert result[0].field == ""
        assert result[0].rule == ""
        assert result[0].severity == "warning"

    def test_empty_list_returns_empty(self) -> None:
        assert _coerce_v1_failures([]) == []


class TestDeriveRiskLevel:
    """derive_risk_level returns explicit_risk when it overrides priority."""

    def test_explicit_risk_critical_overrides_priority(self) -> None:
        result = derive_risk_level("P3", explicit_risk="critical")
        assert result == "critical"

    def test_explicit_risk_low_overrides_p0(self) -> None:
        result = derive_risk_level("P0", explicit_risk="low")
        assert result == "low"

    def test_invalid_explicit_risk_falls_back_to_priority(self) -> None:
        result = derive_risk_level("P0", explicit_risk="unknown_risk")
        assert result == "critical"

    def test_none_explicit_risk_uses_priority(self) -> None:
        assert derive_risk_level("P0") == "critical"
        assert derive_risk_level("P1") == "high"
        assert derive_risk_level("P2") == "medium"
        assert derive_risk_level("P3") == "low"

    def test_unknown_priority_defaults_to_medium(self) -> None:
        result = derive_risk_level("P99")
        assert result == "medium"


class TestGetRiskScaledConfig:
    """get_risk_scaled_config returns original config for invalid risk levels."""

    def test_invalid_risk_level_returns_original_config(self) -> None:
        config = TRWConfig()
        result = get_risk_scaled_config(config, "invalid_level")
        assert result is config

    def test_medium_risk_returns_original_config(self) -> None:
        config = TRWConfig()
        result = get_risk_scaled_config(config, "medium")
        assert result is config

    def test_risk_scaling_disabled_returns_original(self) -> None:
        config = TRWConfig(risk_scaling_enabled=False)
        result = get_risk_scaled_config(config, "critical")
        assert result is config

    def test_critical_risk_scales_thresholds(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = get_risk_scaled_config(config, "critical")
        assert result is not config
        assert result.validation_review_threshold == 92.0

    def test_high_risk_scales_thresholds(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = get_risk_scaled_config(config, "high")
        assert result is not config
        assert result.validation_review_threshold == 88.0

    def test_low_risk_scales_thresholds(self) -> None:
        config = TRWConfig(risk_scaling_enabled=True)
        result = get_risk_scaled_config(config, "low")
        assert result is not config
        assert result.validation_review_threshold == 75.0
