"""Tests for TRWConfig — platform fields, config.yaml loading, env overrides.

Coverage:
- T-CFG-01: Default values for new platform config fields
- T-CFG-02: Env var overrides via TRW_ prefix
- T-CFG-03: YAML round-trip serialization
- T-CFG-04: config.yaml loading via get_config() singleton (PRD-FIX-026)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, _reset_config, get_config


class TestPlatformConfigDefaults:
    """T-CFG-01: Verify default values for new platform/telemetry fields."""

    def test_platform_telemetry_enabled_default(self) -> None:
        config = TRWConfig()
        assert config.platform_telemetry_enabled is False

    def test_update_channel_default(self) -> None:
        config = TRWConfig()
        assert config.update_channel == "latest"

    def test_platform_url_default(self) -> None:
        config = TRWConfig()
        assert config.platform_url == ""

    def test_installation_id_default(self) -> None:
        config = TRWConfig()
        assert config.installation_id == ""


class TestPlatformConfigEnvOverrides:
    """T-CFG-02: Verify env var overrides for new platform/telemetry fields."""

    def test_telemetry_enabled_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PLATFORM_TELEMETRY_ENABLED", "true")
        config = TRWConfig()
        assert config.platform_telemetry_enabled is True

    def test_update_channel_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_UPDATE_CHANNEL", "lts")
        config = TRWConfig()
        assert config.update_channel == "lts"

    def test_platform_url_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PLATFORM_URL", "https://api.trwframework.com")
        config = TRWConfig()
        assert config.platform_url == "https://api.trwframework.com"

    def test_installation_id_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_INSTALLATION_ID", "anon-abc123")
        config = TRWConfig()
        assert config.installation_id == "anon-abc123"

    def test_telemetry_enabled_false_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PLATFORM_TELEMETRY_ENABLED", "false")
        config = TRWConfig()
        assert config.platform_telemetry_enabled is False


class TestPlatformConfigYamlRoundTrip:
    """T-CFG-03: Verify YAML round-trip serialization of new platform fields."""

    def test_model_dump_includes_new_fields(self) -> None:
        config = TRWConfig()
        data = config.model_dump()
        assert "platform_telemetry_enabled" in data
        assert "update_channel" in data
        assert "platform_url" in data
        assert "installation_id" in data

    def test_model_dump_values_match_defaults(self) -> None:
        config = TRWConfig()
        data = config.model_dump()
        assert data["platform_telemetry_enabled"] is False
        assert data["update_channel"] == "latest"
        assert data["platform_url"] == ""
        assert data["installation_id"] == ""

    def test_model_dump_with_overrides(self) -> None:
        config = TRWConfig(
            platform_telemetry_enabled=True,
            update_channel="lts",
            platform_url="https://api.trwframework.com",
            installation_id="anon-xyz789",
        )
        data = config.model_dump()
        assert data["platform_telemetry_enabled"] is True
        assert data["update_channel"] == "lts"
        assert data["platform_url"] == "https://api.trwframework.com"
        assert data["installation_id"] == "anon-xyz789"

    def test_round_trip_via_model_construct(self) -> None:
        config = TRWConfig(
            platform_telemetry_enabled=True,
            update_channel="lts",
            platform_url="https://api.trwframework.com",
            installation_id="anon-abc456",
        )
        dumped = config.model_dump()
        restored = TRWConfig(**dumped)
        assert restored.platform_telemetry_enabled is True
        assert restored.update_channel == "lts"
        assert restored.platform_url == "https://api.trwframework.com"
        assert restored.installation_id == "anon-abc456"


# ── Config.yaml Loading Tests (PRD-FIX-026) ─────────────────────────────


@pytest.fixture()
def config_project(tmp_path: Path) -> Path:
    """Create a minimal project with .trw/config.yaml for config loading tests."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".trw").mkdir()
    return tmp_path


class TestConfigYamlLoading:
    """T-CFG-04: get_config() loads .trw/config.yaml overrides."""

    def test_config_yaml_values_loaded(self, config_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config.yaml values are loaded into singleton."""
        config_yaml = config_project / ".trw" / "config.yaml"
        config_yaml.write_text(
            "build_check_pytest_cmd: make test\ntask_root: tasks\n",
            encoding="utf-8",
        )
        # Point resolve_project_root to our tmp project
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.build_check_pytest_cmd == "make test"
            assert cfg.task_root == "tasks"
        finally:
            _reset_config()

    def test_env_vars_override_config_yaml(self, config_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env vars take priority over config.yaml values."""
        config_yaml = config_project / ".trw" / "config.yaml"
        config_yaml.write_text("task_root: from-yaml\n", encoding="utf-8")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        monkeypatch.setenv("TRW_TASK_ROOT", "from-env")
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.task_root == "from-env"
        finally:
            _reset_config()

    def test_missing_config_yaml_falls_back(self, config_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing config.yaml gracefully falls back to defaults."""
        # No config.yaml written
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.task_root == "docs"  # default
            assert cfg.build_check_pytest_cmd is None  # default
        finally:
            _reset_config()

    def test_corrupt_config_yaml_falls_back(self, config_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Corrupt config.yaml gracefully falls back to defaults."""
        config_yaml = config_project / ".trw" / "config.yaml"
        config_yaml.write_text("{{{{invalid yaml!!!!", encoding="utf-8")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.task_root == "docs"  # default
        finally:
            _reset_config()

    def test_no_project_root_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When resolve_project_root fails, falls back to defaults."""

        def _raise() -> Path:
            msg = "Not in a git repo"
            raise FileNotFoundError(msg)

        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            _raise,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.task_root == "docs"
        finally:
            _reset_config()

    def test_unknown_keys_ignored(self, config_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unknown config.yaml keys are silently ignored (extra='ignore')."""
        config_yaml = config_project / ".trw" / "config.yaml"
        config_yaml.write_text(
            "nonexistent_field: true\ntask_root: custom\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.task_root == "custom"
        finally:
            _reset_config()

    def test_none_values_filtered(self, config_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Config.yaml entries with null values don't override defaults."""
        config_yaml = config_project / ".trw" / "config.yaml"
        config_yaml.write_text("task_root: null\ndebug: true\n", encoding="utf-8")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.task_root == "docs"  # null filtered, keeps default
            assert cfg.debug is True
        finally:
            _reset_config()

    def test_meta_tune_flat_key_populates_nested_config(
        self, config_project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SAFE-001: legacy ``meta_tune_enabled`` still activates nested config."""
        config_yaml = config_project / ".trw" / "config.yaml"
        config_yaml.write_text("meta_tune_enabled: true\n", encoding="utf-8")
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_project_root",
            lambda: config_project,
        )
        _reset_config()
        try:
            cfg = get_config()
            assert cfg.meta_tune_enabled is True
            assert cfg.meta_tune.enabled is True
        finally:
            _reset_config()


# --- Config field defaults (consolidated from 14 individual tests) ---


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("atdd_enabled", True),
        ("worktree_dir", ".trees"),
        ("commit_fr_trailer_enabled", True),
        ("sprint_integration_branch_pattern", "sprint-{N}-integration"),
        ("compliance_review_retention_days", 365),
        ("provenance_enabled", True),
        ("confidence_threshold", 0.8),
        ("test_skeleton_dir", ""),
        ("completion_hooks_blocking", False),
        ("self_review_blocking", False),
        ("incremental_validation_enabled", True),
        ("security_check_enabled", True),
        ("compact_instructions_template", ""),
        ("pause_after_compaction", False),
    ],
    ids=[
        "atdd_enabled",
        "worktree_dir",
        "commit_fr_trailer_enabled",
        "sprint_integration_branch_pattern",
        "compliance_review_retention_days",
        "provenance_enabled",
        "confidence_threshold",
        "test_skeleton_dir",
        "completion_hooks_blocking",
        "self_review_blocking",
        "incremental_validation_enabled",
        "security_check_enabled",
        "compact_instructions_template",
        "pause_after_compaction",
    ],
)
def test_config_defaults(field: str, expected: object) -> None:
    """Verify TRWConfig field defaults (consolidated from 14 individual tests)."""
    config = TRWConfig()
    assert getattr(config, field) == expected
