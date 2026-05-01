"""Tests for .claude/settings.json expectations."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._test_bundle_asset_support import _MONOREPO_CLAUDE


class TestSettingsJson:
    """Tests for .claude/settings.json hook registrations.

    These tests validate the monorepo's settings.json. When running in the
    standalone public repo (no .claude/settings.json at repo root), tests
    are skipped — the equivalent validation happens in test_bootstrap.py
    via init-project.
    """

    @pytest.fixture()
    def settings_path(self) -> Path:
        """Return path to settings.json (monorepo only — skips in standalone)."""
        path = _MONOREPO_CLAUDE / "settings.json"
        if not path.exists():
            pytest.skip("settings.json not present (standalone repo — tested via init-project)")
        return path

    def test_settings_exists(self, settings_path: Path) -> None:
        """settings.json exists."""
        assert settings_path.exists()

    def test_settings_valid_json(self, settings_path: Path) -> None:
        """settings.json is valid JSON."""
        import json

        content = settings_path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_retired_peer_team_hooks_not_registered(self, settings_path: Path) -> None:
        """v25 settings do not register retired peer-team hook events."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        assert "TeammateIdle" not in hooks
        assert "TaskCompleted" not in hooks

    def test_retired_peer_team_env_var_not_set(self, settings_path: Path) -> None:
        """v25 settings do not opt into the retired peer-team env var."""
        import json

        data = json.loads(settings_path.read_text(encoding="utf-8"))
        env = data.get("env", {})
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT" + "_TEAMS" not in env
