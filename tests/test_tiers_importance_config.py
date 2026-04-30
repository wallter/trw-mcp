"""Tests for tier scoring and configuration behavior — PRD-CORE-043 FR05/FR06/FR07."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.tiers import TierManager, compute_importance_score

from tests._tiers_test_support import days_ago, make_entry, make_tier_manager, write_entry_yaml


class TestImportanceScoring:
    """FR05: compute_importance_score — Stanford Generative Agents formula."""

    def _entry(self, impact: float = 0.5, last_accessed_at: str | None = None) -> dict[str, object]:
        return {
            "id": "x",
            "summary": "test entry",
            "detail": "detail",
            "impact": impact,
            "last_accessed_at": last_accessed_at or datetime.now(tz=timezone.utc).date().isoformat(),
        }

    def test_score_in_range_zero_to_one(self) -> None:
        """Result is always in [0.0, 1.0]."""
        score = compute_importance_score(self._entry(impact=0.8), ["test"], config=TRWConfig())
        assert 0.0 <= score <= 1.0

    def test_score_range_with_very_old_entry(self) -> None:
        """Score stays in [0.0, 1.0] for entries accessed 500 days ago."""
        score = compute_importance_score(self._entry(last_accessed_at=days_ago(500)), ["x"], config=TRWConfig())
        assert 0.0 <= score <= 1.0

    def test_relevance_ordering(self) -> None:
        """Higher token overlap → higher score when recency and importance are equal."""
        cfg = TRWConfig(memory_score_w1=0.8, memory_score_w2=0.1, memory_score_w3=0.1)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        hi = {"id": "hi", "summary": "pytest fixture pattern", "detail": "", "impact": 0.5, "last_accessed_at": today}
        lo = {"id": "lo", "summary": "zzz unrelated text", "detail": "", "impact": 0.5, "last_accessed_at": today}
        tokens = ["pytest", "fixture", "pattern"]
        assert compute_importance_score(hi, tokens, config=cfg) > compute_importance_score(lo, tokens, config=cfg)

    def test_recency_ordering(self) -> None:
        """More recent entry scores higher when relevance and importance are equal."""
        cfg = TRWConfig(memory_score_w1=0.1, memory_score_w2=0.8, memory_score_w3=0.1)
        recent = self._entry(impact=0.5, last_accessed_at=days_ago(1))
        old = self._entry(impact=0.5, last_accessed_at=days_ago(200))
        assert compute_importance_score(recent, [], config=cfg) > compute_importance_score(old, [], config=cfg)

    def test_importance_ordering(self) -> None:
        """Higher impact → higher score when relevance and recency are equal."""
        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        assert compute_importance_score(
            self._entry(impact=0.9, last_accessed_at=today), [], config=cfg
        ) > compute_importance_score(self._entry(impact=0.1, last_accessed_at=today), [], config=cfg)

    def test_weight_normalization(self) -> None:
        """Non-unit weights are normalized; score stays in [0, 1]."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=1.0, memory_score_w3=1.0)
        score = compute_importance_score(self._entry(impact=0.9), ["anything"], config=cfg)
        assert 0.0 <= score <= 1.0

    def test_token_overlap_fallback_when_no_embedding(self) -> None:
        """Token overlap is used for relevance when no embeddings provided."""
        cfg = TRWConfig(memory_score_w1=0.9, memory_score_w2=0.05, memory_score_w3=0.05)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        entry = {
            "id": "x",
            "summary": "sqlalchemy orm pattern",
            "detail": "database access",
            "impact": 0.5,
            "last_accessed_at": today,
        }
        assert compute_importance_score(entry, ["sqlalchemy", "orm"], config=cfg) > compute_importance_score(
            entry, ["unrelated", "terms"], config=cfg
        )

    def test_zero_query_tokens_returns_zero_relevance(self) -> None:
        """Empty query tokens → zero relevance component."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=0.0, memory_score_w3=0.0)
        assert compute_importance_score(self._entry(), [], config=cfg) == 0.0

    def test_cosine_similarity_when_embeddings_provided(self) -> None:
        """Identical embeddings → cosine = 1.0 → score near w1."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=0.0, memory_score_w3=0.0)
        vec = [0.1] * 384
        score = compute_importance_score(self._entry(), [], query_embedding=vec, entry_embedding=vec, config=cfg)
        assert abs(score - 1.0) < 0.01

    def test_antiparallel_embeddings_clamp_to_zero(self) -> None:
        """Negative cosine similarity is clamped to 0.0."""
        cfg = TRWConfig(memory_score_w1=1.0, memory_score_w2=0.0, memory_score_w3=0.0)
        score = compute_importance_score(
            self._entry(),
            [],
            query_embedding=[1.0] + [0.0] * 383,
            entry_embedding=[-1.0] + [0.0] * 383,
            config=cfg,
        )
        assert score == 0.0

    def test_zero_embedding_does_not_raise(self) -> None:
        """query_embedding=[0.0]*N does not cause divide-by-zero."""
        zero_vec = [0.0] * 384
        score = compute_importance_score(
            self._entry(), [], query_embedding=zero_vec, entry_embedding=zero_vec, config=TRWConfig()
        )
        assert 0.0 <= score <= 1.0

    def test_impact_field_clamped_to_unit_interval(self) -> None:
        """Impact values outside [0,1] in raw dict are clamped."""
        cfg = TRWConfig(memory_score_w1=0.0, memory_score_w2=0.0, memory_score_w3=1.0)
        today = datetime.now(tz=timezone.utc).date().isoformat()
        over = {"id": "x", "summary": "", "detail": "", "impact": "2.5", "last_accessed_at": today}
        under = {"id": "y", "summary": "", "detail": "", "impact": "-0.5", "last_accessed_at": today}
        assert compute_importance_score(over, [], config=cfg) <= 1.0
        assert compute_importance_score(under, [], config=cfg) >= 0.0


class TestConfigAtCallTime:
    """FR06: sweep() reads config from get_config() at call time."""

    def test_sweep_reads_config_at_call_time(self, tmp_path: Path) -> None:
        """Injecting config into singleton before sweep applies new thresholds."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        write_entry_yaml(entries_dir, FileStateWriter(), "threshold-test", impact=0.1, last_accessed_at=days_ago(10))

        cfg = TRWConfig(memory_cold_threshold_days=5)
        _reset_config(cfg)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=FileStateWriter(), config=cfg)
        assert mgr.sweep().demoted >= 1

    def test_hot_put_reads_config_at_call_time(self, tmp_path: Path) -> None:
        """hot_put() respects capacity from injected config."""
        mgr = make_tier_manager(tmp_path)
        mgr._config = TRWConfig(memory_hot_max_entries=2)
        mgr.hot_put("e1", make_entry("e1"))
        mgr.hot_put("e2", make_entry("e2"))
        mgr.hot_put("e3", make_entry("e3"))
        assert mgr.hot_get("e1") is None
        assert mgr.hot_get("e3") is not None


class TestConfigFields:
    """FR07: TRWConfig CORE-043 fields."""

    def test_config_defaults(self) -> None:
        """All 7 CORE-043 config fields have correct defaults."""
        cfg = TRWConfig()
        assert cfg.memory_hot_max_entries == 50
        assert cfg.memory_hot_ttl_days == 7
        assert cfg.memory_cold_threshold_days == 90
        assert cfg.memory_retention_days == 365
        assert cfg.memory_score_w1 == pytest.approx(0.4)
        assert cfg.memory_score_w2 == pytest.approx(0.3)
        assert cfg.memory_score_w3 == pytest.approx(0.3)

    def test_config_score_weights_sum_to_one(self) -> None:
        """Default w1+w2+w3 == 1.0 within float tolerance."""
        cfg = TRWConfig()
        assert abs(cfg.memory_score_w1 + cfg.memory_score_w2 + cfg.memory_score_w3 - 1.0) < 1e-9

    def test_config_env_override_hot_max_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_HOT_MAX_ENTRIES env var overrides default."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_HOT_MAX_ENTRIES", "100")
        assert TRWConfig().memory_hot_max_entries == 100

    def test_config_env_override_hot_ttl_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_HOT_TTL_DAYS env var overrides default."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_HOT_TTL_DAYS", "14")
        assert TRWConfig().memory_hot_ttl_days == 14

    def test_config_env_override_retention_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_RETENTION_DAYS env var overrides default."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_RETENTION_DAYS", "730")
        assert TRWConfig().memory_retention_days == 730

    def test_config_env_override_score_weights(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Score weight env vars override defaults."""
        _reset_config()
        monkeypatch.setenv("TRW_MEMORY_SCORE_W1", "0.5")
        monkeypatch.setenv("TRW_MEMORY_SCORE_W2", "0.3")
        monkeypatch.setenv("TRW_MEMORY_SCORE_W3", "0.2")
        cfg = TRWConfig()
        assert cfg.memory_score_w1 == pytest.approx(0.5)
        assert cfg.memory_score_w2 == pytest.approx(0.3)
        assert cfg.memory_score_w3 == pytest.approx(0.2)
