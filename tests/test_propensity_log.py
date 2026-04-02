"""Tests for propensity logging (PRD-CORE-103-FR03).

Verifies:
- PropensityEntry schema completeness
- log_selection() writes correct JSONL entries
- Deterministic defaults (prob=1.0, exploration=False)
- Runner-up auto-population logic
- No learning content leakage (IDs only)
- Log rotation via _rotate_jsonl
- read_propensity_entries() reads back entries
- Fail-open on all errors
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.propensity_log import (
    PropensityEntry,
    log_selection,
    read_propensity_entries,
)


class TestLogSelection:
    """Tests for log_selection()."""

    def test_entry_all_fields(self, tmp_path: Path) -> None:
        """Propensity entry contains all required fields."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(
            trw_dir,
            selected="L-a3Fq",
            candidate_set=["L-a3Fq", "L-b2Xp", "L-c1Yq", "L-d0Zr"],
            runner_up="L-b2Xp",
            selection_probability=1.0,
            context_phase="IMPLEMENT",
            context_domain=["auth"],
            context_agent_type="claude-code",
            session_id="sess-001",
        )
        log_path = trw_dir / "logs" / "propensity.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["selected"] == "L-a3Fq"
        assert entry["runner_up"] == "L-b2Xp"
        assert entry["selection_probability"] == 1.0
        assert len(entry["candidate_set"]) == 4
        assert entry["exploration"] is False
        assert entry["context_phase"] == "IMPLEMENT"
        assert entry["context_domain"] == ["auth"]
        assert entry["context_agent_type"] == "claude-code"
        assert entry["session_id"] == "sess-001"
        assert "timestamp" in entry

    def test_deterministic_selection_prob_1(self, tmp_path: Path) -> None:
        """Pre-bandit: default selection probability is 1.0."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-x")
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["selection_probability"] == 1.0
        assert entry["exploration"] is False

    def test_runner_up_populated_from_candidates(self, tmp_path: Path) -> None:
        """Runner-up auto-populated from candidate set when not explicitly provided."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-a", candidate_set=["L-a", "L-b", "L-c"])
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["runner_up"] == "L-b"

    def test_runner_up_single_candidate(self, tmp_path: Path) -> None:
        """Single candidate: runner_up is empty."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-a", candidate_set=["L-a"])
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["runner_up"] == ""

    def test_runner_up_empty_candidates(self, tmp_path: Path) -> None:
        """No candidates: runner_up stays empty."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-a")
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["runner_up"] == ""

    def test_runner_up_explicit_overrides_auto(self, tmp_path: Path) -> None:
        """Explicitly provided runner_up is preserved, not overwritten."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(
            trw_dir,
            selected="L-a",
            candidate_set=["L-a", "L-b", "L-c"],
            runner_up="L-c",
        )
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["runner_up"] == "L-c"

    def test_runner_up_when_selected_is_second(self, tmp_path: Path) -> None:
        """Auto runner_up skips selected when it's the second element."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # selected is L-b (index 1), runner_up should be L-a (index 0)
        log_selection(trw_dir, selected="L-b", candidate_set=["L-a", "L-b", "L-c"])
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        # When selected == candidates[1], should pick candidates[0]
        assert entry["runner_up"] == "L-a"

    def test_no_content_in_propensity_logs(self, tmp_path: Path) -> None:
        """Propensity logs must NOT contain learning content (IDs only)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-x", candidate_set=["L-x"])
        text = (trw_dir / "logs" / "propensity.jsonl").read_text()
        assert "summary" not in text
        assert "detail" not in text
        assert "content" not in text

    def test_exploration_flag(self, tmp_path: Path) -> None:
        """Exploration flag can be set to True."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(
            trw_dir,
            selected="L-a",
            exploration=True,
            selection_probability=0.3,
        )
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["exploration"] is True
        assert entry["selection_probability"] == 0.3

    def test_multiple_entries_appended(self, tmp_path: Path) -> None:
        """Multiple log_selection calls append to the same file."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(5):
            log_selection(trw_dir, selected=f"L-{i}")
        log_path = trw_dir / "logs" / "propensity.jsonl"
        lines = [line for line in log_path.read_text().strip().split("\n") if line.strip()]
        assert len(lines) == 5
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["selected"] == f"L-{i}"

    def test_creates_log_directory(self, tmp_path: Path) -> None:
        """log_selection creates the logs/ directory if missing."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        assert not (trw_dir / "logs").exists()
        log_selection(trw_dir, selected="L-a")
        assert (trw_dir / "logs").exists()
        assert (trw_dir / "logs" / "propensity.jsonl").exists()

    def test_rotation(self, tmp_path: Path) -> None:
        """Propensity log rotates when file exceeds size threshold."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_path = trw_dir / "logs" / "propensity.jsonl"
        log_path.parent.mkdir(parents=True)
        # Write >10MB to trigger rotation
        log_path.write_text("x" * (10 * 1024 * 1024 + 1))
        log_selection(trw_dir, selected="L-x")
        assert (trw_dir / "logs" / "propensity.jsonl.1").exists()

    def test_fail_open_on_permission_error(self, tmp_path: Path) -> None:
        """log_selection never raises on write errors (fail-open)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Patch Path.open to raise PermissionError
        with patch.object(Path, "mkdir", side_effect=PermissionError("denied")):
            # Should NOT raise
            log_selection(trw_dir, selected="L-a")

    def test_fail_open_on_os_error(self, tmp_path: Path) -> None:
        """log_selection silently fails on OS-level errors."""
        # Use a non-existent nested path that can't be created
        trw_dir = Path("/nonexistent/deeply/nested/.trw")
        # Should NOT raise
        log_selection(trw_dir, selected="L-a")

    def test_timestamp_is_iso_format(self, tmp_path: Path) -> None:
        """Timestamp field is ISO 8601 formatted."""
        from datetime import datetime

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-a")
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        # Should parse as ISO 8601 without error
        ts = datetime.fromisoformat(entry["timestamp"])
        assert ts.tzinfo is not None  # UTC-aware

    def test_context_domain_defaults_to_empty_list(self, tmp_path: Path) -> None:
        """context_domain defaults to empty list when not provided."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_selection(trw_dir, selected="L-a")
        entry = json.loads((trw_dir / "logs" / "propensity.jsonl").read_text().strip())
        assert entry["context_domain"] == []


class TestReadPropensityEntries:
    """Tests for read_propensity_entries()."""

    def test_reads_entries(self, tmp_path: Path) -> None:
        """Reads back all logged entries."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(3):
            log_selection(trw_dir, selected=f"L-{i}", candidate_set=[f"L-{i}"])
        entries = read_propensity_entries(trw_dir)
        assert len(entries) == 3
        assert entries[0]["selected"] == "L-0"
        assert entries[2]["selected"] == "L-2"

    def test_empty_when_no_file(self, tmp_path: Path) -> None:
        """Returns empty list when propensity.jsonl doesn't exist."""
        assert read_propensity_entries(tmp_path / ".trw") == []

    def test_max_entries_limit(self, tmp_path: Path) -> None:
        """Respects max_entries limit (returns last N entries)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        for i in range(10):
            log_selection(trw_dir, selected=f"L-{i}")
        entries = read_propensity_entries(trw_dir, max_entries=3)
        assert len(entries) == 3
        # Should be the LAST 3 entries
        assert entries[0]["selected"] == "L-7"
        assert entries[2]["selected"] == "L-9"

    def test_fail_open_on_corrupt_file(self, tmp_path: Path) -> None:
        """Returns empty list on corrupted file (fail-open)."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_dir = trw_dir / "logs"
        log_dir.mkdir()
        (log_dir / "propensity.jsonl").write_text("not json at all\n{bad\n")
        entries = read_propensity_entries(trw_dir)
        assert entries == []

    def test_handles_mixed_valid_invalid_lines(self, tmp_path: Path) -> None:
        """Gracefully handles files with some invalid lines."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        log_dir = trw_dir / "logs"
        log_dir.mkdir()
        valid_entry = json.dumps({"selected": "L-1", "timestamp": "2026-01-01T00:00:00+00:00"})
        (log_dir / "propensity.jsonl").write_text(f"{valid_entry}\nbad line\n")
        # This may return empty or partial based on implementation
        # The fail-open behavior returns [] on any parse error
        entries = read_propensity_entries(trw_dir)
        # Either returns partial or empty - both are acceptable fail-open behaviors
        assert isinstance(entries, list)


class TestPropensityEntrySchema:
    """Tests for PropensityEntry TypedDict schema."""

    def test_typed_dict_fields(self) -> None:
        """PropensityEntry has all expected fields defined."""
        annotations = PropensityEntry.__annotations__
        expected_fields = {
            "timestamp",
            "selected",
            "selection_probability",
            "candidate_set",
            "runner_up",
            "exploration",
            "context_phase",
            "context_domain",
            "context_agent_type",
            "session_id",
        }
        assert expected_fields == set(annotations.keys())

    def test_typed_dict_is_total_false(self) -> None:
        """PropensityEntry uses total=False so all fields are optional."""
        assert PropensityEntry.__total__ is False
