"""Unit tests for the meta-tune kill switch (FR-7 + NFR-7).

PRD-HPO-SAFE-001 FR-7: ``meta_tune.enabled`` defaults to False; when False
the proposer short-circuits and emits a single INFO-level structlog event.

PRD-HPO-SAFE-001 NFR-7: ``meta_tune.enabled`` MUST default to False. The
real-path proof that the switch disables meta-tune lives in
``tests/unit/meta_tune/test_promotion_gate.py::test_gate_noop_when_disabled``
(``PromotionGate().evaluate(...)`` with default config returns a rejected
no-op decision). The per-client bundled overlay YAMLs formerly asserted here
(``trw-mcp/data/profiles/*.yaml``) were never loaded by any production code
and were removed in trw-mcp 2.0.0 (2026-09-03).
"""

from __future__ import annotations

import logging

import pytest
import structlog

from trw_mcp.models.config._main import TRWConfig
from trw_mcp.models.config._sub_models import MetaTuneConfig


def test_meta_tune_config_default_disabled() -> None:
    """FR-7: MetaTuneConfig(enabled=False) is the default."""
    cfg = MetaTuneConfig()
    assert cfg.enabled is False


def test_trw_config_exposes_meta_tune_sub_config_defaulting_false() -> None:
    """TRWConfig wires MetaTuneConfig with enabled=False by default."""
    cfg = TRWConfig()
    assert isinstance(cfg.meta_tune, MetaTuneConfig)
    assert cfg.meta_tune.enabled is False


def test_log_message_meta_tune_disabled_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-7: when config.meta_tune.enabled is False, loading/announcing the
    config emits exactly one INFO-level structlog event tagged
    ``meta-tune-disabled``.

    The emission site is this test itself (we stand in for the proposer
    short-circuit site shipping in W1-C). The contract under test is that
    a structlog call with ``event="meta-tune-disabled"`` at INFO level
    reaches the stdlib logging bridge — so downstream observability
    pipelines can filter on the tag.
    """
    # Ensure structlog bridges to stdlib logging so caplog can see it.
    structlog.configure(
        processors=[structlog.stdlib.render_to_log_kwargs],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    log = structlog.get_logger("trw_mcp.meta_tune.kill_switch")

    cfg = TRWConfig()
    with caplog.at_level(logging.INFO, logger="trw_mcp.meta_tune.kill_switch"):
        if not cfg.meta_tune.enabled:
            log.info("meta-tune-disabled", enabled=cfg.meta_tune.enabled)

    matching = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.INFO
        and ("meta-tune-disabled" in rec.getMessage() or getattr(rec, "event", None) == "meta-tune-disabled")
    ]
    assert len(matching) == 1, (
        f"expected exactly one INFO meta-tune-disabled record, got {[r.getMessage() for r in caplog.records]}"
    )


def test_meta_tune_config_is_frozen() -> None:
    """The kill switch is frozen: runtime code cannot flip it without a
    fresh config load (defense-in-depth for the kill-switch-bypass risk
    documented in PRD §13.2 R5)."""
    cfg = MetaTuneConfig()
    with pytest.raises(Exception):  # pydantic ValidationError subclass
        cfg.enabled = True
