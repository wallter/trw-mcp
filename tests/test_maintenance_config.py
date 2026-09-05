"""Tests for maintenance config fields."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config


class TestTRWConfigMaintenanceFields:
    """Validate new config fields introduced for maintenance features."""

    def test_run_auto_close_enabled_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.run_auto_close_enabled is True

    def test_learning_auto_prune_on_deliver_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_on_deliver is True

    def test_learning_auto_prune_cap_default(self) -> None:
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_cap == 150

    def test_run_auto_close_enabled_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_RUN_AUTO_CLOSE_ENABLED", "false")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.run_auto_close_enabled is False

    def test_learning_auto_prune_on_deliver_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRW_LEARNING_AUTO_PRUNE_ON_DELIVER", "false")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_on_deliver is False

    def test_learning_auto_prune_cap_env_override(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("TRW_LEARNING_AUTO_PRUNE_CAP", "200")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.learning_auto_prune_cap == 200


class TestWalCheckpointTunables:
    """PRD-CORE-248 NFR05: no magic numbers in the WAL-checkpoint subsystem."""

    def test_no_caller_permit_knob_survives_the_oq1_reversal(self) -> None:
        """PRD-CORE-248 OQ-1 was reversed: no config may influence a WAL reset.

        ``wal_truncate_exclusive_wait_ms`` bounded a BEGIN EXCLUSIVE probe that
        review deleted, because the probe cannot be held across the checkpoint
        PRAGMA. A knob whose only job was to tune an unsafe permit must not
        linger as a dormant field.
        """
        assert "wal_truncate_exclusive_wait_ms" not in TRWConfig.model_fields

    def test_wal_tunables_are_bounded_documented_fields(self) -> None:
        """Every touched tunable exposes a description and both ge/le bounds.

        ``wal_checkpoint_threshold_mb`` was the bare annotated integer
        ``wal_checkpoint_threshold_mb: int = 10`` — no bounds, no description,
        no age companion — which is exactly why the policy it governed drifted
        without anyone being able to see the knob.
        """
        from annotated_types import Ge, Le

        for name in (
            "wal_checkpoint_threshold_mb",
            "wal_checkpoint_max_age_seconds",
            "wal_checkpoint_idle_interval_seconds",
            "boot_deferred_work_budget_ms",
            "pin_ttl_hours",
        ):
            info = TRWConfig.model_fields[name]
            assert info.description, f"{name} must carry a description"
            kinds = {type(m) for m in info.metadata}
            assert Ge in kinds, f"{name} must declare a lower bound"
            if name != "pin_ttl_hours":
                # pin_ttl_hours predates this PRD and is unbounded above by design.
                assert Le in kinds, f"{name} must declare an upper bound"

    def test_wal_tunable_defaults_are_pinned(self) -> None:
        """Defaults are the measured policy, not an accident of editing."""
        cfg = TRWConfig()
        assert cfg.wal_checkpoint_threshold_mb == 10  # unchanged by NFR05
        assert cfg.wal_checkpoint_max_age_seconds == 3600  # the wal_liveness SLO period
        assert cfg.wal_checkpoint_idle_interval_seconds == 60
        assert cfg.boot_deferred_work_budget_ms == 5000

    def test_wal_tunables_are_env_overridable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_WAL_CHECKPOINT_MAX_AGE_SECONDS", "900")
        monkeypatch.setenv("TRW_WAL_CHECKPOINT_IDLE_INTERVAL_SECONDS", "15")
        monkeypatch.setenv("TRW_BOOT_DEFERRED_WORK_BUDGET_MS", "500")
        _reset_config()
        cfg = TRWConfig()
        assert cfg.wal_checkpoint_max_age_seconds == 900
        assert cfg.wal_checkpoint_idle_interval_seconds == 15
        assert cfg.boot_deferred_work_budget_ms == 500

    def test_wal_tunables_reject_out_of_range_values(self) -> None:
        import pydantic

        for name, value in (
            ("wal_checkpoint_threshold_mb", 0),
            ("wal_checkpoint_max_age_seconds", 10),
            ("wal_checkpoint_idle_interval_seconds", 1),
            ("boot_deferred_work_budget_ms", 1),
        ):
            with pytest.raises(pydantic.ValidationError):
                TRWConfig(**{name: value})

    def test_new_wal_tunables_carry_full_admission_records(self) -> None:
        """PRD-CORE-218 FR05: a new public field needs explicit admission metadata."""
        from trw_mcp.models.config._field_admission import build_field_admissions

        admissions = build_field_admissions()
        for name in (
            "wal_checkpoint_max_age_seconds",
            "wal_checkpoint_idle_interval_seconds",
            "boot_deferred_work_budget_ms",
        ):
            record = admissions[name]
            assert record.budget_decision == "admitted"
            assert record.consumer and record.consumer != "TRWConfig"
            assert "PRD-CORE-248" in record.owner
