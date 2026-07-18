"""Regression tests for the semantic canon-version scan."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_checker() -> ModuleType:
    path = Path(__file__).parents[2] / "scripts/check-canon-version-surfaces.py"
    spec = importlib.util.spec_from_file_location("check_canon_version_surfaces", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker() -> ModuleType:
    return _load_checker()


def _fixture(
    tmp_path: Path,
    *,
    usage: str,
    body: str,
    selector: str | None = None,
    expected_value: str | None = None,
    rationale: str | None = None,
) -> Path:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw/config.yaml").write_text(
        "framework_version: v26.1_TRW\naaref_version: v3.2.0\n",
        encoding="utf-8",
    )
    (tmp_path / "surface.md").write_text(body, encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "version_surfaces": [
                    {
                        "id": "surface",
                        "path": "surface.md",
                        "selector": selector,
                        "usage": usage,
                        "expected_value": expected_value,
                        "rationale": rationale,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_version_agnostic_rejects_release_pin(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, usage="version_agnostic", body="default v25_TRW")
    assert checker.check(tmp_path, manifest) == ["surface.md: version_agnostic surface pins v25_TRW"]


def test_historical_record_preserves_old_release(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(
        tmp_path,
        usage="historical_record",
        body="released as v25_TRW",
        selector="framework_version",
        expected_value="v25_TRW",
        rationale="2026-06-10 release record",
    )
    assert checker.check(tmp_path, manifest) == []


def test_historical_record_rejects_blind_replacement(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(
        tmp_path,
        usage="historical_record",
        body="released as v26.1_TRW",
        selector="framework_version",
        expected_value="v25_TRW",
        rationale="2026-06-10 release record",
    )
    assert checker.check(tmp_path, manifest) == [
        "surface.md: historical expected_value 'v25_TRW' was replaced or removed"
    ]


def test_version_agnostic_rejects_aaref_release_pin(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, usage="version_agnostic", body="AARE-F version: v3.1.0")
    assert checker.check(tmp_path, manifest) == ["surface.md: version_agnostic surface pins v3.1.0"]


def test_current_default_must_match_selected_config_value(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(
        tmp_path,
        usage="current_default",
        selector="framework_version",
        body="Framework v25_TRW",
    )
    errors = checker.check(tmp_path, manifest)
    assert "surface.md: current_default does not contain framework_version=v26.1_TRW" in errors
    assert "surface.md: current_default contains stale versions v25_TRW" in errors


def test_install_snapshot_requires_explicit_v2_history_schema(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(
        tmp_path,
        usage="historical_install_snapshot",
        selector="framework_version_at_install",
        body="framework_version: v25_TRW\n",
    )
    errors = checker.check(tmp_path, manifest)
    assert any("record_kind" in error for error in errors)
    assert any("schema v2" in error for error in errors)
    assert any("framework_version_at_install" in error for error in errors)
