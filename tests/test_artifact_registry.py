"""Tests for artifact_registry — PRD-HPO-MEAS-001 FR-1 / FR-2."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.telemetry.artifact_registry import (
    ComponentFingerprint,
    SurfaceArtifact,
    SurfaceRegistry,
    SurfaceSnapshot,
    _artifacts_snapshot_id,
    _component_rollup,
    clear_snapshot_cache,
    resolve_surface_registry,
    resolve_surface_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


@pytest.fixture
def _fake_data_root(tmp_path: Path) -> Path:
    """Build a fake bundled-data layout that SurfaceRegistry.build can walk."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("agent-a")
    (tmp_path / "agents" / "b.md").write_text("agent-b")
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "h.sh").write_text("#!/bin/sh\necho hi")
    (tmp_path / "behavioral_protocol.yaml").write_text("ok: true\n")
    return tmp_path


class TestComponentFingerprint:
    def test_defaults_are_empty(self) -> None:
        fp = ComponentFingerprint()
        assert fp.digest == ""
        assert fp.file_count == 0
        assert fp.total_bytes == 0

    def test_frozen_raises_validation_error(self) -> None:
        fp = ComponentFingerprint(digest="abc", file_count=1, total_bytes=10)
        with pytest.raises(ValidationError):
            fp.digest = "xyz"  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ComponentFingerprint(digest="a", file_count=0, total_bytes=0, extra_field="x")  # type: ignore[call-arg]


class TestSurfaceArtifact:
    def test_required_fields(self) -> None:
        now = datetime.now(tz=timezone.utc)
        a = SurfaceArtifact(
            surface_id="agents:a.md",
            content_hash="ff" * 32,
            version="0.1.0",
            discovered_at=now,
            source_path="agents/a.md",
        )
        assert a.surface_id == "agents:a.md"
        assert a.version == "0.1.0"

    def test_frozen_raises_validation_error(self) -> None:
        a = SurfaceArtifact(
            surface_id="x",
            content_hash="a",
            version="0",
            discovered_at=datetime.now(tz=timezone.utc),
            source_path="x",
        )
        with pytest.raises(ValidationError):
            a.surface_id = "y"  # type: ignore[misc]

    def test_extra_field_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            SurfaceArtifact(  # type: ignore[call-arg]
                surface_id="x",
                content_hash="a",
                version="0",
                discovered_at=datetime.now(tz=timezone.utc),
                source_path="x",
                extra="nope",
            )


class TestSurfaceRegistryBuild:
    def test_empty_root_produces_empty_artifacts(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty-dir"
        empty.mkdir()
        reg = SurfaceRegistry.build(data_root=empty)
        assert reg.artifacts == ()

    def test_missing_root_produces_empty_artifacts(self, tmp_path: Path) -> None:
        reg = SurfaceRegistry.build(data_root=tmp_path / "does-not-exist")
        assert reg.artifacts == ()

    def test_fake_root_produces_per_artifact_records(self, _fake_data_root: Path) -> None:
        reg = SurfaceRegistry.build(data_root=_fake_data_root)
        # 2 agents + 1 hook + 1 config = 4 artifacts
        assert len(reg.artifacts) == 4
        surface_ids = {a.surface_id for a in reg.artifacts}
        assert "agents:agents/a.md" in surface_ids
        assert "agents:agents/b.md" in surface_ids
        assert "hooks:hooks/h.sh" in surface_ids
        assert "config:behavioral_protocol.yaml" in surface_ids

    def test_artifact_records_have_required_fr1_fields(self, _fake_data_root: Path) -> None:
        reg = SurfaceRegistry.build(data_root=_fake_data_root)
        for art in reg.artifacts:
            assert art.surface_id
            assert len(art.content_hash) == 64  # sha256 hex
            assert art.version
            assert art.discovered_at.tzinfo is timezone.utc
            assert art.source_path

    def test_build_accepts_explicit_now(self, _fake_data_root: Path) -> None:
        fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        reg = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        assert reg.generated_at == fixed
        for art in reg.artifacts:
            assert art.discovered_at == fixed

    def test_snapshot_id_is_stable_across_builds(self, _fake_data_root: Path) -> None:
        fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        reg_a = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        reg_b = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        assert reg_a.snapshot_id == reg_b.snapshot_id

    def test_snapshot_id_changes_on_content_change(self, _fake_data_root: Path) -> None:
        fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        reg_a = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        (_fake_data_root / "agents" / "a.md").write_text("agent-a-CHANGED")
        reg_b = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        assert reg_a.snapshot_id != reg_b.snapshot_id

    def test_snapshot_id_changes_on_rename(self, _fake_data_root: Path) -> None:
        fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        reg_a = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        (_fake_data_root / "agents" / "a.md").rename(_fake_data_root / "agents" / "c.md")
        reg_b = SurfaceRegistry.build(data_root=_fake_data_root, now=fixed)
        assert reg_a.snapshot_id != reg_b.snapshot_id


class TestSurfaceRegistryToSnapshot:
    def test_round_trip_preserves_artifact_count(self, _fake_data_root: Path) -> None:
        reg = SurfaceRegistry.build(data_root=_fake_data_root)
        snap = reg.to_snapshot()
        assert len(snap.artifacts) == len(reg.artifacts)
        assert snap.snapshot_id == reg.snapshot_id

    def test_artifacts_are_sorted_in_snapshot(self, _fake_data_root: Path) -> None:
        reg = SurfaceRegistry.build(data_root=_fake_data_root)
        snap = reg.to_snapshot()
        sorted_ids = [a.surface_id for a in snap.artifacts]
        assert sorted_ids == sorted(sorted_ids)


class TestSnapshotIdDigest:
    def test_empty_artifact_set_produces_version_only_digest(self) -> None:
        d1 = _artifacts_snapshot_id([], trw_mcp_version="1", framework_version="v1")
        d2 = _artifacts_snapshot_id([], trw_mcp_version="1", framework_version="v1")
        assert d1 == d2
        assert len(d1) == 64

    def test_version_bump_changes_digest(self) -> None:
        d1 = _artifacts_snapshot_id([], trw_mcp_version="1", framework_version="v1")
        d2 = _artifacts_snapshot_id([], trw_mcp_version="2", framework_version="v1")
        assert d1 != d2


class TestComponentRollup:
    def test_missing_root_empty(self, tmp_path: Path) -> None:
        fp = _component_rollup(tmp_path / "nope", ("**/*",))
        assert fp.file_count == 0

    def test_deterministic_rollup(self, _fake_data_root: Path) -> None:
        fp_a = _component_rollup(_fake_data_root / "agents", ("*.md",))
        fp_b = _component_rollup(_fake_data_root / "agents", ("*.md",))
        assert fp_a.digest == fp_b.digest
        assert fp_a.file_count == 2


class TestResolveSurfaceSnapshotBackCompat:
    def test_returns_snapshot(self) -> None:
        snap = resolve_surface_snapshot()
        assert isinstance(snap, SurfaceSnapshot)
        assert snap.snapshot_id
        assert len(snap.snapshot_id) == 64

    def test_cache_is_hit_on_repeat_call(self) -> None:
        snap1 = resolve_surface_snapshot()
        snap2 = resolve_surface_snapshot()
        assert snap1.snapshot_id == snap2.snapshot_id
        assert snap1.generated_at == snap2.generated_at

    def test_refresh_forces_new_generation(self) -> None:
        snap1 = resolve_surface_snapshot()
        snap2 = resolve_surface_snapshot(refresh=True)
        assert snap1.snapshot_id == snap2.snapshot_id
        assert snap2.generated_at >= snap1.generated_at


class TestResolveSurfaceRegistry:
    def test_returns_registry(self) -> None:
        reg = resolve_surface_registry()
        assert isinstance(reg, SurfaceRegistry)
        # component_rollup returns all 6 known categories even with no data
        rollup = reg.component_rollup()
        assert set(rollup.keys()) == {"agents", "skills", "hooks", "prompts", "surfaces", "config"}
