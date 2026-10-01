"""UF-MCP-07: PRD-CORE-034's forced impact distribution is gone, and a config that still sets its knobs is told once.

``check_soft_cap`` and ``enforce_distribution`` lost their only caller in the 2026-09-15 squash, so the four knobs
gated nothing. Both functions, the trw-memory algorithm behind them and the knobs are removed.
"""

from __future__ import annotations

import importlib.util

import pytest

from trw_mcp.models.config import TRWConfig, _retired_keys

_KNOBS = (
    "impact_forced_distribution_enabled",
    "impact_high_threshold_pct",
    "impact_tier_critical_cap",
    "impact_tier_high_cap",
)


@pytest.fixture(autouse=True)
def _reset_warned() -> object:
    _retired_keys._reset_warned_keys()
    yield
    _retired_keys._reset_warned_keys()


def test_the_functions_the_knobs_governed_are_gone() -> None:
    from trw_mcp.tools import _learning_helpers

    assert not hasattr(_learning_helpers, "check_soft_cap")
    assert not hasattr(_learning_helpers, "enforce_distribution")
    assert importlib.util.find_spec("trw_mcp.scoring._distribution") is None
    from trw_memory.lifecycle import scoring

    assert not hasattr(scoring, "enforce_tier_distribution")


@pytest.mark.parametrize("knob", _KNOBS)
def test_each_knob_is_removed_from_the_config_and_named_as_retired(knob: str) -> None:
    assert knob not in TRWConfig.model_fields
    assert knob in _retired_keys.retired_config_keys()


def test_a_config_that_still_sets_the_knobs_warns_once_per_key_and_never_raises(
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = dict.fromkeys(_KNOBS, 0.5)

    first = _retired_keys.warn_unrecognised_config_keys(raw, set())
    second = _retired_keys.warn_unrecognised_config_keys(raw, set())

    assert sorted(first) == sorted(_KNOBS)
    assert second == [], "a key warns at most once per process"
    err = capsys.readouterr().err
    assert all(knob in err for knob in _KNOBS)
