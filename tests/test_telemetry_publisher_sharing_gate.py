"""PRD-SEC-004-FR05: learning-content publishing is gated by the separate
learning_sharing_enabled consent flag, independent of anonymous usage telemetry.

These are zero-POST behavior tests (not existence tests): they assert that the
network transport (_post_learning) is NEVER invoked when sharing is disabled,
even with platform telemetry enabled and learning entries present on disk.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
from trw_mcp.telemetry.publisher import publish_learnings


def _setup_entries(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))
    return trw_dir


def test_content_requires_sharing_flag_zero_post_when_disabled(tmp_path: Path) -> None:
    """sharing OFF + telemetry ON + entries present => zero POST, no content egress."""
    cfg = _make_config(platform_telemetry_enabled=True, learning_sharing_enabled=False)
    trw_dir = _setup_entries(tmp_path)

    with (
        patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
        patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.telemetry.publisher._post_learning") as post_mock,
    ):
        result = publish_learnings()

    post_mock.assert_not_called()
    assert result["published"] == 0
    assert result["skipped_reason"] == "offline_mode"


def test_content_publishes_when_sharing_enabled(tmp_path: Path) -> None:
    """sharing ON + entries present => content publishes (POST invoked)."""
    cfg = _make_config(platform_telemetry_enabled=True, learning_sharing_enabled=True)
    trw_dir = _setup_entries(tmp_path)

    with (
        patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
        patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.telemetry.publisher._post_learning", return_value=True) as post_mock,
    ):
        result = publish_learnings()

    post_mock.assert_called()
    assert result["published"] == 1
    assert result["errors"] == 0


def test_sharing_enabled_but_telemetry_disabled_still_publishes(tmp_path: Path) -> None:
    """The two consents are independent: sharing ON publishes even if usage
    telemetry is OFF (no implicit coupling back to platform_telemetry_enabled)."""
    cfg = _make_config(platform_telemetry_enabled=False, learning_sharing_enabled=True)
    trw_dir = _setup_entries(tmp_path)

    with (
        patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
        patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.telemetry.publisher._post_learning", return_value=True) as post_mock,
    ):
        result = publish_learnings()

    post_mock.assert_called()
    assert result["published"] == 1


def test_default_config_zero_post(tmp_path: Path) -> None:
    """Fresh default TRWConfig (sharing default false) => zero POST."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig(platform_url="https://api.example.com")
    trw_dir = _setup_entries(tmp_path)

    assert cfg.learning_sharing_enabled is False

    with (
        patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
        patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.telemetry.publisher._post_learning") as post_mock,
    ):
        result = publish_learnings()

    post_mock.assert_not_called()
    assert result["skipped_reason"] == "offline_mode"
