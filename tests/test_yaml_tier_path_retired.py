"""UF-PRD-23: trw-mcp's YAML-file tier path (``TierManager`` and the deliver ``tier_sweep`` step) is retired.

It ran over the legacy ``.trw/learnings/entries`` YAML files. The product reads the daemon store, which the sweep never
touched, so ``assign_impact_tiers`` labelled files nothing reads and PRD-FIX-052's "100% of entries tiered" was never true
of the store. The step, its census rows and its three settings are removed together; a config that still sets one of the
settings gets the retired-key warning once.
"""

from __future__ import annotations

import importlib.util

import pytest

from trw_mcp.models.config import TRWConfig, _retired_keys

_KNOBS = ("memory_hot_ttl_days", "memory_cold_threshold_days", "memory_retention_days")


@pytest.fixture(autouse=True)
def _reset_warned() -> object:
    _retired_keys._reset_warned_keys()
    yield
    _retired_keys._reset_warned_keys()


def test_the_yaml_tier_modules_are_gone() -> None:
    for module in ("tiers", "_tier_sweep", "_tier_scoring"):
        assert importlib.util.find_spec(f"trw_mcp.state.{module}") is None, module
    assert importlib.util.find_spec("trw_mcp.state._tier_routing") is not None, "namespace routing is live and stays"


def test_the_deliver_roster_and_its_effect_census_lose_the_step_together() -> None:
    from trw_mcp.tools._deferred_delivery import DEFERRED_STEP_COUNT, DEFERRED_STEPS
    from trw_mcp.tools._delivery_effect_registry import DELIVERY_EFFECT_REGISTRY
    from trw_mcp.tools._delivery_tracer import DEFERRED_STEP_EFFECT_IDS

    assert "tier_sweep" not in DEFERRED_STEPS
    assert DEFERRED_STEP_COUNT == len(DEFERRED_STEPS)
    assert "tier_sweep" not in DEFERRED_STEP_EFFECT_IDS
    assert "D03" not in DELIVERY_EFFECT_REGISTRY
    assert set(DEFERRED_STEP_EFFECT_IDS) <= set(DEFERRED_STEPS), "every journaled step is still on the roster"


@pytest.mark.parametrize("knob", _KNOBS)
def test_each_setting_is_removed_and_named_as_retired(knob: str) -> None:
    assert knob not in TRWConfig.model_fields
    assert knob in _retired_keys.retired_config_keys()


def test_a_config_that_still_sets_them_warns_once_per_key(capsys: pytest.CaptureFixture[str]) -> None:
    raw = dict.fromkeys(_KNOBS, 7)

    first = _retired_keys.warn_unrecognised_config_keys(raw, set())
    second = _retired_keys.warn_unrecognised_config_keys(raw, set())

    assert sorted(first) == sorted(_KNOBS)
    assert second == []
    err = capsys.readouterr().err
    assert all(knob in err for knob in _KNOBS)


def test_the_learning_model_no_longer_carries_an_impact_tier_label() -> None:
    from trw_mcp.models.learning import LearningEntry

    assert "impact_tier" not in LearningEntry.model_fields
