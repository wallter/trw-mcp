"""PRD-INFRA-150 FR02 — S3 installer-artifact version-currency check.

The release/publish flow must verify the served installer artifact version
matches the current ``trw-mcp`` PyPI version, blocking (or alerting, per the
``TRW_INSTALLER_VERSION_CHECK_MODE`` knob) on a stale artifact. The operator
report saw an artifact built from 0.54.0 served while PyPI was 0.55.x.

The check is a pure function over two version strings + a mode + a PyPI-lookup
seam (so tests run with no network). ``packaging.version`` semantics (FR04).
NFR06: structured output never contains an auth token or pre-signed URL query.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_installer.py"


def _load_build_installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_installer_currency", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build() -> ModuleType:
    return _load_build_installer()


def test_mode_knob_default_is_block(build: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR05: default mode is 'block' when the env knob is unset."""
    monkeypatch.delenv("TRW_INSTALLER_VERSION_CHECK_MODE", raising=False)
    assert build.version_check_mode() == "block"


def test_mode_knob_reads_env(build: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_INSTALLER_VERSION_CHECK_MODE", "advisory")
    assert build.version_check_mode() == "advisory"


def test_mode_knob_invalid_falls_back_to_block(build: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_INSTALLER_VERSION_CHECK_MODE", "nonsense")
    assert build.version_check_mode() == "block"


def test_stale_artifact_blocks(build: ModuleType) -> None:
    """PyPI 0.55.17, artifact 0.54.0, mode block -> rc != 0, status=stale-artifact."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.54.0",
        pypi_version="0.55.17",
        mode="block",
    )
    assert rc != 0
    assert record["status"] == "stale-artifact"
    assert record["pypi"] == "0.55.17"
    assert record["artifact"] == "0.54.0"


def test_current_artifact_passes(build: ModuleType) -> None:
    """artifact == PyPI -> status=current, rc 0."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.55.17",
        pypi_version="0.55.17",
        mode="block",
    )
    assert rc == 0
    assert record["status"] == "current"


def test_artifact_ahead_passes(build: ModuleType) -> None:
    """artifact strictly newer than PyPI (release-in-flight) -> current, rc 0."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.55.18",
        pypi_version="0.55.17",
        mode="block",
    )
    assert rc == 0
    assert record["status"] == "current"


def test_advisory_mode_warns_not_blocks(build: ModuleType) -> None:
    """stale + advisory -> rc 0 (alert only), status still stale-artifact."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.54.0",
        pypi_version="0.55.17",
        mode="advisory",
    )
    assert rc == 0
    assert record["status"] == "stale-artifact"


def test_pypi_unreachable_advisory(build: ModuleType) -> None:
    """NFR02: PyPI lookup unavailable (None) -> advisory, no hard block."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.54.0",
        pypi_version=None,
        mode="block",
    )
    assert rc == 0
    assert record["status"] == "pypi-unreachable"


def test_semantic_compare_in_currency(build: ModuleType) -> None:
    """FR04: 0.55.10 vs 0.55.9 -> current (semantic, not lexicographic)."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.55.10",
        pypi_version="0.55.9",
        mode="block",
    )
    assert rc == 0
    assert record["status"] == "current"


def test_no_token_in_output(build: ModuleType) -> None:
    """NFR06: the structured record carries only versions + status — no secrets."""
    rc, record = build.verify_artifact_version_currency(
        artifact_version="0.54.0",
        pypi_version="0.55.17",
        mode="block",
    )
    assert set(record.keys()) == {"pypi", "artifact", "status"}
    blob = " ".join(str(v) for v in record.values())
    assert "X-Amz-" not in blob
    assert "token" not in blob.lower()
    assert "Signature" not in blob
