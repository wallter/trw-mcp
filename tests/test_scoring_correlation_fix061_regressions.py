"""Focused regressions for FIX-061 compatibility lookup behavior."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ruamel.yaml import YAML

from trw_mcp.scoring._correlation import process_outcome
from trw_mcp.scoring._io_boundary import _default_lookup_entry, _reset_yaml_path_index


def _write_receipt(trw_dir: Path, learning_id: str) -> None:
    receipt_file = trw_dir / "logs" / "recall_tracking.jsonl"
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    now_ts = datetime.now(timezone.utc).timestamp()
    receipt_file.write_text(json.dumps({"timestamp": now_ts, "learning_id": learning_id}) + "\n")


def _make_entry_data(learning_id: str) -> dict[str, object]:
    return {
        "id": learning_id,
        "summary": f"learning {learning_id}",
        "q_value": 0.5,
        "q_observations": 0,
        "recurrence": 1,
        "outcome_history": [],
    }


def test_process_outcome_uses_analytics_fallback_when_yaml_index_misses(
    tmp_path: Path,
) -> None:
    """Regression: exercise the compatibility branch after SQLite and cache miss."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_receipt(trw_dir, "compat-fallback-006")
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    entry_path = entries_dir / "compat-fallback-006.yaml"
    fallback_data = _make_entry_data("compat-fallback-006")

    _reset_yaml_path_index()

    with (
        patch("trw_mcp.scoring._io_boundary._build_yaml_path_index", return_value={}),
        patch("trw_mcp.state.memory_adapter.find_entry_by_id", return_value=None) as mock_sqlite,
        patch(
            "trw_mcp.state.analytics.find_entry_by_id",
            return_value=(entry_path, fallback_data),
        ) as mock_analytics,
        patch("trw_mcp.state.persistence.FileStateWriter.write_yaml") as mock_write_yaml,
    ):
        updated = process_outcome(trw_dir, 0.8, "tests_passed")

    assert updated == ["compat-fallback-006"]
    mock_sqlite.assert_called_once_with(trw_dir, "compat-fallback-006")
    mock_analytics.assert_called_once_with(entries_dir, "compat-fallback-006")
    mock_write_yaml.assert_called_once()
    written_path, written_data = mock_write_yaml.call_args.args
    assert written_path == entry_path
    assert written_data["id"] == "compat-fallback-006"


def test_default_lookup_entry_backfills_yaml_cache_after_analytics_fallback(
    tmp_path: Path,
) -> None:
    """A successful compatibility fallback should seed the TTL cache for repeats."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    entries_dir = trw_dir / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    entry_path = entries_dir / "compat-fallback-007.yaml"
    fallback_data = _make_entry_data("compat-fallback-007")

    yaml = YAML(typ="safe")
    with entry_path.open("w") as fh:
        yaml.dump(fallback_data, fh)

    _reset_yaml_path_index()

    with (
        patch("trw_mcp.scoring._io_boundary._build_yaml_path_index", return_value={}),
        patch("trw_mcp.state.memory_adapter.find_entry_by_id", return_value=None),
        patch(
            "trw_mcp.state.analytics.find_entry_by_id",
            return_value=(entry_path, dict(fallback_data)),
        ) as mock_analytics,
    ):
        first_path, first_data = _default_lookup_entry("compat-fallback-007", trw_dir, entries_dir)
        second_path, second_data = _default_lookup_entry("compat-fallback-007", trw_dir, entries_dir)

    assert first_path == entry_path
    assert second_path == entry_path
    assert first_data is not None
    assert second_data is not None
    assert second_data["id"] == "compat-fallback-007"
    assert mock_analytics.call_count == 1


def test_default_lookup_entry_rebuilds_cache_for_new_entries_dir(
    tmp_path: Path,
) -> None:
    """Regression: TTL cache must not leak YAML paths across different temp dirs."""
    first_trw_dir = tmp_path / "first" / ".trw"
    first_entries_dir = first_trw_dir / "learnings" / "entries"
    first_entries_dir.mkdir(parents=True)
    first_entry_path = first_entries_dir / "shared-id.yaml"
    second_trw_dir = tmp_path / "second" / ".trw"
    second_entries_dir = second_trw_dir / "learnings" / "entries"
    second_entries_dir.mkdir(parents=True)
    second_entry_path = second_entries_dir / "shared-id.yaml"

    yaml = YAML(typ="safe")
    first_data = _make_entry_data("shared-id")
    second_data = _make_entry_data("shared-id")
    second_data["summary"] = "learning from second dir"
    with first_entry_path.open("w") as fh:
        yaml.dump(first_data, fh)
    with second_entry_path.open("w") as fh:
        yaml.dump(second_data, fh)

    _reset_yaml_path_index()

    with patch("trw_mcp.state.memory_adapter.find_entry_by_id", return_value=None):
        resolved_first_path, _ = _default_lookup_entry("shared-id", first_trw_dir, first_entries_dir)
        resolved_second_path, resolved_second_data = _default_lookup_entry(
            "shared-id",
            second_trw_dir,
            second_entries_dir,
        )

    assert resolved_first_path == first_entry_path
    assert resolved_second_path == second_entry_path
    assert resolved_second_data is not None
    assert resolved_second_data["summary"] == "learning from second dir"
