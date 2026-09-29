"""An older trw-mcp must never overwrite a newer deployed framework generation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from tests._tools_orchestration_support import orch_tools  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _orchestration_helpers as helpers

pytestmark = pytest.mark.unit


def _bundle(fw: str, aa: str, marker: str = "") -> tuple[dict[str, str], TRWConfig]:
    files = {
        "framework.md": f"{fw} — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK\n{marker}\n",
        "aaref.md": f"# AARE-F\n\n**Version**: {aa.lstrip('v')}\n{marker}\n",
    }
    return files, TRWConfig(framework_version=fw, aaref_version=aa)


def _run(monkeypatch: pytest.MonkeyPatch, trw_dir: Path, bundle: tuple[dict[str, str], TRWConfig]) -> dict[str, str]:
    files, config = bundle
    monkeypatch.setattr(helpers, "get_config", lambda: config)
    monkeypatch.setattr(helpers, "_get_bundled_file", lambda name, subdir=None: files.get(name))
    return helpers._deploy_frameworks(trw_dir)


def _tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _rollbacks(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if ".rollback" in p.parts)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


NEW = ("v99.5_TRW", "v9.2.0")
OLD = ("v99.4_TRW", "v9.1.0")


def test_older_bundle_leaves_newer_generation_byte_identical(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    assert _run(monkeypatch, trw_dir, _bundle(*NEW))["status"] == "deployed"
    before = _tree(trw_dir)
    rollbacks = _rollbacks(trw_dir)
    result = _run(monkeypatch, trw_dir, _bundle(*OLD))
    assert result["status"] == "skipped_stale_package"
    assert result["running"] == OLD[0] and result["deployed"] == NEW[0]
    assert "upgrade trw-mcp" in result["nudge"]
    assert "VERSION.yaml stamp framework_version" in result["nudge"]
    assert "TRW_FRAMEWORK_FORCE_DEPLOY=1" in result["nudge"]
    assert _tree(trw_dir) == before
    assert _rollbacks(trw_dir) == rollbacks


def test_older_bundle_repeated_flips_never_grow_rollback(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    _run(monkeypatch, trw_dir, _bundle(*NEW))
    rollbacks = _rollbacks(trw_dir)
    for _ in range(3):
        _run(monkeypatch, trw_dir, _bundle(*OLD))
    assert _rollbacks(trw_dir) == rollbacks


def test_same_version_drift_still_repairs(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    _run(monkeypatch, trw_dir, _bundle(*NEW))
    body = trw_dir / "frameworks" / "FRAMEWORK.md"
    body.write_text(body.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
    assert _run(monkeypatch, trw_dir, _bundle(*NEW))["status"] == "deployed"
    assert "drift" not in body.read_text(encoding="utf-8")


def test_newer_bundle_upgrades(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    _run(monkeypatch, trw_dir, _bundle(*OLD))
    assert _run(monkeypatch, trw_dir, _bundle(*NEW))["status"] == "deployed"
    assert (trw_dir / "frameworks" / "FRAMEWORK.md").read_text(encoding="utf-8").startswith(NEW[0])


def test_newer_pin_in_config_also_blocks(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    (trw_dir / "config.yaml").write_text(f"framework_version: {NEW[0]}\n", encoding="utf-8")
    result = _run(monkeypatch, trw_dir, _bundle(*OLD))
    assert result["status"] == "skipped_stale_package"
    assert "config pin framework_version in .trw/config.yaml" in result["nudge"]
    assert not (trw_dir / "frameworks" / "FRAMEWORK.md").exists()


@pytest.mark.parametrize("bad", ["garbage", "vX.Y_TRW", ""])
def test_unparseable_deployed_version_keeps_today_behaviour(
    monkeypatch: pytest.MonkeyPatch, trw_dir: Path, bad: str
) -> None:
    _run(monkeypatch, trw_dir, _bundle(*NEW))
    stamp = trw_dir / "frameworks" / "VERSION.yaml"
    stamp.write_text(f"framework_version: {bad}\naaref_version: {bad}\n", encoding="utf-8")
    body = trw_dir / "frameworks" / "FRAMEWORK.md"
    body.write_text("no version here\n", encoding="utf-8")
    aaref = trw_dir / "frameworks" / "AARE-F-FRAMEWORK.md"
    aaref.write_text("no version here\n", encoding="utf-8")
    assert _run(monkeypatch, trw_dir, _bundle(*OLD))["status"] == "deployed"


def test_nudge_reaches_trw_init_result(orch_tools: dict[str, Any], tmp_path: Path) -> None:
    first = orch_tools["trw_init"].fn(task_name="guard-one")
    assert "framework_nudge" not in first
    frameworks = tmp_path / ".trw" / "frameworks"
    stamp = frameworks / "VERSION.yaml"
    stamp.write_text("framework_version: v999.0_TRW\naaref_version: v99.0.0\n", encoding="utf-8")
    before = _tree(frameworks)
    second = orch_tools["trw_init"].fn(task_name="guard-two")
    assert second["framework_deploy"] == "skipped_stale_package"
    assert "upgrade trw-mcp" in second["framework_nudge"]
    assert _tree(frameworks) == before


def test_force_env_bypasses_guard(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    _run(monkeypatch, trw_dir, _bundle(*NEW))
    monkeypatch.setenv("TRW_FRAMEWORK_FORCE_DEPLOY", "1")
    assert _run(monkeypatch, trw_dir, _bundle(*OLD))["status"] == "deployed"
    assert (trw_dir / "frameworks" / "FRAMEWORK.md").read_text(encoding="utf-8").startswith(OLD[0])


@pytest.mark.parametrize(
    ("a", "b", "expected_newer"),
    [("v88.5.0", "v88.5_TRW", False), ("v88.5_TRW", "v88.5.0", False), ("v88.5.1", "v88.5_TRW", True)],
)
def test_version_key_pads_trailing_zeros(a: str, b: str, expected_newer: bool) -> None:
    from trw_mcp.framework_integrity import _version_key

    ka, kb = _version_key(a), _version_key(b)
    assert ka is not None and kb is not None
    assert (ka > kb) is expected_newer
    assert (ka == kb) is (not expected_newer)


def test_unreadable_stamp_is_logged(
    monkeypatch: pytest.MonkeyPatch, trw_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _run(monkeypatch, trw_dir, _bundle(*NEW))
    stamp = trw_dir / "frameworks" / "VERSION.yaml"
    stamp.write_bytes(b"\xff\xfe\x00bad")
    with caplog.at_level("WARNING"):
        _run(monkeypatch, trw_dir, _bundle(*OLD))
    assert "could not read" in caplog.text


def test_update_project_reports_skip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.bootstrap import _utils

    frameworks = tmp_path / ".trw" / "frameworks"
    frameworks.mkdir(parents=True)
    (frameworks / "VERSION.yaml").write_text(
        "framework_version: v999.0_TRW\naaref_version: v99.0.0\n", encoding="utf-8"
    )
    before = _tree(frameworks)
    result: dict[str, list[str]] = {"created": [], "updated": [], "skipped": [], "errors": [], "warnings": []}
    _utils._write_version_yaml(tmp_path, result)
    assert any("Framework deploy skipped" in w and "upgrade trw-mcp" in w for w in result["warnings"])
    assert _tree(frameworks) == before
    assert result["created"] == [] and result["updated"] == []


@pytest.mark.parametrize("huge", ["v99999_TRW", "v1.99999_TRW"])
def test_oversized_version_component_is_unparseable(monkeypatch: pytest.MonkeyPatch, trw_dir: Path, huge: str) -> None:
    from trw_mcp.framework_integrity import _version_key

    assert _version_key(huge) is None
    (trw_dir / "config.yaml").write_text(f"framework_version: {huge}\n", encoding="utf-8")
    assert _run(monkeypatch, trw_dir, _bundle(*OLD))["status"] == "deployed"
