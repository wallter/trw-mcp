"""Tests for surface tracking JSONL logging.

Covers SurfaceEvent schema, log_surface_event(), read_surface_events(),
and _rotate_jsonl() rotation logic.
"""

from __future__ import annotations

import json
from pathlib import Path

from trw_mcp.state.surface_tracking import (
    SurfaceEvent,
    _rotate_jsonl,
    log_surface_event,
    read_surface_events,
)


class TestLogSurfaceEvent:
    """Tests for log_surface_event() append behaviour and field completeness."""

    def test_event_all_fields(self, tmp_path: Path) -> None:
        """Surface event contains all 10 required fields."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(
            trw_dir,
            learning_id="L-a3Fq",
            surface_type="recall",
            phase="IMPLEMENT",
            domain_match=["auth", "api"],
            files_context=["src/auth.py"],
            prd_boosted=True,
            bandit_score=0.85,
            session_id="sess-001",
        )
        log_path = trw_dir / "logs" / "surface_tracking.jsonl"
        assert log_path.exists()
        event = json.loads(log_path.read_text().strip())
        assert event["learning_id"] == "L-a3Fq"
        assert event["surface_type"] == "recall"
        assert event["phase"] == "IMPLEMENT"
        assert event["domain_match"] == ["auth", "api"]
        assert event["prd_boosted"] is True
        assert event["bandit_score"] == 0.85
        assert event["exploration"] is False
        assert "surfaced_at" in event
        assert event["session_id"] == "sess-001"

    def test_session_id_present(self, tmp_path: Path) -> None:
        """Session ID is stored in the logged event."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-x", surface_type="nudge", session_id="s1")
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        assert event["session_id"] == "s1"

    def test_no_learning_content_in_logs(self, tmp_path: Path) -> None:
        """Surface events must NOT contain learning summary or detail."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-x", surface_type="recall")
        text = (trw_dir / "logs" / "surface_tracking.jsonl").read_text()
        assert "summary" not in text
        assert "detail" not in text
        assert "content" not in text

    def test_creates_logs_directory(self, tmp_path: Path) -> None:
        """Logs directory is auto-created when it does not exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-x", surface_type="recall")
        assert (trw_dir / "logs" / "surface_tracking.jsonl").exists()

    def test_append_multiple_events(self, tmp_path: Path) -> None:
        """Multiple calls append separate JSON lines."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(3):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")
        lines = (trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip().split("\n")
        assert len(lines) == 3

    def test_default_values(self, tmp_path: Path) -> None:
        """Omitted optional fields get sensible defaults."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-min", surface_type="session_start")
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        assert event["phase"] == ""
        assert event["domain_match"] == []
        assert event["files_context"] == []
        assert event["prd_boosted"] is False
        assert event["bandit_score"] == 0.0
        assert event["exploration"] is False
        assert event["session_id"] == ""

    def test_fail_open_on_bad_trw_dir(self, tmp_path: Path) -> None:
        """log_surface_event never raises, even with an invalid directory."""
        # Pass a path that cannot be created (file masquerading as dir)
        blocker = tmp_path / "blocked"
        blocker.write_text("not a dir")
        bad_trw_dir = blocker / "nested"
        # Should not raise
        log_surface_event(bad_trw_dir, learning_id="L-err", surface_type="recall")

    def test_surfaced_at_is_iso_format(self, tmp_path: Path) -> None:
        """surfaced_at field is a valid ISO 8601 timestamp."""
        from datetime import datetime

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(trw_dir, learning_id="L-ts", surface_type="nudge")
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        # Should parse without error
        dt = datetime.fromisoformat(event["surfaced_at"])
        assert dt.year >= 2026

    def test_files_context_stored(self, tmp_path: Path) -> None:
        """files_context list is persisted correctly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(
            trw_dir,
            learning_id="L-fc",
            surface_type="phase_transition",
            files_context=["a.py", "b.py", "c.py"],
        )
        event = json.loads((trw_dir / "logs" / "surface_tracking.jsonl").read_text().strip())
        assert event["files_context"] == ["a.py", "b.py", "c.py"]


class TestRotation:
    """Tests for _rotate_jsonl() file rotation."""

    def test_rotation_at_10mb(self, tmp_path: Path) -> None:
        """File exceeding max_bytes is renamed to .1 suffix."""
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * (10 * 1024 * 1024 + 1))
        _rotate_jsonl(log_path)
        rotated = tmp_path / "test.jsonl.1"
        assert rotated.exists()
        # Original should no longer exist (renamed, not copied)
        assert not log_path.exists()

    def test_no_rotation_under_limit(self, tmp_path: Path) -> None:
        """File under max_bytes is not rotated."""
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("small data")
        _rotate_jsonl(log_path)
        assert not (tmp_path / "test.jsonl.1").exists()
        assert log_path.exists()

    def test_rotation_overwrites_existing_backup(self, tmp_path: Path) -> None:
        """Existing .1 backup is overwritten on rotation."""
        log_path = tmp_path / "test.jsonl"
        rotated = tmp_path / "test.jsonl.1"
        rotated.write_text("old backup")
        log_path.write_text("x" * (10 * 1024 * 1024 + 1))
        _rotate_jsonl(log_path)
        assert rotated.exists()
        # Old backup content should be replaced
        assert rotated.read_text() != "old backup"

    def test_rotation_custom_max_bytes(self, tmp_path: Path) -> None:
        """Custom max_bytes threshold triggers rotation."""
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * 200)
        _rotate_jsonl(log_path, max_bytes=100)
        assert (tmp_path / "test.jsonl.1").exists()

    def test_rotation_missing_file(self, tmp_path: Path) -> None:
        """Rotation on non-existent file is a no-op."""
        log_path = tmp_path / "nonexistent.jsonl"
        _rotate_jsonl(log_path)  # Should not raise

    def test_rotation_exactly_at_limit(self, tmp_path: Path) -> None:
        """File exactly at max_bytes is NOT rotated (only exceeding)."""
        log_path = tmp_path / "test.jsonl"
        log_path.write_text("x" * 100)
        _rotate_jsonl(log_path, max_bytes=100)
        assert not (tmp_path / "test.jsonl.1").exists()


class TestReadSurfaceEvents:
    """Tests for read_surface_events() retrieval."""

    def test_reads_events(self, tmp_path: Path) -> None:
        """All logged events are returned."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(5):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")
        events = read_surface_events(trw_dir)
        assert len(events) == 5

    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        """Returns empty list when tracking file does not exist."""
        assert read_surface_events(tmp_path / ".trw") == []

    def test_max_events_cap(self, tmp_path: Path) -> None:
        """max_events limits returned events to most recent N."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(10):
            log_surface_event(trw_dir, learning_id=f"L-{i}", surface_type="recall")
        events = read_surface_events(trw_dir, max_events=3)
        assert len(events) == 3
        # Should be the last 3 events
        assert events[0]["learning_id"] == "L-7"
        assert events[2]["learning_id"] == "L-9"

    def test_preserves_event_types(self, tmp_path: Path) -> None:
        """Read events preserve original types (bool, float, list)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_surface_event(
            trw_dir,
            learning_id="L-typed",
            surface_type="recall",
            prd_boosted=True,
            bandit_score=0.42,
            domain_match=["api"],
        )
        events = read_surface_events(trw_dir)
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev["prd_boosted"], bool)
        assert isinstance(ev["bandit_score"], float)
        assert isinstance(ev["domain_match"], list)

    def test_fail_open_on_corrupt_jsonl(self, tmp_path: Path) -> None:
        """Returns empty list on malformed JSONL content."""
        trw_dir = tmp_path / ".trw"
        log_dir = trw_dir / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "surface_tracking.jsonl").write_text("not json\n{broken\n")
        events = read_surface_events(trw_dir)
        assert events == []

    def test_reads_valid_lines_from_mixed_content(self, tmp_path: Path) -> None:
        """Valid lines are returned even if other lines are empty."""
        trw_dir = tmp_path / ".trw"
        log_dir = trw_dir / "logs"
        log_dir.mkdir(parents=True)
        valid_event = json.dumps({"learning_id": "L-ok", "surface_type": "recall"})
        (log_dir / "surface_tracking.jsonl").write_text(f"\n{valid_event}\n\n")
        events = read_surface_events(trw_dir)
        assert len(events) == 1
        assert events[0]["learning_id"] == "L-ok"


class TestSurfaceEventTypedDict:
    """Tests for the SurfaceEvent TypedDict schema."""

    def test_surface_event_accepts_all_fields(self) -> None:
        """SurfaceEvent TypedDict can hold all documented fields."""
        event: SurfaceEvent = {
            "learning_id": "L-test",
            "surfaced_at": "2026-04-01T00:00:00+00:00",
            "surface_type": "nudge",
            "phase": "PLAN",
            "domain_match": ["auth"],
            "files_context": ["src/a.py"],
            "prd_boosted": False,
            "bandit_score": 0.0,
            "exploration": False,
            "session_id": "s-1",
        }
        assert event["learning_id"] == "L-test"
        assert event["surface_type"] == "nudge"
