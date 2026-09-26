"""Tests for auto_upgrade credential-egress host validation (W38 security P1).

The platform bearer API key must NEVER be attached to a network request whose
target host is not on the trusted-host allowlist (default platform host, or a
host from USER-level ``~/.trw/config.yaml`` / env — never a project's tracked
``.trw/config.yaml``). These tests assert BEHAVIOR (was the Authorization
header attached / suppressed) for:

  (a) version-check (check_for_update) to an http:// non-localhost host, and
      to an https host that is NOT on the trusted allowlist (the actual
      vulnerability: a project's tracked platform_url used to auto-trust ANY
      https host)
  (b) artifact download whose artifact_url host is untrusted (e.g. a poisoned
      URL OR a legitimate presigned-S3 URL — both must NOT receive the bearer)
  (c) the legitimate https trusted-host artifact download (bearer attached)
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
import structlog

from tests._auto_upgrade_test_support import (
    _make_tar_gz_bytes,
    _mock_httpx_client,
    _mock_httpx_response,
    reset_cfg,  # noqa: F401
)
from trw_mcp.models.config import TRWConfig, _reset_config
from trw_mcp.state import auto_upgrade
from trw_mcp.state._platform_trust import bearer_allowed_for as _bearer_allowed_for
from trw_mcp.state.auto_upgrade import (
    check_for_update,
    download_release_artifact,
)

# ---------------------------------------------------------------------------
# _bearer_allowed_for — the shared trust gate, as seen from auto_upgrade
# ---------------------------------------------------------------------------


class TestBearerAllowedFor:
    def test_https_trusted_default_host_allowed(self) -> None:
        assert _bearer_allowed_for("https://api.trwframework.com/v1/releases/x")

    def test_https_untrusted_host_refused(self) -> None:
        # A project's tracked config CANNOT make an arbitrary host trusted —
        # this is the actual W38 vulnerability: any https host used to pass.
        assert not _bearer_allowed_for("https://example.com/v1/releases/x")

    def test_https_presigned_s3_host_refused(self) -> None:
        assert not _bearer_allowed_for("https://trw-releases.s3.amazonaws.com/x?X-Amz-Signature=abc")

    def test_http_non_localhost_refused(self) -> None:
        assert not _bearer_allowed_for("http://attacker.host/v1/releases/latest")

    def test_http_localhost_allowed_for_dev(self) -> None:
        assert _bearer_allowed_for("http://127.0.0.1:5002/v1/releases/latest")
        assert _bearer_allowed_for("http://localhost:5002/v1/releases/latest")

    def test_case_insensitive_host_match(self) -> None:
        assert _bearer_allowed_for("https://API.TRWframework.com/x")


# ---------------------------------------------------------------------------
# (a) version-check to an untrusted host → no Authorization
# ---------------------------------------------------------------------------


class TestCheckForUpdateEgress:
    def test_http_non_localhost_withholds_bearer(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="http://attacker.host",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            with structlog.testing.capture_logs() as logs:
                check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers
        assert any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)

    def test_https_untrusted_host_withholds_bearer(self) -> None:
        """The core W38 regression: an https project-configured host that is
        NOT the trusted platform host must not receive the bearer."""
        _reset_config(
            TRWConfig(
                platform_url="https://example.com",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            with structlog.testing.capture_logs() as logs:
                check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers
        assert any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)

    def test_https_trusted_host_attaches_bearer(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"

    def test_http_localhost_attaches_bearer(self) -> None:
        _reset_config(
            TRWConfig(
                platform_url="http://127.0.0.1:5002",
                platform_api_key="secret-key",
            )
        )
        resp = _mock_httpx_response(json_data={"version": "9.9.9"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client):
            check_for_update()

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"


# ---------------------------------------------------------------------------
# (b) artifact_url host untrusted → bearer NOT attached
# (c) legit https trusted host → bearer attached + works
# ---------------------------------------------------------------------------


class TestDownloadArtifactEgress:
    def _archive_client(self, archive_bytes: bytes):  # type: ignore[no-untyped-def]
        resp = _mock_httpx_response(content=archive_bytes)
        return _mock_httpx_client(resp)

    def test_presigned_s3_host_withholds_bearer(self, tmp_path: Path) -> None:
        """A presigned S3 URL is a DIFFERENT host carrying its own query-string
        auth — the platform bearer must NOT be attached."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                with structlog.testing.capture_logs() as logs:
                    result = download_release_artifact(
                        "https://trw-releases.s3.amazonaws.com/v1/release.tar.gz?X-Amz-Signature=abc",
                        expected_checksum=checksum,
                    )

        assert result is not None  # download still succeeds (S3 auth is in the URL)
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers
        assert any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)

    def test_attacker_host_withholds_bearer(self, tmp_path: Path) -> None:
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact(
                    "https://attacker.host/release.tar.gz",
                    expected_checksum=checksum,
                )

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers

    def test_trusted_host_https_attaches_bearer(self, tmp_path: Path) -> None:
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(
            TRWConfig(
                platform_url="https://api.trwframework.com",
                platform_api_key="secret-key",
            )
        )
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                result = download_release_artifact(
                    "https://api.trwframework.com/v1/release.tar.gz",
                    expected_checksum=checksum,
                )

        assert result is not None
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer secret-key"

    def test_no_platform_urls_configured_withholds_bearer(self, tmp_path: Path) -> None:
        """Even with no configured platform_urls, the trusted-host allowlist
        still includes the built-in default host, so the default host still
        gets the bearer — an untrusted host never does regardless."""
        archive_bytes = _make_tar_gz_bytes({"data/f.txt": b"ok"})
        checksum = hashlib.sha256(archive_bytes).hexdigest()
        client = self._archive_client(archive_bytes)
        _reset_config(TRWConfig(platform_url="", platform_api_key="secret-key"))
        with patch("httpx.Client", return_value=client):
            with patch("tempfile.mkdtemp", return_value=str(tmp_path / "dl")):
                (tmp_path / "dl").mkdir()
                download_release_artifact(
                    "https://attacker.host/v1/release.tar.gz",
                    expected_checksum=checksum,
                )

        headers = client.get.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# _fetch_artifact_info — the release-artifact lookup is gated like the others
# ---------------------------------------------------------------------------


class TestFetchArtifactInfoEgress:
    @pytest.mark.parametrize(
        ("platform_url", "expected_auth"),
        [
            ("https://example.com", None),
            ("http://attacker.host", None),
            ("https://api.trwframework.com", "Bearer secret-key"),
            ("http://127.0.0.1:5002", "Bearer secret-key"),
        ],
    )
    def test_bearer_follows_the_trust_gate(self, platform_url: str, expected_auth: str | None) -> None:
        _reset_config(TRWConfig(platform_url=platform_url, platform_api_key="secret-key"))
        resp = _mock_httpx_response(json_data={"artifact_url": "https://x/a.tar.gz", "checksum": "c"})
        client = _mock_httpx_client(resp)
        with patch("httpx.Client", return_value=client), structlog.testing.capture_logs() as logs:
            result = auto_upgrade._fetch_artifact_info("9.9.9")

        assert result == {"artifact_url": "https://x/a.tar.gz", "checksum": "c"}
        assert client.get.call_args.args[0] == f"{platform_url}/v1/releases/9.9.9/artifact"
        headers = client.get.call_args.kwargs.get("headers") or {}
        assert headers.get("Authorization") == expected_auth
        withheld = any(e.get("event") == "credential_withheld_untrusted_host" for e in logs)
        assert withheld is (expected_auth is None)
