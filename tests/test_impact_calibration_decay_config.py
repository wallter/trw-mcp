"""Impact decay and config-default calibration tests."""

from __future__ import annotations


class TestOutcomeWindowDefault:
    """Verify the outcome correlation window default.

    History: PRD-FIX-070-FR05 lowered 480→60; PRD-FIX-088 FR04 then lowered
    60→7 (a 60-min window matched ~2800 receipts on active sessions, with one
    build_check correlating 2823 entries in 91s; 7 minutes covers a typical
    work cycle while keeping correlation set sizes O(100)). The default is
    pinned in ``models/config/_fields_scoring.py``.
    """

    def test_outcome_window_default_is_7(self) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        assert cfg.learning_outcome_correlation_window_minutes == 7


class TestConfigFields:
    """Verify new config fields exist with correct defaults."""

    def test_impact_high_threshold_pct_default(self) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        assert cfg.impact_high_threshold_pct == 20.0
