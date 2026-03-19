"""Autonomous update pipeline — PRD-INFRA-014 Phase 2C.

Checks for available updates on session start and optionally
installs them. Fail-open: network errors never block session start.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config

logger = structlog.get_logger(__name__)

# Cache duration: check at most once per 24h
_VERSION_CACHE_HOURS = 24


def get_installed_version() -> str:
    """Return the currently installed trw-mcp version."""
    try:
        from trw_mcp import __version__

        return __version__
    except (ImportError, AttributeError):
        return "0.0.0"


def check_for_update() -> dict[str, object]:
    """Check if a newer version is available.

    Returns:
        {available: bool, current: str, latest: str, channel: str, advisory: str | None}
    Fail-open: returns available=False on any error.
    """
    cfg = get_config()
    current = get_installed_version()

    urls = cfg.effective_platform_urls
    if not urls:
        return {
            "available": False,
            "current": current,
            "latest": current,
            "channel": cfg.update_channel,
            "advisory": None,
        }

    # First-success: try each backend until one responds
    for base_url in urls:
        try:
            url = f"{base_url.rstrip('/')}/v1/releases/latest?channel={cfg.update_channel}"
            headers: dict[str, str] = {}
            _key = cfg.platform_api_key.get_secret_value()
            if _key:
                headers["Authorization"] = f"Bearer {_key}"
            req = urllib.request.Request(url, method="GET", headers=headers)  # noqa: S310 — URL from cfg.effective_platform_urls (operator config, not user input)
            with urllib.request.urlopen(req, timeout=3) as response:  # noqa: S310 — see Request comment above
                if 200 <= response.status < 300:
                    data: dict[str, object] = json.loads(response.read().decode("utf-8"))
                    latest = str(data.get("version", current))
                    available = _compare_versions(current, latest)
                    advisory: str | None = f"TRW v{latest} available (you have v{current}). " if available else None
                    return {
                        "available": available,
                        "current": current,
                        "latest": latest,
                        "channel": cfg.update_channel,
                        "advisory": advisory,
                    }
        except (  # noqa: PERF203
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            json.JSONDecodeError,
            KeyError,
        ):
            logger.debug("version_check_failed", base_url=base_url)

    return {
        "available": False,
        "current": current,
        "latest": current,
        "channel": cfg.update_channel,
        "advisory": None,
    }


def _parse_version(version: str) -> tuple[int, int, int]:
    """Parse version string to semver tuple (3 parts), raise on failure."""
    return tuple(int(x) for x in version.split(".")[:3])  # type: ignore[return-value]


def _compare_versions(current: str, latest: str) -> bool:
    """Return True if latest is newer than current using semver tuple comparison."""
    try:
        return _parse_version(latest) > _parse_version(current)
    except (ValueError, TypeError):
        return False


def _is_compatible(current: str, min_version: str) -> bool:
    """Return True if current version meets minimum compatibility requirement."""
    try:
        return _parse_version(current) >= _parse_version(min_version)
    except (ValueError, TypeError):
        return True  # Fail-open: assume compatible if parsing fails


def download_release_artifact(
    artifact_url: str,
    expected_checksum: str | None = None,
) -> Path | None:
    """Download and verify a release artifact.

    Args:
        artifact_url: URL to the .tar.gz bundle
        expected_checksum: SHA-256 hex digest to verify against

    Returns:
        Path to extracted data/ directory, or None on failure.
    Fail-open: returns None on any error.
    """
    import hashlib
    import tarfile
    import tempfile

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="trw-upgrade-"))
        archive_path = tmp_dir / "release.tar.gz"

        # Download
        cfg = get_config()
        headers: dict[str, str] = {}
        _key = cfg.platform_api_key.get_secret_value()
        if _key:
            headers["Authorization"] = f"Bearer {_key}"
        req = urllib.request.Request(artifact_url, method="GET", headers=headers)  # noqa: S310 — artifact_url comes from the backend API response (operator-controlled platform); checksum is verified after download
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310 — see Request comment above
            archive_path.write_bytes(response.read())

        # Mandatory checksum verification (PRD-QUAL-042-FR08)
        if not expected_checksum:
            logger.warning("checksum_missing", url=artifact_url)
            return None
        h = hashlib.sha256()
        with open(archive_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected_checksum:
            logger.warning(
                "checksum_mismatch",
                expected=expected_checksum,
                actual=actual,
            )
            return None

        # Extract
        with tarfile.open(archive_path, "r:gz") as tar:
            # Security: prevent path traversal
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name:
                    logger.warning("suspicious_archive_member", member_name=member.name)
                    return None
            tar.extractall(tmp_dir, filter="data")

        data_dir = tmp_dir / "data"
        if data_dir.is_dir():
            return data_dir

        logger.warning("archive_missing_data_dir")
        return None

    except Exception:  # justified: boundary, artifact download from remote may fail for many reasons
        logger.debug("artifact_download_failed", exc_info=True)
        return None


def perform_upgrade(update_info: dict[str, object]) -> dict[str, object]:
    """Download and apply a TRW update.

    Args:
        update_info: Result from check_for_update() with artifact details.

    Returns:
        {applied: bool, version: str, details: str}
    Fail-open: returns applied=False on any error.
    """
    import fcntl

    cfg = get_config()
    target_dir = Path.cwd()
    lock_path = target_dir / cfg.trw_dir / "update.lock"

    try:
        # Acquire exclusive lock
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as lock_fd:
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return {
                    "applied": False,
                    "version": str(update_info.get("latest", "")),
                    "details": "Another upgrade is in progress",
                }

            try:
                # Get artifact info from backend
                artifact_info = _fetch_artifact_info(str(update_info.get("latest", "")))
                if artifact_info is None:
                    return {
                        "applied": False,
                        "version": str(update_info.get("latest", "")),
                        "details": "Could not fetch artifact info",
                    }

                artifact_url = str(artifact_info.get("artifact_url", ""))
                checksum = artifact_info.get("artifact_checksum")
                min_version = str(artifact_info.get("min_compatible_version", "0.0.0") or "0.0.0")

                # Check compatibility
                current = get_installed_version()
                if not _is_compatible(current, min_version):
                    return {
                        "applied": False,
                        "version": str(update_info.get("latest", "")),
                        "details": f"Current v{current} below min compatible v{min_version}",
                    }

                # Download and verify
                data_dir = download_release_artifact(
                    artifact_url,
                    expected_checksum=str(checksum) if checksum else None,
                )
                if data_dir is None:
                    return {
                        "applied": False,
                        "version": str(update_info.get("latest", "")),
                        "details": "Download or verification failed",
                    }

                # Apply update
                from trw_mcp.bootstrap import update_project

                result = update_project(target_dir, data_dir=data_dir)
                errors = result.get("errors", [])
                if errors:
                    return {
                        "applied": False,
                        "version": str(update_info.get("latest", "")),
                        "details": f"Update errors: {'; '.join(str(e) for e in errors)}",
                    }

                total = len(result.get("updated", [])) + len(result.get("created", []))
                return {
                    "applied": True,
                    "version": str(update_info.get("latest", "")),
                    "details": f"Updated {total} files",
                }
            finally:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    lock_path.unlink(missing_ok=True)

    except Exception:  # justified: boundary, auto-upgrade is best-effort: fail-open upgrade boundary
        logger.debug("auto_upgrade_failed", exc_info=True)
        return {
            "applied": False,
            "version": str(update_info.get("latest", "")),
            "details": "Unexpected error during upgrade",
        }


def _fetch_artifact_info(version: str) -> dict[str, object] | None:
    """Fetch artifact download info for a specific release version."""
    cfg = get_config()
    urls = cfg.effective_platform_urls
    if not urls:
        return None

    for base_url in urls:
        try:
            url = f"{base_url.rstrip('/')}/v1/releases/{version}/artifact"
            headers: dict[str, str] = {}
            _key = cfg.platform_api_key.get_secret_value()
            if _key:
                headers["Authorization"] = f"Bearer {_key}"
            req = urllib.request.Request(url, method="GET", headers=headers)  # noqa: S310 — URL from cfg.effective_platform_urls (operator config, not user input)
            with urllib.request.urlopen(req, timeout=5) as response:  # noqa: S310 — see Request comment above
                if 200 <= response.status < 300:
                    result: dict[str, object] = json.loads(response.read().decode("utf-8"))
                    return result
        except (  # noqa: PERF203
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            json.JSONDecodeError,
        ):
            continue

    return None
