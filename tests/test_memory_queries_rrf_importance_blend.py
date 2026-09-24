"""Remove the competing utility decision before final relevance-first ranking."""

from trw_mcp.models.config import TRWConfig


def test_retired_acquisition_blend_is_not_public_configuration():
    assert "hybrid_rrf_importance_alpha" not in TRWConfig.model_fields
    config = TRWConfig(hybrid_rrf_importance_alpha=0.0)
    assert "hybrid_rrf_importance_alpha" not in config.model_dump()
