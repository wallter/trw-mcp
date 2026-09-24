"""Regression tests for the semantic canon-version scan."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

# Every case exercises scripts absent from the standalone package.
pytestmark = requires_monorepo


def _load_checker() -> ModuleType:
    path = (MONOREPO_ROOT or PACKAGE_ROOT.parent) / "scripts/check-canon-version-surfaces.py"
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
        "framework_version: v99.9_TRW\naaref_version: v3.2.0\n",
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
        body="released as v99.9_TRW",
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
    assert "surface.md: current_default does not contain framework_version=v99.9_TRW" in errors
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


def test_an_absent_install_snapshot_is_not_an_error(checker: ModuleType, tmp_path: Path) -> None:
    """The snapshot is gitignored and exists only after an install; a clean worktree has none."""
    manifest = _fixture(
        tmp_path,
        usage="historical_install_snapshot",
        selector="framework_version_at_install",
        body="",
    )
    (tmp_path / "surface.md").unlink()
    assert checker.check(tmp_path, manifest) == []


def test_an_absent_current_default_surface_is_still_an_error(checker: ModuleType, tmp_path: Path) -> None:
    manifest = _fixture(tmp_path, usage="current_default", selector="framework_version", body="")
    (tmp_path / "surface.md").unlink()
    assert checker.check(tmp_path, manifest) == ["surface.md: governed surface is missing"]


def _write(tmp_path: Path, surfaces: list[dict[str, object]]) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version_surfaces": surfaces}), encoding="utf-8")
    return manifest


_GOVERNED = [
    (".trw/installer-meta.yaml", "historical_install_snapshot", "framework_version_at_install"),
    (".trw/installer-meta.yaml", "historical_install_snapshot", "aaref_version_at_install"),
    ("docs/TRW-COMPREHENSIVE-GUIDE.md", "version_agnostic", None),
    ("docs/documentation/INDEX.md", "version_agnostic", None),
    ("docs/documentation/agent-guide.md", "version_agnostic", None),
    ("docs/documentation/architecture-overview.md", "version_agnostic", None),
    ("docs/documentation/prd-implementation-status.md", "version_agnostic", None),
]
_BUNDLED = PACKAGE_ROOT / "src/trw_mcp/data/framework_canons.json"


def _key(record: dict[str, object]) -> tuple[object, object, object]:
    return (record.get("path"), record.get("usage"), record.get("selector"))


def test_an_empty_manifest_misses_every_required_surface(checker: ModuleType, tmp_path: Path) -> None:
    missing = checker.missing_required(_write(tmp_path, []))
    assert len(missing) == len(_GOVERNED)
    assert all(message.startswith("required governed surface not declared: ") for message in missing)


@pytest.mark.parametrize("dropped", _GOVERNED, ids=lambda key: f"{key[0]}:{key[2] or key[1]}")
def test_dropping_any_governed_record_is_reported(
    checker: ModuleType, tmp_path: Path, dropped: tuple[str, str, str | None]
) -> None:
    records = json.loads(_BUNDLED.read_text(encoding="utf-8"))["version_surfaces"]
    kept = [record for record in records if _key(record) != dropped]
    assert len(kept) == len(records) - 1, "the bundled manifest must declare the dropped record"
    path, usage, selector = dropped
    detail = f"{usage}, {selector}" if selector else usage
    assert checker.missing_required(_write(tmp_path, kept)) == [
        f"required governed surface not declared: {path} ({detail})"
    ]


def test_the_cli_fails_on_an_empty_manifest(checker: ModuleType, tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw/config.yaml").write_text("framework_version: v99.9_TRW\n", encoding="utf-8")
    manifest = _write(tmp_path, [])
    assert checker.main(["--root", str(tmp_path), "--manifest", str(manifest)]) == 1


def test_the_bundled_manifest_declares_every_required_surface(checker: ModuleType) -> None:
    assert checker.missing_required(_BUNDLED) == []
