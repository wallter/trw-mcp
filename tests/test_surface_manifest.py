"""Tests for surface_manifest — PRD-HPO-MEAS-001 S2."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from trw_mcp.telemetry.artifact_registry import (
    ComponentFingerprint,
    SurfaceSnapshot,
    clear_snapshot_cache,
)
from trw_mcp.telemetry.surface_manifest import (
    MANIFEST_FILENAME,
    load_manifest,
    snapshot_to_yaml,
    stamp_session,
    write_manifest,
    yaml_to_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


def _sample_snapshot() -> SurfaceSnapshot:
    return SurfaceSnapshot(
        snapshot_id="deadbeef" * 8,
        trw_mcp_version="0.1.2",
        framework_version="v24.6_TRW",
        generated_at=datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc),
        components={
            "agents": ComponentFingerprint(digest="aaa", file_count=3, total_bytes=42),
            "skills": ComponentFingerprint(digest="bbb", file_count=5, total_bytes=100),
        },
    )


class TestSerialization:
    def test_round_trip_preserves_fields(self):
        original = _sample_snapshot()
        raw = snapshot_to_yaml(original)
        restored = yaml_to_snapshot(raw)

        assert restored.snapshot_id == original.snapshot_id
        assert restored.trw_mcp_version == original.trw_mcp_version
        assert restored.framework_version == original.framework_version
        assert restored.generated_at == original.generated_at
        assert restored.components.keys() == original.components.keys()
        for k in original.components:
            assert restored.components[k].digest == original.components[k].digest
            assert restored.components[k].file_count == original.components[k].file_count

    def test_yaml_keys_are_sorted(self):
        raw = snapshot_to_yaml(_sample_snapshot())
        parsed = yaml.safe_load(raw)
        assert list(parsed.keys()) == sorted(parsed.keys())
        assert list(parsed["components"].keys()) == sorted(parsed["components"].keys())

    def test_malformed_yaml_raises_value_error(self):
        with pytest.raises(ValueError):
            yaml_to_snapshot("not-a-mapping")

    def test_missing_required_keys_raises(self):
        with pytest.raises((ValueError, KeyError)):
            yaml_to_snapshot("just_a_field: 1")

    def test_non_mapping_components_raises(self):
        bad = (
            "snapshot_id: x\n"
            "trw_mcp_version: 0\n"
            "framework_version: 0\n"
            "generated_at: 2026-04-23T00:00:00+00:00\n"
            "components: not-a-mapping\n"
        )
        with pytest.raises(ValueError):
            yaml_to_snapshot(bad)


class TestWriteManifest:
    def test_writes_to_target(self, tmp_path: Path):
        snap = _sample_snapshot()
        target = write_manifest(snap, tmp_path)
        assert target == tmp_path / MANIFEST_FILENAME
        assert target.exists()
        assert target.read_text().startswith("components:")

    def test_creates_run_dir_if_missing(self, tmp_path: Path):
        run_dir = tmp_path / "new" / "run"
        target = write_manifest(_sample_snapshot(), run_dir)
        assert target.exists()
        assert run_dir.is_dir()

    def test_atomic_write_no_tmp_leftover(self, tmp_path: Path):
        write_manifest(_sample_snapshot(), tmp_path)
        leftovers = list(tmp_path.glob(".surface_manifest.*.tmp"))
        assert leftovers == []

    def test_overwrite_existing(self, tmp_path: Path):
        snap1 = _sample_snapshot()
        snap2 = _sample_snapshot().model_copy(update={"snapshot_id": "bbbbb" * 12 + "bbbb"})
        write_manifest(snap1, tmp_path)
        write_manifest(snap2, tmp_path)
        restored = load_manifest(tmp_path)
        assert restored is not None
        assert restored.snapshot_id == snap2.snapshot_id


class TestLoadManifest:
    def test_missing_returns_none(self, tmp_path: Path):
        assert load_manifest(tmp_path) is None

    def test_round_trip_via_disk(self, tmp_path: Path):
        snap = _sample_snapshot()
        write_manifest(snap, tmp_path)
        restored = load_manifest(tmp_path)
        assert restored is not None
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.components.keys() == snap.components.keys()


class TestStampSession:
    def test_stamps_a_fresh_run_dir(self, tmp_path: Path):
        snap = stamp_session(tmp_path)
        assert (tmp_path / MANIFEST_FILENAME).exists()
        assert snap.snapshot_id  # non-empty
        assert len(snap.snapshot_id) == 64

    def test_idempotent_on_same_process(self, tmp_path: Path):
        snap1 = stamp_session(tmp_path)
        snap2 = stamp_session(tmp_path)
        assert snap1.snapshot_id == snap2.snapshot_id

    def test_round_trip_through_disk(self, tmp_path: Path):
        snap_written = stamp_session(tmp_path)
        snap_loaded = load_manifest(tmp_path)
        assert snap_loaded is not None
        assert snap_loaded.snapshot_id == snap_written.snapshot_id
