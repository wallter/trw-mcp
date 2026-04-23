"""Tests for surface_manifest — PRD-HPO-MEAS-001 FR-2 (run_surface_snapshot.yaml)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from trw_mcp.telemetry.artifact_registry import (
    SurfaceArtifact,
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
def _clear_cache() -> Iterator[None]:
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


def _sample_snapshot() -> SurfaceSnapshot:
    ts = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
    return SurfaceSnapshot(
        snapshot_id="deadbeef" * 8,
        trw_mcp_version="0.1.2",
        framework_version="v24.6_TRW",
        generated_at=ts,
        artifacts=(
            SurfaceArtifact(
                surface_id="agents:a.md",
                content_hash="aa" * 32,
                version="0.1.2",
                discovered_at=ts,
                source_path="agents/a.md",
            ),
            SurfaceArtifact(
                surface_id="hooks:h.sh",
                content_hash="bb" * 32,
                version="0.1.2",
                discovered_at=ts,
                source_path="hooks/h.sh",
            ),
        ),
    )


class TestManifestFilename:
    def test_matches_prd_fr2_glob_contract(self) -> None:
        # PRD-HPO-MEAS-001 FR-2 glob_exists mandates
        # ``.trw/runs/*/meta/run_surface_snapshot.yaml``. Rename here
        # requires a PRD update.
        assert MANIFEST_FILENAME == "run_surface_snapshot.yaml"


class TestSerialization:
    def test_round_trip_preserves_fields(self) -> None:
        original = _sample_snapshot()
        raw = snapshot_to_yaml(original)
        restored = yaml_to_snapshot(raw)

        assert restored.snapshot_id == original.snapshot_id
        assert restored.trw_mcp_version == original.trw_mcp_version
        assert restored.framework_version == original.framework_version
        assert restored.generated_at == original.generated_at
        assert len(restored.artifacts) == len(original.artifacts)
        for a_restored, a_orig in zip(restored.artifacts, original.artifacts):
            assert a_restored.surface_id == a_orig.surface_id
            assert a_restored.content_hash == a_orig.content_hash
            assert a_restored.source_path == a_orig.source_path

    def test_yaml_keys_are_sorted(self) -> None:
        raw = snapshot_to_yaml(_sample_snapshot())
        parsed = yaml.safe_load(raw)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_artifacts_are_sorted_deterministically(self) -> None:
        raw = snapshot_to_yaml(_sample_snapshot())
        parsed = yaml.safe_load(raw)
        ids = [a["surface_id"] for a in parsed["artifacts"]]
        assert ids == sorted(ids)

    def test_malformed_yaml_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            yaml_to_snapshot("not-a-mapping")

    def test_missing_required_keys_raises(self) -> None:
        with pytest.raises((ValueError, KeyError)):
            yaml_to_snapshot("just_a_field: 1")

    def test_non_list_artifacts_raises(self) -> None:
        bad = (
            "snapshot_id: x\n"
            "trw_mcp_version: 0\n"
            "framework_version: 0\n"
            "generated_at: 2026-04-23T00:00:00+00:00\n"
            "artifacts: not-a-list\n"
        )
        with pytest.raises(ValueError):
            yaml_to_snapshot(bad)

    def test_non_mapping_artifact_entry_raises(self) -> None:
        bad = (
            "snapshot_id: x\n"
            "trw_mcp_version: 0\n"
            "framework_version: 0\n"
            "generated_at: 2026-04-23T00:00:00+00:00\n"
            "artifacts:\n"
            "  - 'just-a-string'\n"
        )
        with pytest.raises(ValueError):
            yaml_to_snapshot(bad)


class TestWriteManifest:
    def test_writes_to_correct_filename(self, tmp_path: Path) -> None:
        snap = _sample_snapshot()
        target = write_manifest(snap, tmp_path)
        assert target == tmp_path / MANIFEST_FILENAME
        assert target.exists()
        assert target.name == "run_surface_snapshot.yaml"

    def test_creates_run_dir_if_missing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "new" / "run"
        target = write_manifest(_sample_snapshot(), run_dir)
        assert target.exists()
        assert run_dir.is_dir()

    def test_atomic_write_no_tmp_leftover(self, tmp_path: Path) -> None:
        write_manifest(_sample_snapshot(), tmp_path)
        leftovers = list(tmp_path.glob(".run_surface_snapshot.*.tmp"))
        assert leftovers == []

    def test_overwrite_preserves_new_content(self, tmp_path: Path) -> None:
        snap1 = _sample_snapshot()
        # Use a valid 64-char hex id, not the earlier 60-char bug.
        snap2 = snap1.model_copy(update={"snapshot_id": "b" * 64})
        write_manifest(snap1, tmp_path)
        write_manifest(snap2, tmp_path)
        restored = load_manifest(tmp_path)
        assert restored is not None
        assert restored.snapshot_id == "b" * 64


class TestLoadManifest:
    def test_missing_returns_none(self, tmp_path: Path) -> None:
        assert load_manifest(tmp_path) is None

    def test_round_trip_via_disk(self, tmp_path: Path) -> None:
        snap = _sample_snapshot()
        write_manifest(snap, tmp_path)
        restored = load_manifest(tmp_path)
        assert restored is not None
        assert restored.snapshot_id == snap.snapshot_id
        assert len(restored.artifacts) == len(snap.artifacts)


class TestStampSession:
    def test_stamps_a_fresh_run_dir(self, tmp_path: Path) -> None:
        snap = stamp_session(tmp_path)
        assert (tmp_path / MANIFEST_FILENAME).exists()
        assert snap.snapshot_id
        assert len(snap.snapshot_id) == 64

    def test_idempotent_on_same_process(self, tmp_path: Path) -> None:
        snap1 = stamp_session(tmp_path)
        snap2 = stamp_session(tmp_path)
        assert snap1.snapshot_id == snap2.snapshot_id

    def test_round_trip_through_disk(self, tmp_path: Path) -> None:
        snap_written = stamp_session(tmp_path)
        snap_loaded = load_manifest(tmp_path)
        assert snap_loaded is not None
        assert snap_loaded.snapshot_id == snap_written.snapshot_id
