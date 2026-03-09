"""Tests for Progressive Trust Model (PRD-CORE-068)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.trust import (
    increment_session_count,
    read_audit_log,
    read_trust_registry,
    requires_human_review,
    trust_level_calculate,
    write_trust_registry,
)


@pytest.fixture()
def trust_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set up a .trw directory with trust registry support."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    config = TRWConfig()
    _reset_config(config)
    monkeypatch.setenv("TRW_AGENT_ID", "test-agent")

    yield trw_dir, config
    _reset_config()


class TestTrustRegistry:
    """FR01: Trust Registry Data Store."""

    def test_registry_created_on_first_access(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        registry = read_trust_registry(trw_dir)
        project = registry["project"]
        assert isinstance(project, dict)
        assert project["session_count"] == 0
        assert project["tier"] == "crawl"

    def test_registry_read_existing(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {
                "project": {
                    "session_count": 75,
                    "successful_sessions": 72,
                    "last_session_at": "2026-03-01T18:00:00Z",
                    "tier": "walk",
                }
            },
        )
        registry = read_trust_registry(trw_dir)
        project = registry["project"]
        assert isinstance(project, dict)
        assert project["session_count"] == 75


class TestTrustLevelCalculation:
    """FR02: Trust Level Calculation."""

    def test_crawl_at_session_1(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 1, "successful_sessions": 1, "last_session_at": None, "tier": "crawl"}},
        )
        result = trust_level_calculate(trw_dir, config)
        assert result["tier"] == "crawl"
        assert result["review_mode"] == "mandatory"
        assert result["review_sample_rate"] == 1.0

    def test_crawl_at_boundary_50(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 50, "successful_sessions": 50, "last_session_at": None, "tier": "crawl"}},
        )
        result = trust_level_calculate(trw_dir, config)
        assert result["tier"] == "crawl"

    def test_walk_starts_at_51(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 51, "successful_sessions": 51, "last_session_at": None, "tier": "walk"}},
        )
        result = trust_level_calculate(trw_dir, config)
        assert result["tier"] == "walk"
        assert result["review_mode"] == "sampled"
        assert result["review_sample_rate"] == 0.3

    def test_walk_upper_boundary_200(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 200, "successful_sessions": 200, "last_session_at": None, "tier": "walk"}},
        )
        result = trust_level_calculate(trw_dir, config)
        assert result["tier"] == "walk"

    def test_run_starts_at_201(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 201, "successful_sessions": 201, "last_session_at": None, "tier": "run"}},
        )
        result = trust_level_calculate(trw_dir, config)
        assert result["tier"] == "run"
        assert result["review_mode"] == "risk_based"
        assert result["review_sample_rate"] is None

    def test_custom_crawl_boundary(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        custom_config = TRWConfig(trust_crawl_boundary=30, trust_walk_boundary=200)
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 35, "successful_sessions": 35, "last_session_at": None, "tier": "walk"}},
        )
        result = trust_level_calculate(trw_dir, custom_config)
        assert result["tier"] == "walk"


class TestSecurityTagOverride:
    """FR03: Security-Tagged Change Override."""

    def test_security_tag_overrides_run_tier(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trust_result: dict[str, object] = {"tier": "run", "session_count": 300}
        result = requires_human_review(["auth"], [], trust_result)
        assert result["required"] is True
        assert result["reason"] == "security_tagged"
        assert result["override_tier"] is True

    def test_security_tag_overrides_walk_tier(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trust_result: dict[str, object] = {"tier": "walk", "session_count": 100}
        result = requires_human_review(["secrets"], [], trust_result)
        assert result["required"] is True
        assert result["override_tier"] is True

    def test_no_security_tag_crawl(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trust_result: dict[str, object] = {"tier": "crawl", "session_count": 10}
        result = requires_human_review([], [], trust_result)
        assert result["required"] is True
        assert result["reason"] == "crawl_mandatory"

    def test_no_security_tag_run(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trust_result: dict[str, object] = {"tier": "run", "session_count": 300}
        result = requires_human_review([], ["utils.py"], trust_result)
        assert result["required"] is False
        assert result["reason"] == "risk_based"

    def test_risk_based_file_pattern(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trust_result: dict[str, object] = {"tier": "run", "session_count": 300}
        result = requires_human_review([], ["auth_handler.py"], trust_result)
        assert result["required"] is True
        assert result["reason"] == "risk_based_file_pattern"


class TestAdminLock:
    """FR08: Admin Trust Lock."""

    def test_admin_lock_overrides_run(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        locked_config = TRWConfig(trust_locked=True)
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 300, "successful_sessions": 300, "last_session_at": None, "tier": "run"}},
        )
        result = trust_level_calculate(trw_dir, locked_config)
        assert result["tier"] == "crawl"
        assert result["locked"] is True
        assert result["lock_reason"] == "admin_override"

    def test_admin_lock_removed(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, config = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 300, "successful_sessions": 300, "last_session_at": None, "tier": "run"}},
        )
        result = trust_level_calculate(trw_dir, config)
        assert result["tier"] == "run"
        assert result["locked"] is False


class TestSessionIncrement:
    """FR05: Session Count Increment."""

    def test_increment_on_deliver(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 10, "successful_sessions": 10, "last_session_at": None, "tier": "crawl"}},
        )
        result = increment_session_count(trw_dir, "test-agent")
        assert result["session_count"] == 11
        assert result["transitioned"] is False

    def test_transition_logged(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 50, "successful_sessions": 50, "last_session_at": None, "tier": "crawl"}},
        )
        result = increment_session_count(trw_dir, "test-agent")
        assert result["session_count"] == 51
        assert result["transitioned"] is True
        assert result["previous_tier"] == "crawl"
        assert result["new_tier"] == "walk"

    def test_no_increment_preserves_count(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 5, "successful_sessions": 5, "last_session_at": None, "tier": "crawl"}},
        )
        registry = read_trust_registry(trw_dir)
        project = registry["project"]
        assert isinstance(project, dict)
        assert project["session_count"] == 5


class TestAuditLog:
    """FR07: Trust Transition Audit Log."""

    def test_transition_creates_audit_entry(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 50, "successful_sessions": 50, "last_session_at": None, "tier": "crawl"}},
        )
        increment_session_count(trw_dir, "test-agent")
        entries = read_audit_log(trw_dir)
        assert len(entries) == 1
        entry = entries[0]
        assert entry["previous_tier"] == "crawl"
        assert entry["new_tier"] == "walk"
        assert entry["session_count"] == 51
        assert entry["triggered_by"] == "session_count"
        assert "timestamp" in entry
        assert entry["agent_id"] == "test-agent"

    def test_audit_append_only(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        # Create 2 transitions
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 50, "successful_sessions": 50, "last_session_at": None, "tier": "crawl"}},
        )
        increment_session_count(trw_dir, "agent-1")
        # Reset to 200 for another transition
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 200, "successful_sessions": 200, "last_session_at": None, "tier": "walk"}},
        )
        increment_session_count(trw_dir, "agent-2")
        entries = read_audit_log(trw_dir)
        assert len(entries) == 2
        assert entries[0]["new_tier"] == "walk"
        assert entries[1]["new_tier"] == "run"

    def test_no_audit_without_transition(self, trust_env: tuple[Path, TRWConfig]) -> None:
        trw_dir, _ = trust_env
        write_trust_registry(
            trw_dir,
            {"project": {"session_count": 10, "successful_sessions": 10, "last_session_at": None, "tier": "crawl"}},
        )
        increment_session_count(trw_dir, "test-agent")
        entries = read_audit_log(trw_dir)
        assert len(entries) == 0
