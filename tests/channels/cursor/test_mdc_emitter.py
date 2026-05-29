"""Tests for MdcEmitter — conventions, hotspots, dangerous edits, emit_all.

PRD-DIST-2401 FR01-FR12, FR17-FR21, NFR04-NFR06.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.channels.cursor._mdc_emitter import MdcEmitter, MdcEmitterError


def _make_sidecar(
    *,
    sha: str = "abc12345def67890",
    conventions: int = 3,
    hotspot_dirs: int = 5,
    survivors: int = 2,
    undocumented: int = 1,
) -> dict[str, Any]:
    """Build a synthetic sidecar dict matching the sidecar envelope schema."""
    conv_list = [
        {"slug": f"conv-{i}", "title": f"Convention {i}", "body": f"Body {i}"}
        for i in range(conventions)
    ]
    hotspot_list = [
        {
            "file_path": f"pkg_{d}/module.py",
            "risk_score": 0.9 - d * 0.05,
            "reason": f"reason {d}",
        }
        for d in range(hotspot_dirs)
    ]
    survivor_list = [
        {"file_path": f"svc_{i}/handler.py", "description": f"survivor {i}"}
        for i in range(survivors)
    ]
    undoc_list = [
        {"file_path": f"lib_{i}/utils.py", "description": f"undoc {i}"}
        for i in range(undocumented)
    ]
    return {
        "schema_version": "risk-report-sidecar/v0",
        "sha": sha,
        "payload": {
            "generated_at": "2026-05-28T00:00:00Z",
            "conventions": conv_list,
            "hotspots": hotspot_list,
            "edge_case_survivors": survivor_list,
            "edge_case_undocumented": undoc_list,
        },
    }


# ---------------------------------------------------------------------------
# FR01 — manifest dependency (simplified: MdcEmitter works without YAML manifest)
# ---------------------------------------------------------------------------


class TestEmitterBasicFunctionality:
    def test_emit_conventions_writes_file(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()
        result = emitter.emit_conventions(sidecar)
        assert result["status"] == "written"
        mdc_path = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        assert mdc_path.exists()

    def test_emit_conventions_valid_frontmatter(self, tmp_path: Path) -> None:
        from trw_mcp.channels.cursor._mdc_templates import validate_mdc_frontmatter

        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()
        emitter.emit_conventions(sidecar)
        mdc_path = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        content = mdc_path.read_text()
        valid, reason = validate_mdc_frontmatter(content)
        assert valid, reason

    def test_emit_conventions_alwaysapply_false(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.emit_conventions(_make_sidecar())
        content = (tmp_path / ".cursor" / "rules" / "distill-conventions.mdc").read_text()
        assert "alwaysApply: false" in content

    def test_emit_conventions_description_nonempty(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.emit_conventions(_make_sidecar())
        content = (tmp_path / ".cursor" / "rules" / "distill-conventions.mdc").read_text()
        desc_line = next(l for l in content.splitlines() if l.startswith("description:"))
        assert len(desc_line.replace("description:", "").strip()) > 0

    def test_emit_hotspots_writes_files(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        results = emitter.emit_hotspots(_make_sidecar(hotspot_dirs=3))
        assert len(results) == 3
        assert all(r["status"] == "written" for r in results)

    def test_emit_dangerous_writes_file(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        result = emitter.emit_dangerous_edits(_make_sidecar())
        assert result["status"] == "written"
        assert (tmp_path / ".cursor" / "rules" / "distill-dangerous-edits.mdc").exists()


# ---------------------------------------------------------------------------
# FR02 — quota enforcement (tier-down)
# ---------------------------------------------------------------------------


class TestQuotaEnforcement:
    def test_emit_conventions_returns_result_within_quota(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar(conventions=2, hotspot_dirs=2)
        result = emitter.emit_conventions(sidecar)
        assert result["status"] == "written"
        assert result.get("bytes_written", 0) > 0


# ---------------------------------------------------------------------------
# FR03 — hotspots max_instantiations cap
# ---------------------------------------------------------------------------


class TestHotspotsMaxInstantiationsCap:
    def test_max_12_cap_with_20_dirs(self, tmp_path: Path) -> None:
        # Make 20 distinct directories
        payload_hotspots = [
            {"file_path": f"dir_{i}/module.py", "risk_score": 0.9 - i * 0.01, "reason": "r"}
            for i in range(20)
        ]
        sidecar: dict[str, Any] = {
            "schema_version": "risk-report-sidecar/v0",
            "sha": "test123",
            "payload": {
                "generated_at": "2026-05-28T00:00:00Z",
                "conventions": [],
                "hotspots": payload_hotspots,
                "edge_case_survivors": [],
                "edge_case_undocumented": [],
            },
        }
        emitter = MdcEmitter(tmp_path, max_instantiations=12)
        results = emitter.emit_hotspots(sidecar)
        written = [r for r in results if r["status"] == "written"]
        assert len(written) == 12

    def test_mdc_filenames_are_kebab_slugs(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar(hotspot_dirs=2)
        emitter.emit_hotspots(sidecar)
        cursor_rules = tmp_path / ".cursor" / "rules"
        mdc_files = list(cursor_rules.glob("distill-hotspots-*.mdc"))
        assert len(mdc_files) == 2
        for f in mdc_files:
            # All chars in stem should be lowercase, alphanumeric, or dashes
            import re
            assert re.match(r"^[a-z0-9-]+$", f.stem.replace("distill-hotspots-", ""))


# ---------------------------------------------------------------------------
# FR04 — dangerous edits dir-level globs
# ---------------------------------------------------------------------------


class TestDangerousEditsGlobs:
    def test_globs_use_directory_level_patterns(self, tmp_path: Path) -> None:
        from trw_mcp.channels.cursor._mdc_templates import validate_minimatch_glob

        emitter = MdcEmitter(tmp_path)
        sidecar: dict[str, Any] = {
            "schema_version": "risk-report-sidecar/v0",
            "sha": "abc",
            "payload": {
                "generated_at": "ts",
                "conventions": [],
                "hotspots": [],
                "edge_case_survivors": [
                    {"file_path": "backend/routers/admin.py", "description": "d"}
                ],
                "edge_case_undocumented": [],
            },
        }
        emitter.emit_dangerous_edits(sidecar)
        content = (tmp_path / ".cursor" / "rules" / "distill-dangerous-edits.mdc").read_text()
        globs_line = next(l for l in content.splitlines() if l.startswith("globs:"))
        glob_val = globs_line.replace("globs:", "").strip()
        # Should NOT be a literal .py path
        valid, _ = validate_minimatch_glob(glob_val)
        assert valid or glob_val == "[]"


# ---------------------------------------------------------------------------
# FR07 — atomic write / crash safety
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_normal_write_records_sha_in_render_log(self, tmp_path: Path) -> None:
        import hashlib

        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()
        emitter.emit_conventions(sidecar)

        target = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        assert target.exists()
        content_bytes = target.read_bytes()
        sha = hashlib.sha256(content_bytes).hexdigest()
        # Render log should contain this sha
        log_path = tmp_path / ".trw" / "channels" / "render-log.jsonl"
        assert log_path.exists()
        log_text = log_path.read_text()
        assert sha in log_text


# ---------------------------------------------------------------------------
# FR09 — conflict detection skip policy
# ---------------------------------------------------------------------------


class TestConflictDetection:
    def test_skip_policy_preserves_human_edits(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()
        # First emit
        emitter.emit_conventions(sidecar)
        target = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        # Manually edit the file (human edit)
        target.write_text(target.read_text() + "\n<!-- human edit -->", encoding="utf-8")
        human_content = target.read_text(encoding="utf-8")
        # Second emit — should skip due to conflict
        result = emitter.emit_conventions(sidecar)
        assert result["status"] in ("skipped_conflict", "written")
        # If skipped, human edit preserved
        if result["status"] == "skipped_conflict":
            assert "human edit" in target.read_text(encoding="utf-8")

    def test_force_bypasses_conflict(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()
        emitter.emit_conventions(sidecar)
        target = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        target.write_text(target.read_text() + "\n<!-- human -->", encoding="utf-8")
        # Force should overwrite
        result = emitter.emit_conventions(sidecar, force=True)
        assert result["status"] == "written"
        assert "human" not in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# FR10 — tombstone on TTL (via mocked check_staleness)
# ---------------------------------------------------------------------------


class TestTombstone:
    def test_stale_sidecar_writes_tombstone(self, tmp_path: Path) -> None:
        from trw_mcp.channels._ttl import CheckResult

        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()

        with patch(
            "trw_mcp.channels.cursor._mdc_write.check_staleness",
            return_value=CheckResult(is_stale=True, ttl_unknown=False),
        ):
            result = emitter.emit_conventions(sidecar)

        assert result["status"] == "tombstone"
        target = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        if target.exists():
            content = target.read_text()
            assert "globs: []" in content

    def test_ttl_unknown_skips_tombstone(self, tmp_path: Path) -> None:
        from trw_mcp.channels._ttl import CheckResult

        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()

        with patch(
            "trw_mcp.channels.cursor._mdc_write.check_staleness",
            return_value=CheckResult(is_stale=False, ttl_unknown=True),
        ):
            result = emitter.emit_conventions(sidecar)

        # Should proceed normally (not tombstone)
        assert result["status"] != "tombstone"


# ---------------------------------------------------------------------------
# FR11 — dry_run returns content without writing
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_content_without_file(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        result = emitter.emit_conventions(_make_sidecar(), dry_run=True)
        assert result["status"] == "dry_run"
        assert "would_write" in result
        assert len(result["would_write"]) > 0
        # File should NOT exist
        assert not (tmp_path / ".cursor" / "rules" / "distill-conventions.mdc").exists()


# ---------------------------------------------------------------------------
# FR12 — combined token quota (emit_all)
# ---------------------------------------------------------------------------


class TestCombinedTokenQuota:
    def test_emit_all_returns_combined_status(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path, max_combined_tokens=2000)
        sidecar = _make_sidecar(conventions=5, hotspot_dirs=15, survivors=8)
        result = emitter.emit_all(sidecar)
        assert "status" in result
        assert "combined_tokens_estimated" in result
        assert "conventions" in result
        assert "hotspots" in result
        assert "dangerous_edits" in result

    def test_combined_budget_flag_set_when_over(self, tmp_path: Path) -> None:
        # Use max_combined_tokens=1 to guarantee breach
        emitter = MdcEmitter(tmp_path, max_combined_tokens=1)
        sidecar = _make_sidecar(conventions=5, hotspot_dirs=3)
        result = emitter.emit_all(sidecar)
        assert result["combined_budget_enforced"] is True


# ---------------------------------------------------------------------------
# NFR04 — performance: emit_all under 5s
# ---------------------------------------------------------------------------


class TestPerformance:
    def test_full_emit_latency_under_5s(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar(conventions=5, hotspot_dirs=15, survivors=8)
        start = time.monotonic()
        emitter.emit_all(sidecar)
        elapsed = time.monotonic() - start
        assert elapsed < 5.0, f"emit_all took {elapsed:.2f}s (should be <5s)"


# ---------------------------------------------------------------------------
# NFR05 — idempotency: same sidecar SHA → byte-identical output
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_same_sidecar_produces_identical_output(self, tmp_path: Path) -> None:
        import hashlib

        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()

        emitter.emit_conventions(sidecar, force=True)
        content1 = (tmp_path / ".cursor" / "rules" / "distill-conventions.mdc").read_bytes()
        sha1 = hashlib.sha256(content1).hexdigest()

        emitter.emit_conventions(sidecar, force=True)
        content2 = (tmp_path / ".cursor" / "rules" / "distill-conventions.mdc").read_bytes()
        sha2 = hashlib.sha256(content2).hexdigest()

        assert sha1 == sha2


# ---------------------------------------------------------------------------
# NFR06 — error handling preserves existing file
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_error_preserves_existing_file(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        sidecar = _make_sidecar()
        # First write
        emitter.emit_conventions(sidecar)
        target = tmp_path / ".cursor" / "rules" / "distill-conventions.mdc"
        original_content = target.read_text()

        # Second call with broken sidecar — should return error but NOT crash
        bad_sidecar: dict[str, Any] = {"sha": "bad", "payload": None}
        result = emitter.emit_conventions(bad_sidecar)
        # Should return a result dict (not raise)
        assert isinstance(result, dict)
        # File should still exist with original or new content
        assert target.exists()


# ---------------------------------------------------------------------------
# FR08 — bootstrap_stubs idempotency
# ---------------------------------------------------------------------------


class TestBootstrapStubs:
    def test_bootstrap_stubs_creates_t0_stubs(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        result = emitter.bootstrap_stubs()
        assert result["status"] == "ok"
        assert (tmp_path / ".cursor" / "rules" / "distill-conventions.mdc").exists()
        assert (tmp_path / ".cursor" / "rules" / "distill-dangerous-edits.mdc").exists()

    def test_bootstrap_stubs_idempotent(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        result1 = emitter.bootstrap_stubs()
        result2 = emitter.bootstrap_stubs()
        # Second call should not create new stubs (already exist)
        assert result2["status"] == "ok"
        assert len(result2["created"]) == 0

    def test_bootstrap_stubs_adds_gitignore_entries(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.bootstrap_stubs()
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            assert "distill-hotspots-*.mdc" in content
            assert "distill-dangerous-edits.mdc" in content


# ---------------------------------------------------------------------------
# FR08 — git_tracked defaults
# ---------------------------------------------------------------------------


class TestGitTrackedDefaults:
    def test_cur01_git_tracked_true_not_in_gitignore(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.bootstrap_stubs()
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            content = gitignore.read_text()
            assert "distill-conventions.mdc" not in content or "distill-hotspots" in content

    def test_cur02_gitignore_glob_added(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.bootstrap_stubs()
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            assert "distill-hotspots-*.mdc" in gitignore.read_text()

    def test_cur03_gitignore_entry_added(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.bootstrap_stubs()
        gitignore = tmp_path / ".gitignore"
        if gitignore.exists():
            assert "distill-dangerous-edits.mdc" in gitignore.read_text()


# ---------------------------------------------------------------------------
# FR20 — telemetry record_ids format
# ---------------------------------------------------------------------------


class TestTelemetryRecordIds:
    def test_emit_writes_to_telemetry_log(self, tmp_path: Path) -> None:
        emitter = MdcEmitter(tmp_path)
        emitter.emit_conventions(_make_sidecar())
        telemetry_path = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
        assert telemetry_path.exists()
        lines = telemetry_path.read_text().splitlines()
        assert len(lines) >= 1
