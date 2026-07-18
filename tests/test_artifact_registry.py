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
            fp.digest = "xyz"

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
            a.surface_id = "y"

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


class TestRepoRootArtifactDiscovery:
    """PRD-HPO-MEAS-001 FR-1: CLAUDE.md, FRAMEWORK.md, sub-CLAUDE.md coverage."""

    @pytest.fixture
    def _fake_repo(self, tmp_path: Path) -> Path:
        """Build a repo-root-shaped fake with CLAUDE.md, .trw/frameworks/FRAMEWORK.md,
        and a sub-CLAUDE.md under trw-mcp/src/trw_mcp/telemetry/."""
        (tmp_path / "CLAUDE.md").write_text("# Root governing document")
        (tmp_path / ".trw" / "frameworks").mkdir(parents=True)
        (tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md").write_text("# Framework v24.6")
        sub = tmp_path / "trw-mcp" / "src" / "trw_mcp" / "telemetry"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("# Sub-CLAUDE telemetry scope")
        return tmp_path

    def test_repo_root_discovers_claude_md(self, _fake_repo: Path, tmp_path: Path) -> None:
        empty_data = tmp_path / "empty-data"
        empty_data.mkdir()
        reg = SurfaceRegistry.build(data_root=empty_data, repo_root=_fake_repo)
        ids = {a.surface_id for a in reg.artifacts}
        assert "claude_md_root:CLAUDE.md" in ids

    def test_repo_root_discovers_framework_md(self, _fake_repo: Path, tmp_path: Path) -> None:
        empty_data = tmp_path / "empty-data"
        empty_data.mkdir()
        reg = SurfaceRegistry.build(data_root=empty_data, repo_root=_fake_repo)
        ids = {a.surface_id for a in reg.artifacts}
        assert "framework_md:.trw/frameworks/FRAMEWORK.md" in ids

    def test_repo_root_discovers_sub_claude_md(self, _fake_repo: Path, tmp_path: Path) -> None:
        empty_data = tmp_path / "empty-data"
        empty_data.mkdir()
        reg = SurfaceRegistry.build(data_root=empty_data, repo_root=_fake_repo)
        ids = {a.surface_id for a in reg.artifacts}
        sub_ids = {i for i in ids if i.startswith("sub_claude_md:")}
        assert sub_ids, f"expected sub_claude_md: prefix; got {ids}"
        # Verify the specific sub-CLAUDE we created is in there.
        assert any("telemetry/CLAUDE.md" in i for i in sub_ids)

    def test_claude_md_edit_changes_snapshot_id(self, _fake_repo: Path, tmp_path: Path) -> None:
        empty_data = tmp_path / "empty-data"
        empty_data.mkdir()
        fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        reg_a = SurfaceRegistry.build(data_root=empty_data, repo_root=_fake_repo, now=fixed)
        (_fake_repo / "CLAUDE.md").write_text("# Root governing document — EDITED")
        reg_b = SurfaceRegistry.build(data_root=empty_data, repo_root=_fake_repo, now=fixed)
        assert reg_a.snapshot_id != reg_b.snapshot_id, (
            "CLAUDE.md edit must change snapshot_id — otherwise prompt "
            "ablation against outcome deltas is impossible (FR-1)"
        )

    def test_missing_repo_root_skips_cleanly(self, tmp_path: Path) -> None:
        empty_data = tmp_path / "empty-data"
        empty_data.mkdir()
        reg = SurfaceRegistry.build(data_root=empty_data, repo_root=None)
        assert reg.artifacts == ()


class TestUnreadableFileResilience:
    """Invariant #3: build must not raise on a disk-state anomaly.

    A governing file can become unreadable (permission change, torn read,
    TOCTOU vanish while a concurrent agent rewrites it) between the caller's
    ``is_file()`` gate and the hash open. The fault must be contained to that
    one record — not abort the whole walk and collapse the snapshot to ``""``.
    """

    def test_hash_file_returns_sentinel_on_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.telemetry._artifact_discovery import _hash_file

        target = tmp_path / "x.md"
        target.write_text("real content")
        real_open = Path.open

        def boom(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "x.md":
                raise PermissionError("permission denied")
            return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", boom)
        assert _hash_file(target) == ("", 0)

    def test_build_skips_unreadable_file_without_raising(
        self, _fake_data_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_open = Path.open

        def boom(self: Path, *args: object, **kwargs: object) -> object:
            if self.name == "a.md":  # one of two agent files
                raise PermissionError("permission denied")
            return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "open", boom)

        # Must NOT raise — the whole snapshot would otherwise collapse to "".
        reg = SurfaceRegistry.build(data_root=_fake_data_root)

        by_id = {a.surface_id: a for a in reg.artifacts}
        # Every artifact is still recorded — the unreadable one is contained,
        # not the entire walk aborted.
        assert "agents:agents/a.md" in by_id
        assert "agents:agents/b.md" in by_id
        assert "hooks:hooks/h.sh" in by_id
        # The unreadable file degrades to the empty-hash sentinel...
        assert by_id["agents:agents/a.md"].content_hash == ""
        # ...while its readable siblings retain real content hashes.
        assert len(by_id["agents:agents/b.md"].content_hash) == 64
        # A non-empty snapshot id still resolves (identity preserved).
        assert reg.snapshot_id


# ---------------------------------------------------------------------------
# PRD-CORE-181-NFR01: evidence precedence — legal hold, explicit pin, active
# run, referenced receipt, and authoritative source override age/size collection.
# ---------------------------------------------------------------------------


def test_prd_core_181_nfr01(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every precedence fixture retains its protected artifact even though age
    alone would collect it, both via classify_artifact and the WIRED cleanup."""
    from trw_mcp.telemetry.retention_registry import (
        REASON_AUTHORITATIVE,
        REASON_NOT_EXPIRED,
        REASON_PERMANENT,
        REASON_REFERENCED,
        REASON_SENSITIVE,
        AuthorityClass,
        RetentionClass,
        RetentionDecision,
        RetentionEntry,
        SensitivityClass,
        classify_artifact,
        digest_file,
        save_registry,
    )

    root = tmp_path
    names = ["control.log", "legal.log", "pin.log", "run.log", "receipt.log", "authority.yaml"]
    for name in names:
        (root / name).write_text(f"{name}\n", encoding="utf-8")

    def entry(name: str, **overrides: object) -> RetentionEntry:
        base: dict[str, object] = {
            "path": name,
            "authority_class": AuthorityClass.OBSERVATIONAL,
            "producer": "p",
            "owner": "o",
            "sensitivity": SensitivityClass.NONE,
            "retention_class": RetentionClass.BOUNDED_DAYS,
            "digest": digest_file(root / name),
            "retention_days": 7,
            "registered_epoch_days": 100,
        }
        base.update(overrides)
        return RetentionEntry.model_validate(base, strict=False)

    entries = [
        entry("control.log"),  # no override -> collectible once the window expires
        entry("legal.log", sensitivity=SensitivityClass.SENSITIVE),  # legal hold
        entry("pin.log", retention_class=RetentionClass.PERMANENT),  # explicit pin
        entry("run.log", retention_class=RetentionClass.RUN_SCOPED),  # active run
        entry("receipt.log", references=("receipt/2026",)),  # referenced receipt
        entry("authority.yaml", authority_class=AuthorityClass.AUTHORITATIVE),  # authoritative source
    ]

    now = 200  # registered_epoch_days=100 + retention_days=7 -> window long expired

    def classify(name: str) -> object:
        return classify_artifact(name, root, entries, now_epoch_days=now)

    # Age-collection is genuinely live: the un-overridden control IS eligible.
    assert classify("control.log").decision is RetentionDecision.ELIGIBLE
    # Each precedence override retains the protected artifact despite its age.
    assert classify("legal.log").reason == REASON_SENSITIVE
    assert classify("pin.log").reason == REASON_PERMANENT
    assert classify("run.log").reason == REASON_NOT_EXPIRED  # active run never auto-collects
    assert classify("receipt.log").reason == REASON_REFERENCED
    assert classify("authority.yaml").reason == REASON_AUTHORITATIVE
    for name in ("legal.log", "pin.log", "run.log", "receipt.log", "authority.yaml"):
        assert classify(name).decision is RetentionDecision.RETAINED

    # Drive the WIRED cleanup: an authoritative sidecar under .trw matches the
    # cleanup suffixes and is old, yet the registry gate retains it.
    # Must be undone at teardown. A bare sys.path.insert(0, <monorepo root>) leaks
    # for the life of the pytest process, and the monorepo root ships its own
    # regular `tests` package (repo-root tests/__init__.py) which then SHADOWS
    # trw-mcp/tests. The parent process never notices (sys.modules already holds
    # the right `tests`), but every multiprocessing "spawn" child re-imports from
    # the inherited sys.path and dies with
    # `ModuleNotFoundError: No module named 'tests.<victim_module>'` — which is
    # why the spawn-based concurrency tests failed only in a full-suite run.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from scripts.trw_runtime_hygiene import collect_report

    wal_rel = ".trw/authority.db-wal"
    (root / ".trw").mkdir()
    (root / wal_rel).write_text("authoritative-wal\n", encoding="utf-8")
    save_registry(
        root,
        [
            RetentionEntry.model_validate(
                {
                    "path": wal_rel,
                    "authority_class": AuthorityClass.AUTHORITATIVE,
                    "producer": "p",
                    "owner": "o",
                    "sensitivity": SensitivityClass.NONE,
                    "retention_class": RetentionClass.BOUNDED_DAYS,
                    "digest": digest_file(root / wal_rel),
                    "retention_days": 0,
                    "registered_epoch_days": 0,
                },
                strict=False,
            )
        ],
    )
    report = collect_report(root, action="cleanup", dry_run=False, older_than_days=0, compress_min_bytes=1)
    assert (root / wal_rel).exists()  # authoritative -> retained by the wired gate
    reasons = {r.path: r.reason for r in report.retained}
    assert reasons[wal_rel] == REASON_AUTHORITATIVE
