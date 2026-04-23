"""Tests for artifact_registry — PRD-HPO-MEAS-001 S1."""

from __future__ import annotations

import pytest

from trw_mcp.telemetry.artifact_registry import (
    ComponentFingerprint,
    SurfaceSnapshot,
    _fingerprint_component,
    _snapshot_digest,
    clear_snapshot_cache,
    resolve_surface_snapshot,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


class TestComponentFingerprint:
    def test_defaults_are_empty(self):
        fp = ComponentFingerprint()
        assert fp.digest == ""
        assert fp.file_count == 0
        assert fp.total_bytes == 0

    def test_frozen(self):
        fp = ComponentFingerprint(digest="abc", file_count=1, total_bytes=10)
        with pytest.raises(Exception):
            fp.digest = "xyz"  # type: ignore[misc]

    def test_extra_forbidden(self):
        with pytest.raises(Exception):
            ComponentFingerprint(digest="a", file_count=0, total_bytes=0, extra_field="x")  # type: ignore[call-arg]


class TestSurfaceSnapshot:
    def test_required_fields(self):
        from datetime import datetime, timezone

        snap = SurfaceSnapshot(
            snapshot_id="deadbeef",
            trw_mcp_version="0.0.0",
            framework_version="test",
            generated_at=datetime.now(tz=timezone.utc),
        )
        assert snap.snapshot_id == "deadbeef"
        assert snap.components == {}

    def test_frozen(self):
        from datetime import datetime, timezone

        snap = SurfaceSnapshot(
            snapshot_id="a",
            trw_mcp_version="0.0.0",
            framework_version="t",
            generated_at=datetime.now(tz=timezone.utc),
        )
        with pytest.raises(Exception):
            snap.snapshot_id = "b"  # type: ignore[misc]


class TestFingerprintComponent:
    def test_missing_root_is_empty(self, tmp_path):
        fp = _fingerprint_component(tmp_path / "does-not-exist")
        assert fp.digest == ""
        assert fp.file_count == 0

    def test_empty_root_is_empty(self, tmp_path):
        fp = _fingerprint_component(tmp_path)
        assert fp.digest == ""

    def test_single_file_produces_digest(self, tmp_path):
        (tmp_path / "a.md").write_text("hello")
        fp = _fingerprint_component(tmp_path, ("*.md",))
        assert fp.digest  # non-empty
        assert fp.file_count == 1
        assert fp.total_bytes == 5

    def test_deterministic_across_calls(self, tmp_path):
        (tmp_path / "a.md").write_text("hello")
        (tmp_path / "b.md").write_text("world")
        fp1 = _fingerprint_component(tmp_path, ("*.md",))
        fp2 = _fingerprint_component(tmp_path, ("*.md",))
        assert fp1.digest == fp2.digest

    def test_content_change_produces_different_digest(self, tmp_path):
        f = tmp_path / "a.md"
        f.write_text("v1")
        fp1 = _fingerprint_component(tmp_path, ("*.md",))
        f.write_text("v2")
        fp2 = _fingerprint_component(tmp_path, ("*.md",))
        assert fp1.digest != fp2.digest

    def test_sorted_path_order_stable_across_runs(self, tmp_path):
        (tmp_path / "z.md").write_text("z-content")
        (tmp_path / "a.md").write_text("a-content")
        fp_a = _fingerprint_component(tmp_path, ("*.md",))
        # Rewrite in opposite order to attempt to destabilize
        (tmp_path / "z.md").write_text("z-content")
        (tmp_path / "a.md").write_text("a-content")
        fp_b = _fingerprint_component(tmp_path, ("*.md",))
        assert fp_a.digest == fp_b.digest

    def test_rename_changes_digest(self, tmp_path):
        (tmp_path / "a.md").write_text("hello")
        fp1 = _fingerprint_component(tmp_path, ("*.md",))
        (tmp_path / "a.md").rename(tmp_path / "b.md")
        fp2 = _fingerprint_component(tmp_path, ("*.md",))
        assert fp1.digest != fp2.digest, "rename should change rollup since path is part of the hash"


class TestSnapshotDigest:
    def test_stable_ordering(self):
        comps_a = {
            "agents": ComponentFingerprint(digest="deadbeef"),
            "skills": ComponentFingerprint(digest="cafebabe"),
        }
        comps_b = {
            "skills": ComponentFingerprint(digest="cafebabe"),
            "agents": ComponentFingerprint(digest="deadbeef"),
        }
        d1 = _snapshot_digest(trw_mcp_version="1", framework_version="v1", components=comps_a)
        d2 = _snapshot_digest(trw_mcp_version="1", framework_version="v1", components=comps_b)
        assert d1 == d2

    def test_component_change_changes_digest(self):
        comps_a = {"agents": ComponentFingerprint(digest="deadbeef")}
        comps_b = {"agents": ComponentFingerprint(digest="cafebabe")}
        d1 = _snapshot_digest(trw_mcp_version="1", framework_version="v1", components=comps_a)
        d2 = _snapshot_digest(trw_mcp_version="1", framework_version="v1", components=comps_b)
        assert d1 != d2

    def test_version_bump_changes_digest(self):
        comps = {"agents": ComponentFingerprint(digest="deadbeef")}
        d1 = _snapshot_digest(trw_mcp_version="1", framework_version="v1", components=comps)
        d2 = _snapshot_digest(trw_mcp_version="2", framework_version="v1", components=comps)
        assert d1 != d2


class TestResolveSurfaceSnapshot:
    def test_returns_snapshot(self):
        snap = resolve_surface_snapshot()
        assert isinstance(snap, SurfaceSnapshot)
        assert snap.snapshot_id  # non-empty
        assert len(snap.snapshot_id) == 64  # sha256 hex

    def test_cache_is_hit_on_repeat_call(self):
        snap1 = resolve_surface_snapshot()
        snap2 = resolve_surface_snapshot()
        # Same snapshot_id AND same generated_at — proves cache hit.
        assert snap1.snapshot_id == snap2.snapshot_id
        assert snap1.generated_at == snap2.generated_at

    def test_refresh_forces_new_generation(self):
        snap1 = resolve_surface_snapshot()
        snap2 = resolve_surface_snapshot(refresh=True)
        # snapshot_id should match (content-stable) but generated_at should advance.
        assert snap1.snapshot_id == snap2.snapshot_id
        assert snap2.generated_at >= snap1.generated_at

    def test_has_known_components(self):
        snap = resolve_surface_snapshot()
        for key in ("agents", "skills", "hooks", "prompts", "surfaces", "config"):
            assert key in snap.components
