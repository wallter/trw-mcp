"""PRD-CORE-231-NFR03: every new tunable is typed with explicit bounds.

No magic numbers in the consuming code — an out-of-range value must be rejected
at ``TRWConfig`` construction time, not silently accepted and acted on.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._field_admission import build_field_admissions

_NEW_FIELDS = (
    "maintain_verify_batch_limit",
    "hint_sidecar_refresh_enabled",
    "hint_sidecar_refresh_file_cap",
    "wiring_gate_mode_overrides",
)


def test_defaults_match_the_prd() -> None:
    """The shipped defaults are the ones the PRD specifies."""
    config = TRWConfig()

    assert config.hint_sidecar_refresh_file_cap == 20
    assert config.hint_sidecar_refresh_enabled is True
    assert config.maintain_verify_batch_limit == 1000
    assert config.wiring_gate_mode_overrides == {}


def test_hint_delivery_fields_are_gone() -> None:
    """PRD-FIX-125-FR04: the two reader-less hint-delivery knobs are deleted outright.

    They were admitted with a ``consumer=`` naming a telemetry aggregation over
    ``.trw/telemetry/channel-events.jsonl`` that never existed, and their
    admission grandfather expired 2026-08-31. Per the operator rule on dormant
    knobs there is no alias and no default-preserving stub: an intentional
    breaking change to a surface with zero readers.
    """
    config = TRWConfig()

    with pytest.raises(AttributeError):
        _ = config.hint_delivery_rate_min
    with pytest.raises(AttributeError):
        _ = config.hint_delivery_measurement_window_days

    assert "hint_delivery_rate_min" not in build_field_admissions()
    assert "hint_delivery_measurement_window_days" not in build_field_admissions()


def test_hint_sidecar_refresh_file_cap_bounds() -> None:
    """The per-commit fan-out cap must stay bounded on both ends."""
    assert TRWConfig(hint_sidecar_refresh_file_cap=1).hint_sidecar_refresh_file_cap == 1
    assert TRWConfig(hint_sidecar_refresh_file_cap=500).hint_sidecar_refresh_file_cap == 500

    with pytest.raises(ValidationError):
        TRWConfig(hint_sidecar_refresh_file_cap=0)
    with pytest.raises(ValidationError):
        TRWConfig(hint_sidecar_refresh_file_cap=501)


def test_wiring_gate_mode_overrides_rejects_unknown_modes() -> None:
    """Only the two real gate modes are assignable — a typo must not read as 'warn'."""
    assert TRWConfig(wiring_gate_mode_overrides={"CORE": "block"}).wiring_gate_mode_overrides == {"CORE": "block"}

    with pytest.raises(ValidationError):
        TRWConfig(wiring_gate_mode_overrides={"CORE": "blocK-typo"})
    with pytest.raises(ValidationError):
        TRWConfig(wiring_gate_mode_overrides={"CORE": "off"})


def test_every_new_field_carries_admission_metadata() -> None:
    """PRD-CORE-218-FR05: a new public field must pay the admission budget."""
    admissions = build_field_admissions()

    for name in _NEW_FIELDS:
        assert name in admissions, f"{name} lacks a ConfigAdmission entry"
        assert admissions[name].owner, f"{name} admission has no owner"
        assert admissions[name].consumer, f"{name} admission has no consumer"
