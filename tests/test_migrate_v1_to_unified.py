"""Tests for migration.v1_to_unified — PRD-HPO-MEAS-001 S8."""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.migration.v1_to_unified import migrate_run


def _write_legacy_events(run_dir: Path, rows: list[dict[str, object]]) -> None:
    meta = run_dir / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    with (meta / "events.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class TestMigrateDryRun:
    def test_empty_meta_returns_empty_report(self, tmp_path: Path) -> None:
        (tmp_path / "meta").mkdir()
        report = migrate_run(tmp_path)
        assert report.rows_read == 0
        assert report.rows_migrated == 0
        assert report.applied is False

    def test_legacy_rows_counted_dry_run(self, tmp_path: Path) -> None:
        _write_legacy_events(
            tmp_path,
            [
                {"event": "session_start", "ts": "2026-04-23T10:00:00", "session_id": "s1"},
                {"event": "phase_enter", "ts": "2026-04-23T10:01:00", "phase": "IMPLEMENT"},
            ],
        )
        report = migrate_run(tmp_path, apply=False)
        assert report.rows_read == 2
        assert report.rows_migrated == 2  # would-be-migrated
        assert report.applied is False
        # Dry-run did NOT write.
        assert not any((tmp_path / "meta").glob("events-*.jsonl"))


class TestMigrateApply:
    def test_applies_writes_to_target(self, tmp_path: Path) -> None:
        _write_legacy_events(
            tmp_path,
            [
                {"event": "session_start", "ts": "2026-04-23T10:00:00", "session_id": "s1"},
                {"event": "tool_call", "ts": "2026-04-23T10:01:00", "session_id": "s1", "tool": "trw_recall"},
            ],
        )
        report = migrate_run(tmp_path, apply=True)
        assert report.applied is True
        assert report.rows_migrated == 2
        target = report.target_file
        assert target is not None and target.exists()
        lines = target.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_idempotent_skips_duplicates(self, tmp_path: Path) -> None:
        _write_legacy_events(
            tmp_path,
            [
                {"event": "session_start", "event_id": "evt_x", "ts": "t1", "session_id": "s"},
            ],
        )
        r1 = migrate_run(tmp_path, apply=True)
        assert r1.rows_migrated == 1
        r2 = migrate_run(tmp_path, apply=True)
        assert r2.rows_migrated == 0
        assert r2.rows_skipped_duplicate == 1

    def test_preserves_legacy_payload(self, tmp_path: Path) -> None:
        _write_legacy_events(
            tmp_path,
            [
                {
                    "event": "tool_call",
                    "ts": "2026-04-23T10:00:00",
                    "session_id": "s",
                    "tool": "trw_recall",
                    "wall_ms": 250,
                    "outcome": "success",
                },
            ],
        )
        report = migrate_run(tmp_path, apply=True)
        assert report.applied is True
        target = report.target_file
        assert target is not None
        rec = json.loads(target.read_text().strip())
        assert rec["event_type"] == "tool_call"
        assert rec["payload"]["tool"] == "trw_recall"
        assert rec["payload"]["wall_ms"] == 250
        assert rec["payload"]["legacy_event"] == "tool_call"

    def test_unknown_event_falls_back_to_observer(self, tmp_path: Path) -> None:
        _write_legacy_events(
            tmp_path,
            [
                {"event": "some_new_unmapped_type", "ts": "t1", "session_id": "s"},
            ],
        )
        report = migrate_run(tmp_path, apply=True)
        assert report.rows_migrated == 1
        target = report.target_file
        assert target is not None
        rec = json.loads(target.read_text().strip())
        assert rec["event_type"] == "observer"
        assert rec["payload"]["legacy_event"] == "some_new_unmapped_type"


class TestMigrateMalformed:
    def test_malformed_json_line_counted(self, tmp_path: Path) -> None:
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "events.jsonl").write_text(
            '{"event": "session_start", "ts": "t1"}\n{bad json\n{"event": "phase_enter", "ts": "t2"}\n'
        )
        report = migrate_run(tmp_path, apply=True)
        assert report.rows_read == 3
        assert report.rows_migrated == 2
        assert report.rows_skipped_malformed == 1

    def test_row_without_event_field_skipped(self, tmp_path: Path) -> None:
        _write_legacy_events(
            tmp_path,
            [
                {"ts": "t1", "no_event_field": True},
                {"event": "session_start", "ts": "t2"},
            ],
        )
        report = migrate_run(tmp_path, apply=True)
        assert report.rows_migrated == 1
        assert report.rows_skipped_malformed == 1


class TestMultiFileMigration:
    def test_merges_events_and_checkpoints(self, tmp_path: Path) -> None:
        meta = tmp_path / "meta"
        meta.mkdir()
        (meta / "events.jsonl").write_text(json.dumps({"event": "session_start", "ts": "t1"}) + "\n")
        (meta / "checkpoints.jsonl").write_text(json.dumps({"event": "checkpoint", "ts": "t2"}) + "\n")
        report = migrate_run(tmp_path, apply=True)
        assert report.rows_read == 2
        assert report.rows_migrated == 2
        assert len(report.source_files) == 2

    def test_parity_preserves_valid_row_count_timestamps_and_payloads(self, tmp_path: Path) -> None:
        meta = tmp_path / "meta"
        meta.mkdir()
        source_rows = [
            {"event": "session_start", "event_id": "evt_1", "ts": "2026-04-23T10:00:00+00:00", "session_id": "s1"},
            {"event": "checkpoint", "event_id": "evt_2", "ts": "2026-04-23T10:01:00+00:00", "message": "checkpoint-1"},
            {
                "event": "contract",
                "event_id": "evt_3",
                "ts": "2026-04-23T10:02:00+00:00",
                "contract_name": "schema-a",
                "schema_valid": True,
            },
        ]
        (meta / "events.jsonl").write_text(json.dumps(source_rows[0]) + "\n")
        (meta / "checkpoints.jsonl").write_text(json.dumps(source_rows[1]) + "\n")
        (meta / "contract_events.jsonl").write_text(json.dumps(source_rows[2]) + "\n")

        report = migrate_run(tmp_path, apply=True)

        assert report.rows_read == 3
        assert report.rows_migrated == 3
        target = report.target_file
        assert target is not None
        migrated_rows = [json.loads(line) for line in target.read_text().splitlines()]
        assert len(migrated_rows) == 3
        assert [row["event_id"] for row in migrated_rows] == ["evt_1", "evt_2", "evt_3"]
        assert [row["ts"].replace("Z", "+00:00") for row in migrated_rows] == [row["ts"] for row in source_rows]
        assert migrated_rows[1]["payload"]["message"] == "checkpoint-1"
        assert migrated_rows[2]["payload"]["contract_name"] == "schema-a"
        assert migrated_rows[2]["payload"]["schema_valid"] is True
