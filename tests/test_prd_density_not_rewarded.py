"""PRD prose density is diagnostic, not a readiness or score incentive."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import DimensionScore, ValidationResultV2
from trw_mcp.state.validation import generate_improvement_suggestions, prd_quality, validate_prd_quality_v2
from trw_mcp.state.validation._prd_quality_refresh import refresh_dynamic_prd_validation

from ._validation_v2_support import _FILLED_PRD


def _with_density(monkeypatch: pytest.MonkeyPatch, score: float, weight: float = 20.0) -> ValidationResultV2:
    monkeypatch.setattr(
        prd_quality,
        "score_content_density",
        lambda _content, _config: DimensionScore(name="content_density", score=score, max_score=weight),
    )
    config = TRWConfig.model_validate({"risk_scaling_enabled": False, "validation_density_weight": weight})
    return validate_prd_quality_v2(_FILLED_PRD, config=config, include_dynamic_checks=False)


@pytest.mark.parametrize("weight", [20.0, 50.0])
def test_density_cannot_move_score_tier_or_readiness(monkeypatch: pytest.MonkeyPatch, weight: float) -> None:
    low = _with_density(monkeypatch, 0.0, weight)
    high = _with_density(monkeypatch, weight, weight)

    assert low.total_score == high.total_score
    assert low.quality_tier == high.quality_tier
    assert low.verdict == high.verdict == "READY"
    assert [d.score for d in low.dimensions if d.name == "content_density"] == [0.0]
    assert [d.score for d in high.dimensions if d.name == "content_density"] == [weight]


def test_dynamic_refresh_cannot_reintroduce_density_into_score(monkeypatch: pytest.MonkeyPatch) -> None:
    low = _with_density(monkeypatch, 0.0)
    high = _with_density(monkeypatch, 20.0)
    config = TRWConfig.model_validate({"risk_scaling_enabled": False})

    low = refresh_dynamic_prd_validation(low, _FILLED_PRD, config=config)
    high = refresh_dynamic_prd_validation(high, _FILLED_PRD, config=config)

    assert low.total_score == high.total_score
    assert low.quality_tier == high.quality_tier
    assert low.verdict == high.verdict


def test_no_suggestion_rewards_padding_even_at_zero_density() -> None:
    dimensions = [
        DimensionScore(name="content_density", score=0.0, max_score=20.0),
        DimensionScore(name="implementation_readiness", score=5.0, max_score=25.0),
    ]

    suggestions = generate_improvement_suggestions(dimensions)

    assert [suggestion.dimension for suggestion in suggestions] == ["implementation_readiness"]
