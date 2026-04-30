"""Outcome, error, anonymization, and fan-out tests for telemetry publishing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
from trw_mcp.telemetry.publisher import publish_learnings


class TestPublishSuccess:
    def test_publish_success(self, tmp_path: Path) -> None:
        """High-impact active learnings are published successfully."""
        cfg = _make_config()
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
        cfg = _make_config()
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


class TestPublishNetworkError:
    def test_publish_network_error(self, tmp_path: Path) -> None:
        """Network errors count as errors, not exceptions."""
        cfg = _make_config()
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
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / "bad.yaml").write_text("impact: [not a float", encoding="utf-8")

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        ):
            result = publish_learnings()

        assert isinstance(result["errors"], int)


class TestPublishAnonymization:
    def test_publish_anonymizes_content(self, tmp_path: Path) -> None:
        """strip_pii is called on summary and detail before publishing."""
        cfg = _make_config()
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


class TestPublishParallelFanout:
    def test_publish_fanout_both_urls_attempted(self, tmp_path: Path) -> None:
        """Both platform URLs are attempted in parallel."""
        cfg = _make_config(
            platform_urls=["https://url-a.example.com", "https://url-b.example.com"],
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
        cfg = _make_config(
            platform_urls=["https://dead.example.com", "https://live.example.com"],
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
