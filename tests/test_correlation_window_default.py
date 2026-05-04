"""PRD-FIX-088 FR04: outcome-correlation window default + env override.

The default value of ``learning_outcome_correlation_window_minutes`` is
locked at 7 minutes (PRD-FIX-088 §FR04). The pre-fix live deployment was
running with the value at 60 minutes, which produced ~2823 candidate
recall receipts per call — the proximate cause of the 91 s inline
correlation latency.

These tests pin BOTH halves of the contract:

1. ``TRWConfig()`` with no env, no yaml — default is 7.
2. ``TRWConfig()`` with ``TRW_LEARNING_OUTCOME_CORRELATION_WINDOW_MINUTES``
   env var — env wins (Pydantic ``BaseSettings`` precedence: env > yaml >
   field default). The env-override regression is real because §FR04
   acceptance #2 explicitly requires it.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config


def _fresh_config() -> TRWConfig:
    """Build a fresh ``TRWConfig`` with the global singleton cleared."""
    _reset_config()
    return TRWConfig()


def test_default_window_is_seven_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04 acceptance #1: default with no env / no yaml is 7 minutes."""
    # Defensive: scrub the env var so a developer-environment leak doesn't
    # silently mask a regression.
    monkeypatch.delenv("TRW_LEARNING_OUTCOME_CORRELATION_WINDOW_MINUTES", raising=False)

    config = _fresh_config()

    assert config.learning_outcome_correlation_window_minutes == 7, (
        f"FR04: default correlation window MUST be 7 minutes, got "
        f"{config.learning_outcome_correlation_window_minutes}. The pre-fix "
        f"live deployment ran at 60 minutes; if this regressed back, expect "
        f"a 91 s build_check latency under load."
    )


def test_env_override_wins_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04 acceptance #2: ``TRW_LEARNING_...=60`` env override beats the default."""
    monkeypatch.setenv("TRW_LEARNING_OUTCOME_CORRELATION_WINDOW_MINUTES", "60")

    config = _fresh_config()

    assert config.learning_outcome_correlation_window_minutes == 60, (
        f"FR04: TRW_LEARNING_OUTCOME_CORRELATION_WINDOW_MINUTES=60 must win "
        f"over the field default, got {config.learning_outcome_correlation_window_minutes}. "
        f"If this fails, a `Field(...)` change has broken Pydantic env precedence."
    )


def test_env_override_wins_at_12(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: env override at any non-default value wins (12 minutes regression case)."""
    monkeypatch.setenv("TRW_LEARNING_OUTCOME_CORRELATION_WINDOW_MINUTES", "12")

    config = _fresh_config()

    assert config.learning_outcome_correlation_window_minutes == 12, (
        f"FR04: env override at 12 must win, got "
        f"{config.learning_outcome_correlation_window_minutes}"
    )
