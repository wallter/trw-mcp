"""Tests for auto_upgrade perform_upgrade behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._auto_upgrade_test_support import reset_cfg  # noqa: F401
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state.auto_upgrade import perform_upgrade


class TestPerformUpgrade:
    """Tests for the perform_upgrade function covering all branches."""

    def _setup_config(self, tmp_path: Path, api_key: str = "") -> None:
        """Reset config with a platform URL pointing to a temp project."""
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key=api_key,
                update_channel="latest",
            )
        )

    def test_lock_contention_returns_not_applied(self, tmp_path: Path) -> None:
        """When flock raises OSError, returns 'Another upgrade is in progress'."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        with patch("os.getcwd", return_value=str(tmp_path)):
            with patch.object(Path, "cwd", return_value=tmp_path):
                with patch("fcntl.flock", side_effect=OSError("locked")):
                    result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Another upgrade" in str(result["details"])

    def test_artifact_info_none_returns_not_applied(self, tmp_path: Path) -> None:
        """When _fetch_artifact_info returns None."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=None):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Could not fetch artifact info" in str(result["details"])

    def test_incompatible_version_returns_not_applied(self, tmp_path: Path) -> None:
        """When current version is below min_compatible_version."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "99.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "below min compatible" in str(result["details"])

    def test_download_failure_returns_not_applied(self, tmp_path: Path) -> None:
        """When download_release_artifact returns None."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=None):
                    result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Download or verification failed" in str(result["details"])

    def test_update_project_errors_returns_not_applied(self, tmp_path: Path) -> None:
        """When update_project returns errors."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": ["permission denied"],
                            "updated": [],
                            "created": [],
                        },
                    ):
                        result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Update errors" in str(result["details"])
        assert "permission denied" in str(result["details"])

    def test_success_returns_applied(self, tmp_path: Path) -> None:
        """Happy path: download, verify, apply — returns applied=True."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": [],
                            "updated": ["a.txt", "b.txt"],
                            "created": ["c.txt"],
                        },
                    ):
                        result = perform_upgrade(update_info)

        assert result["applied"] is True
        assert result["version"] == "2.0.0"
        assert "3 files" in str(result["details"])

    def test_unexpected_exception_returns_not_applied(self, tmp_path: Path) -> None:
        """Outer except: any unexpected error returns applied=False."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch.object(Path, "mkdir", side_effect=RuntimeError("unexpected")):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "Unexpected error" in str(result["details"])

    def test_lock_cleanup_on_success(self, tmp_path: Path) -> None:
        """Lock file is cleaned up after successful upgrade."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": [],
                            "updated": ["a.txt"],
                            "created": [],
                        },
                    ):
                        result = perform_upgrade(update_info)

        assert result["applied"] is True
        lock_path = tmp_path / ".trw" / "update.lock"
        assert not lock_path.exists()

    def test_artifact_checksum_forwarded(self, tmp_path: Path) -> None:
        """When artifact_info has a checksum, it's forwarded to download_release_artifact."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": "abc123def456",
            "min_compatible_version": "0.0.0",
        }

        captured_args: list[tuple[object, ...]] = []
        captured_kwargs: list[dict[str, object]] = []

        def fake_download(*args: object, **kwargs: object) -> None:
            captured_args.append(args)
            captured_kwargs.append(kwargs)
            return None

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", side_effect=fake_download):
                    perform_upgrade(update_info)

        assert len(captured_args) == 1
        assert captured_args[0][0] == "https://dl.example.com/v2.tar.gz"
        assert captured_kwargs[0]["expected_checksum"] == "abc123def456"

    def test_lock_unlink_oserror_suppressed(self, tmp_path: Path) -> None:
        """OSError during lock_path.unlink is silently suppressed."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        original_unlink = Path.unlink

        def unlink_that_fails(self_path: Path, *args: object, **kwargs: object) -> None:
            if self_path.name == "update.lock":
                raise OSError("permission denied")
            return original_unlink(self_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": [],
                            "updated": ["a.txt"],
                            "created": [],
                        },
                    ):
                        with patch.object(Path, "unlink", unlink_that_fails):
                            result = perform_upgrade(update_info)

        assert result["applied"] is True

    def test_no_checksum_passed_as_none(self, tmp_path: Path) -> None:
        """When artifact_checksum is None, expected_checksum is None."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        captured_kwargs: list[dict[str, object]] = []

        def fake_download(*args: object, **kwargs: object) -> None:
            captured_kwargs.append(kwargs)
            return None

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", side_effect=fake_download):
                    perform_upgrade(update_info)

        assert captured_kwargs[0]["expected_checksum"] is None


class TestPerformUpgradeEdge:
    """Edge cases for perform_upgrade."""

    def _setup_config(self, tmp_path: Path, api_key: str = "") -> None:
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key=api_key,
                update_channel="latest",
            )
        )

    def test_missing_latest_key_in_update_info(self, tmp_path: Path) -> None:
        """When update_info has no 'latest' key, version defaults to empty string."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {}

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=None):
                result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert result["version"] == ""

    def test_min_compatible_version_none_defaults(self, tmp_path: Path) -> None:
        """When min_compatible_version is None in artifact_info, defaults to compatible."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": None,
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": [],
                            "updated": ["a.txt"],
                            "created": [],
                        },
                    ):
                        result = perform_upgrade(update_info)

        assert result["applied"] is True

    def test_update_project_with_no_files_reports_zero(self, tmp_path: Path) -> None:
        """When update_project returns empty updated and created lists."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": [],
                            "updated": [],
                            "created": [],
                        },
                    ):
                        result = perform_upgrade(update_info)

        assert result["applied"] is True
        assert "0 files" in str(result["details"])

    def test_multiple_update_errors_joined(self, tmp_path: Path) -> None:
        """When update_project returns multiple errors, they are semicolon-joined."""
        self._setup_config(tmp_path)
        update_info: dict[str, object] = {"latest": "2.0.0"}
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        artifact_info: dict[str, object] = {
            "artifact_url": "https://dl.example.com/v2.tar.gz",
            "artifact_checksum": None,
            "min_compatible_version": "0.0.0",
        }

        with patch.object(Path, "cwd", return_value=tmp_path):
            with patch("trw_mcp.state.auto_upgrade._fetch_artifact_info", return_value=artifact_info):
                with patch("trw_mcp.state.auto_upgrade.download_release_artifact", return_value=data_dir):
                    with patch(
                        "trw_mcp.bootstrap.update_project",
                        return_value={
                            "errors": ["err1", "err2", "err3"],
                            "updated": [],
                            "created": [],
                        },
                    ):
                        result = perform_upgrade(update_info)

        assert result["applied"] is False
        assert "err1" in str(result["details"])
        assert "err2" in str(result["details"])
        assert "err3" in str(result["details"])
        assert "; " in str(result["details"])
