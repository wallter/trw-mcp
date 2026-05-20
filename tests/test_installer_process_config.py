"""Tests for installer config and backend helper functions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from tests._installer_process_support import (
    _TIPS,
    _check_all_backends,
    _check_backend_health,
    _load_prior_config,
    phase_prompt_features,
    update_config,
)


class TestLoadPriorConfig:
    """FR02: Config-level feature flag persistence."""

    def test_with_feature_flags(self, tmp_path: Path) -> None:
        """Config with embeddings_enabled and sqlite_vec_enabled is parsed correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            "installation_id: my-project\nembeddings_enabled: true\nsqlite_vec_enabled: false\n",
            encoding="utf-8",
        )
        result = _load_prior_config(tmp_path)
        assert result["project_name"] == "my-project"
        assert result["embeddings"] is True
        assert result["sqlite_vec"] is False

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """No config file returns empty dict."""
        assert _load_prior_config(tmp_path) == {}

    def test_malformed_content_returns_empty(self, tmp_path: Path) -> None:
        """Binary/garbage content returns empty dict (OSError or parse failure)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_bytes(b"\x00\x01\x02\xff")
        result = _load_prior_config(tmp_path)
        assert isinstance(result, dict)

    def test_comments_and_empty_lines_skipped(self, tmp_path: Path) -> None:
        """Comments and blank lines don't affect parsing."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            "# This is a comment\n\ninstallation_id: test\n# another comment\n",
            encoding="utf-8",
        )
        result = _load_prior_config(tmp_path)
        assert result["project_name"] == "test"

    def test_target_platforms_list_is_loaded(self, tmp_path: Path) -> None:
        """target_platforms list is parsed for reinstall reuse."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            'target_platforms:\n  - "cursor-ide"\n  - "codex"\n',
            encoding="utf-8",
        )
        result = _load_prior_config(tmp_path)
        assert result["target_platforms"] == ["cursor-ide", "codex"]

    def test_platform_urls_list_is_loaded(self, tmp_path: Path) -> None:
        """platform_urls list is parsed so reinstall/upgrade can preserve custom backends."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            "platform_urls:\n# - https://api.trwframework.com  # intentionally disabled\n- http://localhost:5002\n",
            encoding="utf-8",
        )

        result = _load_prior_config(tmp_path)

        assert result["platform_urls"] == ["http://localhost:5002"]


class TestCheckBackendHealth:
    """FR03: Real backend health check via HTTP probe."""

    def test_success_response(self) -> None:
        """200 response with status field returns reachable=True."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status": "ok"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is True
        assert result["status"] == "ok"

    def test_http_error(self) -> None:
        """HTTP 500 still counts as reachable (server responded)."""
        with patch(
            "urllib.request.urlopen",
            side_effect=HTTPError(
                "http://x/v1/health",
                500,
                "ISE",
                {},
                None,
            ),
        ):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is True
        assert result["status"] == "http-500"

    def test_connection_refused(self) -> None:
        """Connection refused returns unreachable."""
        with patch("urllib.request.urlopen", side_effect=URLError(ConnectionRefusedError("refused"))):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is False
        assert result["status"] == "unreachable"

    def test_timeout(self) -> None:
        """Socket timeout returns unreachable."""
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            result = _check_backend_health("http://example.com")

        assert result["reachable"] is False
        assert result["status"] == "unreachable"


class TestCheckAllBackends:
    """FR04: Docker backend auto-detection."""

    def test_docker_compose_detected(self, tmp_path: Path) -> None:
        """docker-compose.yml triggers localhost:8000 probe."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n", encoding="utf-8")

        with patch(
            "tests._installer_process_support._check_backend_health",
            return_value={"url": "http://localhost:8000", "reachable": False, "status": "unreachable"},
        ):
            results = _check_all_backends(tmp_path, {})

        probed_urls = [r["url"] for r in results]
        assert "http://localhost:8000" in probed_urls

    def test_no_duplicates_with_config(self, tmp_path: Path) -> None:
        """Config URL matching Docker URL is not duplicated."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            'platform_urls:\n  - "http://localhost:8000"\n',
            encoding="utf-8",
        )
        (tmp_path / "docker-compose.yml").write_text("version: '3'\n", encoding="utf-8")

        results = _check_all_backends(tmp_path, {})
        urls = [r["url"] for r in results]
        assert urls.count("http://localhost:8000") == 1

    def test_no_compose_no_local(self, tmp_path: Path) -> None:
        """Without docker-compose, localhost is not auto-added."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text("task_root: docs\n", encoding="utf-8")

        results = _check_all_backends(tmp_path, {})
        assert results == []


class TestUpdateConfig:
    """FR02: Feature flag persistence in config.yaml."""

    def test_persists_feature_flags(self, tmp_path: Path) -> None:
        """Feature flags are written to config.yaml."""
        config = tmp_path / "config.yaml"
        config.write_text("installation_id: test\n", encoding="utf-8")

        update_config(config, "test", "", False, embeddings_enabled=True, sqlite_vec_enabled=True)

        content = config.read_text(encoding="utf-8")
        assert "embeddings_enabled: true" in content
        assert "sqlite_vec_enabled: true" in content

    def test_roundtrip(self, tmp_path: Path) -> None:
        """Write then read back feature flags."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = trw_dir / "config.yaml"
        config.write_text("installation_id: test\n", encoding="utf-8")

        update_config(config, "test", "", False, embeddings_enabled=True, sqlite_vec_enabled=True)

        prior = _load_prior_config(tmp_path)
        assert prior.get("embeddings") is True
        assert prior.get("sqlite_vec") is True

    def test_updates_existing_flags(self, tmp_path: Path) -> None:
        """Existing flags are updated in-place, not duplicated."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "installation_id: test\nembeddings_enabled: false\n",
            encoding="utf-8",
        )

        update_config(config, "test", "", False, embeddings_enabled=True)

        content = config.read_text(encoding="utf-8")
        assert content.count("embeddings_enabled") == 1
        assert "embeddings_enabled: true" in content

    def test_preserves_newlines_before_appending_platform_urls(self, tmp_path: Path) -> None:
        """Appending platform URLs must not merge onto a prior line without newline."""
        config = tmp_path / "config.yaml"
        config.write_text('platform_user_email: "user@example.com"', encoding="utf-8")

        update_config(config, "test", "trw_key_123", True)

        content = config.read_text(encoding="utf-8")
        assert 'platform_user_email: "user@example.com"platform_urls:' not in content
        assert 'platform_user_email: "user@example.com"\n' in content
        assert "platform_urls:\n" in content

    def test_rewrites_platform_urls_without_duplication(self, tmp_path: Path) -> None:
        """Existing platform_urls blocks are replaced in place, not duplicated."""
        config = tmp_path / "config.yaml"
        config.write_text(
            'platform_api_key: ""\nplatform_urls:\n  - "http://old.example.com"\n',
            encoding="utf-8",
        )

        update_config(config, "test", "trw_key_123", True)

        content = config.read_text(encoding="utf-8")
        assert content.count("platform_urls:") == 1
        assert '"https://api.trwframework.com"' in content
        assert '"http://old.example.com"' not in content

    def test_rewrites_platform_urls_with_comments_without_yaml_corruption(self, tmp_path: Path) -> None:
        """Replacement consumes comments and list items from the old block, even when list items are unindented."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "platform_urls:\n"
            "# - https://api.trwframework.com  # temporarily disabled\n"
            "- http://localhost:5002\n"
            "platform_telemetry_enabled: true\n",
            encoding="utf-8",
        )

        update_config(config, "test", "trw_key_123", True)

        content = config.read_text(encoding="utf-8")
        assert content.count("platform_urls:") == 1
        assert '"https://api.trwframework.com"' in content
        assert "localhost:5002" not in content
        assert "platform_telemetry_enabled: true" in content

    def test_preserves_existing_platform_urls_when_requested(self, tmp_path: Path) -> None:
        """Upgrade-only reinstalls with prior platform URLs should not clobber custom backend routing."""
        config = tmp_path / "config.yaml"
        config.write_text(
            "platform_urls:\n"
            "# - https://api.trwframework.com  # intentionally disabled\n"
            "- http://localhost:5002\n"
            "platform_telemetry_enabled: true\n",
            encoding="utf-8",
        )

        update_config(config, "test", "trw_key_123", True, rewrite_platform_urls=False)

        content = config.read_text(encoding="utf-8")
        assert "# - https://api.trwframework.com" in content
        assert "- http://localhost:5002" in content
        assert '"https://api.trwframework.com"' not in content

    def test_persists_target_platforms(self, tmp_path: Path) -> None:
        """Selected client targets are written to config.yaml."""
        config = tmp_path / "config.yaml"
        config.write_text("installation_id: test\n", encoding="utf-8")

        update_config(config, "test", "", False, target_platforms=["cursor-ide", "codex", "gemini"])

        content = config.read_text(encoding="utf-8")
        assert 'target_platforms:\n  - "cursor-ide"\n  - "codex"\n  - "gemini"\n' in content

    def test_rewrites_target_platforms_without_duplication(self, tmp_path: Path) -> None:
        """Existing target_platforms blocks are replaced in place."""
        config = tmp_path / "config.yaml"
        config.write_text(
            'target_platforms:\n  - "claude-code"\n  - "opencode"\nplatform_api_key: ""\n',
            encoding="utf-8",
        )

        update_config(config, "test", "", False, target_platforms=["cursor-cli", "codex"])

        content = config.read_text(encoding="utf-8")
        assert content.count("target_platforms:") == 1
        assert '"claude-code"' not in content
        assert '"opencode"' not in content
        assert '"cursor-cli"' in content
        assert '"codex"' in content

    def test_empty_target_platforms_preserves_existing_block(self, tmp_path: Path) -> None:
        """Upgrade-only project setup returns an empty selection; that must not erase prior clients."""
        config = tmp_path / "config.yaml"
        config.write_text(
            'target_platforms:\n  - "claude-code"\n  - "codex"\nplatform_api_key: ""\n',
            encoding="utf-8",
        )

        update_config(config, "test", "", False, target_platforms=[])

        content = config.read_text(encoding="utf-8")
        assert '"claude-code"' in content
        assert '"codex"' in content


class TestPhasePromptFeatures:
    """FR02: Feature prompt logic with prior_extras."""

    def test_both_configured_returns_true(self) -> None:
        """Both extras configured: returns (True, True) without prompting."""
        ai, vec = phase_prompt_features(
            None,
            None,
            prior_extras={"ai": True, "sqlite_vec": True},
        )
        assert ai is True
        assert vec is True

    def test_cli_override(self) -> None:
        """CLI flags override prior_extras."""
        ai, vec = phase_prompt_features(
            False,
            False,
            prior_extras={"ai": True, "sqlite_vec": True},
        )
        assert ai is False
        assert vec is False

    def test_partial_config(self) -> None:
        """Only AI configured: AI auto-accepted, sqlite_vec defaults to False."""
        ai, vec = phase_prompt_features(
            None,
            None,
            prior_extras={"ai": True},
        )
        assert ai is True
        assert vec is False

    def test_no_prior_no_cli(self) -> None:
        """No prior config and no CLI flags: both default to False."""
        ai, vec = phase_prompt_features(None, None, prior_extras={})
        assert ai is False
        assert vec is False


class TestTips:
    """FR06: Random tip display."""

    def test_tips_list_has_12_items(self) -> None:
        assert len(_TIPS) == 12

    def test_all_tips_are_strings(self) -> None:
        assert all(isinstance(t, str) for t in _TIPS)

    def test_all_tips_are_nonempty(self) -> None:
        assert all(len(t) > 10 for t in _TIPS)
