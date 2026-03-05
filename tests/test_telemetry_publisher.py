"""Tests for trw_mcp.telemetry.publisher — PRD-CORE-033."""

from __future__ import annotations

import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

from trw_mcp.telemetry.publisher import _post_learning, publish_learnings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_learning(entries_dir: Path, filename: str, data: dict[str, object]) -> None:
    """Write a learning YAML file to the entries directory."""
    import ruamel.yaml

    entries_dir.mkdir(parents=True, exist_ok=True)
    yaml = ruamel.yaml.YAML()
    with (entries_dir / filename).open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


def _make_learning(
    *,
    status: str = "active",
    impact: float = 0.9,
    summary: str = "Test learning summary",
    detail: str = "Test learning detail",
    tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "impact": impact,
        "summary": summary,
        "detail": detail,
        "tags": tags or ["testing"],
    }


# ===========================================================================
# Offline / config guard
# ===========================================================================


class TestPublishOfflineMode:
    def test_publish_offline_mode_no_url(self) -> None:
        """Empty platform_url returns skipped_reason='offline_mode'."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(platform_url="", platform_telemetry_enabled=True)
        with patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] == "offline_mode"

    def test_publish_offline_mode_telemetry_disabled(self) -> None:
        """platform_telemetry_enabled=False returns skipped_reason='offline_mode'."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=False,
        )
        with patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg):
            result = publish_learnings()

        assert result["skipped_reason"] == "offline_mode"


# ===========================================================================
# No entries directory
# ===========================================================================


class TestPublishNoEntriesDir:
    def test_publish_no_entries_dir(self, tmp_path: Path) -> None:
        """Non-existent entries dir returns skipped_reason='no_entries'."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        # Do NOT create entries_dir

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] == "no_entries"


# ===========================================================================
# Filtering: impact and status
# ===========================================================================


class TestPublishFiltering:
    def test_publish_skips_low_impact(self, tmp_path: Path) -> None:
        """Learnings with impact < 0.7 are skipped."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "low-impact.yaml", _make_learning(impact=0.5))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 1
        assert result["errors"] == 0
        assert result["skipped_reason"] is None

    def test_publish_skips_non_active_resolved(self, tmp_path: Path) -> None:
        """Resolved learnings are skipped."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "resolved.yaml", _make_learning(status="resolved", impact=0.9))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 1

    def test_publish_skips_non_active_obsolete(self, tmp_path: Path) -> None:
        """Obsolete learnings are skipped."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "obsolete.yaml", _make_learning(status="obsolete", impact=0.9))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 1

    def test_publish_sends_source_learning_id(self, tmp_path: Path) -> None:
        """source_learning_id from YAML id field is included in payload."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        learning = _make_learning(impact=0.9)
        learning["id"] = "L-abc12345"
        _write_learning(entries_dir, "with-id.yaml", learning)

        captured_payloads: list[dict[str, object]] = []

        def _fake_post(url: str, payload: dict[str, object], api_key: str = "") -> bool:
            captured_payloads.append(payload)
            return True

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", side_effect=_fake_post),
        ):
            result = publish_learnings()

        assert result["published"] == 1
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["source_learning_id"] == "L-abc12345"

    def test_publish_skips_unchanged_on_second_call(self, tmp_path: Path) -> None:
        """Content-hash tracking: unchanged entries are skipped on subsequent calls."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result1 = publish_learnings()
            result2 = publish_learnings()

        assert result1["published"] == 1
        assert result2["published"] == 0
        assert result2["unchanged"] == 1  # Skipped due to matching content hash

    def test_publish_force_resends_all(self, tmp_path: Path) -> None:
        """force=True ignores content hashes and re-publishes everything."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result1 = publish_learnings()
            result2 = publish_learnings(force=True)

        assert result1["published"] == 1
        assert result2["published"] == 1  # Force re-publishes despite matching hash


# ===========================================================================
# Successful publication
# ===========================================================================


class TestPublishSuccess:
    def test_publish_success(self, tmp_path: Path) -> None:
        """High-impact active learnings are published successfully."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning-a.yaml", _make_learning(impact=0.9))
        _write_learning(entries_dir, "learning-b.yaml", _make_learning(impact=0.8))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] is None

    def test_publish_mixed_impact(self, tmp_path: Path) -> None:
        """Only high-impact learnings are published; low-impact are skipped."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "high.yaml", _make_learning(impact=0.9))
        _write_learning(entries_dir, "low.yaml", _make_learning(impact=0.3))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 1
        assert result["skipped"] == 1


# ===========================================================================
# Network error handling
# ===========================================================================


class TestPublishNetworkError:
    def test_publish_network_error(self, tmp_path: Path) -> None:
        """Network errors count as errors, not exceptions."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=False),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["errors"] == 1
        assert result["skipped_reason"] is None

    def test_publish_exception_in_file_processing(self, tmp_path: Path) -> None:
        """Exceptions during file processing are caught and counted as errors."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)
        # Write a malformed YAML file
        (entries_dir / "bad.yaml").write_text("impact: [not a float", encoding="utf-8")

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        ):
            result = publish_learnings()

        # Should not raise; errors counted
        assert isinstance(result["errors"], int)


# ===========================================================================
# Content anonymization
# ===========================================================================


class TestPublishAnonymization:
    def test_publish_anonymizes_content(self, tmp_path: Path) -> None:
        """strip_pii is called on summary and detail before publishing."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_url="https://api.example.com",
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(
            entries_dir,
            "pii-learning.yaml",
            _make_learning(
                impact=0.9,
                summary="Contact user@example.com for details",
                detail="API key: sk-abcdefghijklmnopqrstuvwx1234567890",
            ),
        )

        captured_payloads: list[dict[str, object]] = []

        def _fake_post(url: str, payload: dict[str, object], api_key: str = "") -> bool:
            captured_payloads.append(payload)
            return True

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", side_effect=_fake_post),
        ):
            result = publish_learnings()

        assert result["published"] == 1
        assert len(captured_payloads) == 1
        payload = captured_payloads[0]
        assert "user@example.com" not in str(payload.get("summary", ""))
        assert "<email>" in str(payload.get("summary", ""))


# ===========================================================================
# Parallel fan-out
# ===========================================================================


class TestPublishParallelFanout:
    def test_publish_fanout_both_urls_attempted(self, tmp_path: Path) -> None:
        """Both platform URLs are attempted in parallel."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_urls=["https://url-a.example.com", "https://url-b.example.com"],
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))

        attempted_urls: list[str] = []

        def _fake_post(url: str, payload: dict[str, object], api_key: str = "") -> bool:
            attempted_urls.append(url)
            return True

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", side_effect=_fake_post),
        ):
            result = publish_learnings()

        assert result["published"] == 1
        assert len(attempted_urls) == 2
        url_hosts = {u.split("/")[2] for u in attempted_urls}
        assert "url-a.example.com" in url_hosts
        assert "url-b.example.com" in url_hosts

    def test_publish_fanout_one_fails_still_publishes(self, tmp_path: Path) -> None:
        """One URL failing does not prevent success via the other."""
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig(
            platform_urls=["https://dead.example.com", "https://live.example.com"],
            platform_telemetry_enabled=True,
        )
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))

        def _fake_post(url: str, payload: dict[str, object], api_key: str = "") -> bool:
            return "live" in url

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", side_effect=_fake_post),
        ):
            result = publish_learnings()

        assert result["published"] == 1
        assert result["errors"] == 0


# ===========================================================================
# _post_learning unit tests
# ===========================================================================


class TestPostLearning:
    def test_post_learning_success(self) -> None:
        """_post_learning returns True on 2xx response."""
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 200

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = _post_learning("https://api.example.com", {"summary": "test"})

        assert result is True

    def test_post_learning_url_error(self) -> None:
        """_post_learning returns False on URLError."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            result = _post_learning("https://api.example.com", {"summary": "test"})

        assert result is False

    def test_post_learning_url_construction(self) -> None:
        """URL is constructed as {platform_url}/v1/learnings."""
        captured_urls: list[str] = []

        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.status = 201

        def fake_urlopen(req: object, timeout: int = 10) -> object:
            import urllib.request as ur

            if isinstance(req, ur.Request):
                captured_urls.append(req.full_url)
            return mock_response

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _post_learning("https://api.example.com/", {"summary": "test"})

        assert captured_urls[0] == "https://api.example.com/v1/learnings"

    def test_post_learning_4xx_returns_false(self) -> None:
        """_post_learning returns False on 4xx HTTP errors."""
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="https://api.example.com/v1/learnings",
                code=422,
                msg="Unprocessable Entity",
                hdrs=MagicMock(),  # type: ignore[arg-type]
                fp=None,
            ),
        ):
            result = _post_learning("https://api.example.com", {"summary": "test"})

        assert result is False
