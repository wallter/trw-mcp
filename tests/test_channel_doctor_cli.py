"""Tests for trw-mcp channel-doctor CLI sub-command (PRD-DIST-2400 FR18)."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.cli.channel_doctor import run_channel_doctor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_namespace(**kwargs: object) -> argparse.Namespace:
    """Build a minimal argparse.Namespace for channel-doctor dispatch."""
    defaults: dict[str, object] = {
        "project_dir": ".",
        "channel_doctor_command": None,
        "max_age_hours": 24,
        "dry_run": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _write_valid_manifest(channels_dir: Path) -> None:
    channels_dir.mkdir(parents=True, exist_ok=True)
    (channels_dir / "manifest.yaml").write_text(
        """\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: codex-01
    client: codex
    surface: agents_md_segment
    telemetry_tag: codex-01
    file: AGENTS.md
    tier_default: T2
""",
        encoding="utf-8",
    )


def _write_invalid_manifest(channels_dir: Path) -> None:
    channels_dir.mkdir(parents=True, exist_ok=True)
    (channels_dir / "manifest.yaml").write_text(
        "this is not yaml: [[[",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests: init sub-command
# ---------------------------------------------------------------------------


class TestChannelDoctorInit:
    def test_init_creates_manifest_when_absent(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="init",
        )

        run_channel_doctor(args)

        manifest = channels_dir / "manifest.yaml"
        assert manifest.exists(), "Manifest should be created by init"
        out = capsys.readouterr().out
        assert "Created" in out or "exist" in out

    def test_init_is_idempotent(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        _write_valid_manifest(channels_dir)

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="init",
        )

        # Running twice must not raise or corrupt the manifest.
        run_channel_doctor(args)
        run_channel_doctor(args)

        assert (channels_dir / "manifest.yaml").exists()
        out = capsys.readouterr().out
        assert "exist" in out.lower() or "OK" in out


# ---------------------------------------------------------------------------
# Tests: validate sub-command
# ---------------------------------------------------------------------------


class TestChannelDoctorValidate:
    def test_validate_exits_0_on_valid_manifest(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        _write_valid_manifest(channels_dir)

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="validate",
        )

        # Should not raise SystemExit.
        run_channel_doctor(args)
        out = capsys.readouterr().out
        assert "OK" in out or "valid" in out.lower()

    def test_validate_exits_1_on_missing_manifest(self, tmp_path: Path) -> None:
        # No manifest written.
        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="validate",
        )

        with pytest.raises(SystemExit) as exc_info:
            run_channel_doctor(args)
        assert exc_info.value.code == 1

    def test_validate_exits_1_on_invalid_yaml(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        _write_invalid_manifest(channels_dir)

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="validate",
        )

        with pytest.raises(SystemExit) as exc_info:
            run_channel_doctor(args)
        assert exc_info.value.code == 1

    def test_validate_prints_channel_count(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        _write_valid_manifest(channels_dir)

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="validate",
        )
        run_channel_doctor(args)
        out = capsys.readouterr().out
        assert "1" in out or "channel" in out.lower()


# ---------------------------------------------------------------------------
# Tests: scan sub-command
# ---------------------------------------------------------------------------


class TestChannelDoctorScan:
    def test_scan_exits_0_on_clean_repo(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        _write_valid_manifest(channels_dir)

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="scan",
            dry_run=True,
            max_age_hours=24,
        )

        run_channel_doctor(args)  # must not raise
        out = capsys.readouterr().out
        assert "OK" in out or "orphan" in out.lower() or "no " in out.lower()

    def test_scan_dry_run_reports_orphaned_locks(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        _write_valid_manifest(channels_dir)

        # Create an orphaned lock file (not in manifest).
        orphan_lock = channels_dir / "orphaned.lock"
        orphan_lock.write_text("stale", encoding="utf-8")
        # Make it appear old.
        import os
        import time

        old_time = time.time() - (25 * 3600)  # 25 hours ago
        os.utime(orphan_lock, (old_time, old_time))

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="scan",
            dry_run=True,
            max_age_hours=24,
        )

        run_channel_doctor(args)
        out = capsys.readouterr().out
        # Should mention the orphaned lock.
        assert "orphan" in out.lower() or "lock" in out.lower() or str(orphan_lock.name) in out

    def test_scan_does_not_raise(self, tmp_path: Path) -> None:
        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="scan",
            dry_run=True,
        )
        run_channel_doctor(args)  # no exception


# ---------------------------------------------------------------------------
# Tests: clean sub-command
# ---------------------------------------------------------------------------


class TestChannelDoctorClean:
    def test_clean_dry_run_does_not_remove_files(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        # Write an orphaned lock file.
        lock_file = channels_dir / "orphan.lock"
        lock_file.write_text("stale", encoding="utf-8")
        import os
        import time

        old_time = time.time() - (25 * 3600)
        os.utime(lock_file, (old_time, old_time))

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="clean",
            dry_run=True,
            max_age_hours=24,
        )

        run_channel_doctor(args)
        # File should still exist (dry-run).
        assert lock_file.exists(), "dry-run must not delete files"

    def test_clean_removes_orphaned_lock(self, tmp_path: Path) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        lock_file = channels_dir / "orphan.lock"
        lock_file.write_text("stale", encoding="utf-8")
        import os
        import time

        old_time = time.time() - (25 * 3600)
        os.utime(lock_file, (old_time, old_time))

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="clean",
            dry_run=False,
            max_age_hours=24,
        )

        run_channel_doctor(args)
        assert not lock_file.exists(), "clean should remove orphaned locks"

    def test_clean_from_a_non_dot_project_dir_still_preserves(self, tmp_path: Path) -> None:
        """The regression that the absolute-path fixture concealed.

        `--project-dir` defaults to `"."`, which makes globbed candidates and
        relative manifest keys both relative and match by accident. Every other
        invocation — `channel-doctor clean --project-dir /path/to/repo`, which is
        the ordinary operator form — produced an empty intersection, so every
        lock registered by a live channel was reclassified orphaned and unlinked.

        This drives the real CLI entry point with an explicit project dir, so it
        exercises the resolution rather than the accident.
        """
        from argparse import Namespace

        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        (channels_dir / "manifest.yaml").write_text(
            """\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: codex-posttooluse-telemetry
    client: codex
    surface: hook_script
    telemetry_tag: t
    tier_default: T2
    lock_file: ".trw/channels/codex-posttooluse-telemetry.lock"
""",
            encoding="utf-8",
        )
        live = channels_dir / "codex-posttooluse-telemetry.lock"
        live.write_text("held by a live writer", encoding="utf-8")
        orphan = channels_dir / "orphan.lock"
        orphan.write_text("stale", encoding="utf-8")

        run_channel_doctor(
            Namespace(
                project_dir=str(tmp_path),  # NOT "." — this is the whole point
                channel_doctor_command="clean",
                dry_run=False,
                max_age_hours=0,
            )
        )

        assert live.exists(), (
            "a lock registered by a live channel was deleted because the "
            "manifest stores a repo-relative path and the scan globs absolute ones"
        )
        assert not orphan.exists(), "clean must still remove genuinely orphaned locks"

    def test_clean_preserves_a_lock_registered_in_the_manifest(self, tmp_path: Path) -> None:
        """The preservation branch, actually exercised.

        This test used to write `active_channel.lock`, which `_write_valid_manifest`
        never registers as any channel's `lock_file`. `_run_clean` builds
        `active_lock_paths` from the manifest (`channel_doctor.py:105-113`) and
        only spares locks in that set, so the file was deleted every run — the
        test's own trailing comment said so — and it ended with no assertion at
        all, just "Just verify no exception was raised". Deleting the entire
        preservation branch left it green, while its NAME claimed that branch
        worked. The consequence it failed to guard is real: `clean --apply`
        removing the lock of a channel that is mid-write.

        Registering the lock in the manifest puts it in `active_lock_paths`, so
        surviving `max_age_hours=0` is attributable to the preservation logic
        and nothing else.

        The `lock_file` value must be REPO-RELATIVE, which is what every bundled
        manifest actually stores. A first version of this rewrite wrote the
        absolute tmp_path here, and that is the one shape where the comparison
        succeeded regardless: `_run_clean` globs absolute candidates, so an
        absolute key matched while the relative keys production writes did not.
        The test passed while `channel-doctor clean --project-dir <anything but
        '.'>` deleted every registered lock. Found by an adversarial audit, not
        by the rewrite.
        """
        channels_dir = tmp_path / ".trw" / "channels"
        active_lock = channels_dir / "cc-02.lock"
        channels_dir.mkdir(parents=True, exist_ok=True)
        (channels_dir / "manifest.yaml").write_text(
            """\
format_version: "manifest/v1"
generated_by: "trw-mcp"
generated_at: ""
channels:
  - id: codex-01
    client: codex
    surface: agents_md_segment
    telemetry_tag: codex-01
    file: AGENTS.md
    tier_default: T2
    lock_file: ".trw/channels/cc-02.lock"
""",
            encoding="utf-8",
        )
        active_lock.write_text("held", encoding="utf-8")
        orphan_lock = channels_dir / "orphan.lock"
        orphan_lock.write_text("stale", encoding="utf-8")

        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="clean",
            dry_run=False,
            max_age_hours=0,  # zero threshold — everything unregistered is stale
        )

        run_channel_doctor(args)

        assert active_lock.exists(), (
            "a lock registered as a channel's lock_file is held by a live writer "
            "and must survive clean, even at max_age_hours=0"
        )
        assert not orphan_lock.exists(), (
            "the unregistered lock must still be removed — otherwise this test "
            "would pass with clean doing nothing at all"
        )

    def test_clean_does_not_raise(self, tmp_path: Path) -> None:
        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command="clean",
            dry_run=True,
        )
        run_channel_doctor(args)  # no exception


# ---------------------------------------------------------------------------
# Tests: no sub-command prints help
# ---------------------------------------------------------------------------


class TestChannelDoctorNoSubcommand:
    def test_no_subcommand_prints_usage(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        args = _make_namespace(
            project_dir=str(tmp_path),
            channel_doctor_command=None,
        )
        run_channel_doctor(args)
        out = capsys.readouterr().out
        assert "Usage" in out or "channel-doctor" in out


class TestStatsAndThrottleDispatch:
    """The ~90 lines of CLI dispatch that had zero coverage.

    An adversarial audit found `_run_stats` and `_run_throttle` were never
    invoked by any test: the suite exercised `init`, `validate`, `scan`, `clean`
    and the no-subcommand path only, and `test_cli_argparse_subcommands.py`
    covers argument PARSING without ever calling `run_channel_doctor`.

    The library functions beneath them were well tested; the wiring — `--json`,
    the `--apply` vs dry-run branch, and a broad `except Exception: sys.exit(1)`
    — was not. `--apply` mutates tier values in `manifest.yaml` and writes, so
    it is a destructive path that nothing exercised.
    """

    def _repo_with_events(self, tmp_path: Path) -> Path:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_valid_manifest(channels_dir)
        log = tmp_path / ".trw" / "telemetry" / "channel-events.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        # A push with no outcome: the honest verdict is INSUFFICIENT_DATA, which
        # is what every real project produces today (no code emits an outcome).
        log.write_text(
            '{"schema_version":"channel-event/v1","channel_id":"codex-01",'
            '"client":"codex","event_type":"push_write","ts":"2026-07-28T10:00:00.000Z",'
            '"session_id":"s1","file_path":"AGENTS.md"}\n',
            encoding="utf-8",
        )
        return tmp_path

    def test_stats_renders_without_raising(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        repo = self._repo_with_events(tmp_path)
        run_channel_doctor(
            _make_namespace(project_dir=str(repo), channel_doctor_command="stats", window_hours=1, json=False)
        )
        out = capsys.readouterr().out
        assert "Channel Stats" in out or "No channel stats" in out

    def test_stats_json_emits_parseable_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        import json as _json

        repo = self._repo_with_events(tmp_path)
        run_channel_doctor(
            _make_namespace(project_dir=str(repo), channel_doctor_command="stats", window_hours=1, json=True)
        )
        payload = _json.loads(capsys.readouterr().out)
        assert "channels" in payload and "total_events" in payload

    def test_throttle_dry_run_does_not_touch_the_manifest(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The destructive path's safe branch: nothing on disk may change."""
        repo = self._repo_with_events(tmp_path)
        manifest = repo / ".trw" / "channels" / "manifest.yaml"
        before = manifest.read_text(encoding="utf-8")

        run_channel_doctor(
            _make_namespace(project_dir=str(repo), channel_doctor_command="throttle", window_hours=1, apply=False)
        )

        assert manifest.read_text(encoding="utf-8") == before, "throttle without --apply must not write to the manifest"

    def test_throttle_apply_holds_when_the_rate_is_unmeasured(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--apply is exercised, and must NOT demote on an unmeasured rate.

        This is the destructive branch. With a push and no outcome event the
        correlation rate is unmeasured, not zero, so the honest verdict is
        INSUFFICIENT_DATA and the manifest must be left alone. Before the
        correlator fix this same input produced a fabricated 0.0 and a real tier
        demotion, so this test pins the two fixes together at the CLI boundary.
        """
        repo = self._repo_with_events(tmp_path)
        manifest = repo / ".trw" / "channels" / "manifest.yaml"
        before = manifest.read_text(encoding="utf-8")

        run_channel_doctor(
            _make_namespace(project_dir=str(repo), channel_doctor_command="throttle", window_hours=1, apply=True)
        )

        assert manifest.read_text(encoding="utf-8") == before, (
            "an unmeasured correlation rate must never drive a tier change, even under --apply"
        )

    def test_throttle_reports_no_data_rather_than_silence(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        channels_dir = tmp_path / ".trw" / "channels"
        channels_dir.mkdir(parents=True)
        _write_valid_manifest(channels_dir)

        run_channel_doctor(
            _make_namespace(project_dir=str(tmp_path), channel_doctor_command="throttle", window_hours=1, apply=False)
        )

        assert "nothing to throttle" in capsys.readouterr().out.lower()
