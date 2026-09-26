"""Filtering and payload-shape tests for trw_mcp.telemetry.publisher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
from trw_mcp.telemetry.publisher import publish_learnings


class TestPublishFiltering:
    def test_publish_skips_low_impact(self, tmp_path: Path) -> None:
        """Learnings with impact < 0.5 are skipped (PRD-FIX-052-FR06: threshold is now 0.5)."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "low-impact.yaml", _make_learning(impact=0.4))

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

    def test_publish_threshold_lowered_publishes_mid_impact(self, tmp_path: Path) -> None:
        """PRD-FIX-052-FR06: entries with impact 0.5-0.69 are now published (threshold lowered from 0.7)."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "mid-a.yaml", _make_learning(impact=0.5))
        _write_learning(entries_dir, "mid-b.yaml", _make_learning(impact=0.65))

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 2, "entries with impact >= 0.5 should now be published"
        assert result["skipped"] == 0

    def test_publish_includes_resolved(self, tmp_path: Path) -> None:
        """Resolved learnings are published with status preserved."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "resolved.yaml", _make_learning(status="resolved", impact=0.9))

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
        assert result["skipped"] == 0
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["status"] == "resolved"

    def test_publish_body_carries_no_embedding_key(self, tmp_path: Path) -> None:
        """OD6: the telemetry publisher sends no vectors -- the body has no "embedding" key at all."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "no-vector.yaml", _make_learning(impact=0.9))

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
        assert "embedding" not in captured_payloads[0]

    def test_publish_includes_obsolete(self, tmp_path: Path) -> None:
        """Obsolete learnings are published with status preserved."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "obsolete.yaml", _make_learning(status="obsolete", impact=0.9))

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
        assert result["skipped"] == 0
        assert len(captured_payloads) == 1
        assert captured_payloads[0]["status"] == "obsolete"

    def test_publish_sends_source_learning_id(self, tmp_path: Path) -> None:
        """source_learning_id from YAML id field is included in payload."""
        cfg = _make_config()
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

    def test_publish_redacts_secret_bearing_tags(self, tmp_path: Path) -> None:
        """codex-a R2-014 P1: tags are free text from trw_learn, so a pasted key must not reach the POST body."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        learning = _make_learning(impact=0.9)
        secret = "sk_live_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
        learning["tags"] = ["telemetry", secret]
        _write_learning(trw_dir / "learnings" / "entries", "secret-tag.yaml", learning)
        captured_payloads: list[dict[str, object]] = []

        def _fake_post(url: str, payload: dict[str, object], api_key: str = "") -> bool:
            captured_payloads.append(payload)
            return True

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", side_effect=_fake_post),
        ):
            publish_learnings()

        [payload] = captured_payloads
        tags = payload["tags"]
        assert isinstance(tags, list) and tags[0] == "telemetry"
        assert secret not in str(payload)

    def test_publish_sends_status_in_payload(self, tmp_path: Path) -> None:
        """Status field is included in the payload for active learnings."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "active.yaml", _make_learning(status="active", impact=0.9))

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
        assert captured_payloads[0]["status"] == "active"

    def test_publish_skips_unchanged_on_second_call(self, tmp_path: Path) -> None:
        """Content-hash tracking: unchanged entries are skipped on subsequent calls."""
        cfg = _make_config()
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
        assert result2["unchanged"] == 1

    def test_publish_force_resends_all(self, tmp_path: Path) -> None:
        """force=True ignores content hashes and re-publishes everything."""
        cfg = _make_config()
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
        assert result2["published"] == 1
