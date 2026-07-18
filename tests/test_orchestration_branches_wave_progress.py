"""Coverage-targeted wave progress tests for trw_mcp/tools/orchestration.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools.orchestration import _compute_wave_progress


class TestComputeWaveProgress:
    """Lines 329-383: _compute_wave_progress private function, fully uncovered."""

    def test_returns_none_for_empty_waves_list(self, tmp_path: Path) -> None:
        """Empty waves list returns None."""
        result = _compute_wave_progress({"waves": []}, tmp_path)
        assert result is None

    def test_returns_none_for_non_list_waves(self, tmp_path: Path) -> None:
        """Non-list waves value returns None."""
        result = _compute_wave_progress({"waves": "not-a-list"}, tmp_path)
        assert result is None

    def test_returns_none_when_waves_key_absent(self, tmp_path: Path) -> None:
        """Missing 'waves' key returns None (empty list default)."""
        result = _compute_wave_progress({}, tmp_path)
        assert result is None

    def test_single_complete_wave(self, tmp_path: Path) -> None:
        """Single wave with status=complete increments completed_waves."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 1
        assert result["completed_waves"] == 1
        assert result["active_wave"] is None

    def test_single_partial_wave_counts_as_completed(self, tmp_path: Path) -> None:
        """Wave with status=partial also increments completed_waves."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "partial", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["completed_waves"] == 1

    def test_active_wave_by_status(self, tmp_path: Path) -> None:
        """Wave with status=active sets active_wave."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": []},
                {"wave": 2, "status": "active", "shards": ["s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["active_wave"] == 2

    def test_active_wave_by_shard_active_count(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Wave with pending status but active shards also sets active_wave."""
        run_path = tmp_path / "run"
        shards_dir = run_path / "shards"
        shards_dir.mkdir(parents=True)

        writer.write_yaml(
            shards_dir / "manifest.yaml",
            {"shards": [{"id": "s1", "status": "active"}]},
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        assert result["active_wave"] == 1

    def test_shard_statuses_read_from_manifest(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Shard statuses from manifest.yaml are counted in wave details."""
        run_path = tmp_path / "run"
        shards_dir = run_path / "shards"
        shards_dir.mkdir(parents=True)

        writer.write_yaml(
            shards_dir / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "complete"},
                    {"id": "s2", "status": "complete"},
                    {"id": "s3", "status": "failed"},
                ]
            },
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1", "s2", "s3"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 1
        shard_counts = details[0]["shards"]
        assert isinstance(shard_counts, dict)
        assert shard_counts["complete"] == 2
        assert shard_counts["failed"] == 1

    def test_shard_manifest_missing_is_handled_gracefully(
        self,
        tmp_path: Path,
    ) -> None:
        """When shards/manifest.yaml does not exist, shard_statuses is empty (no error)."""
        run_path = tmp_path / "run"
        run_path.mkdir()

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1", "s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        shard_counts = details[0]["shards"]
        assert isinstance(shard_counts, dict)
        assert shard_counts["pending"] == 2

    def test_multiple_waves_mixed_states(
        self,
        tmp_path: Path,
        writer: FileStateWriter,
    ) -> None:
        """Multiple waves: complete + active + pending all counted correctly."""
        run_path = tmp_path / "run"
        (run_path / "shards").mkdir(parents=True)

        writer.write_yaml(
            run_path / "shards" / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "complete"},
                    {"id": "s2", "status": "complete"},
                    {"id": "s3", "status": "active"},
                ]
            },
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
                {"wave": 2, "status": "active", "shards": ["s3"]},
                {"wave": 3, "status": "pending", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        assert result["total_waves"] == 3
        assert result["completed_waves"] == 1
        assert result["active_wave"] == 2
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 3

    def test_non_dict_wave_entries_skipped(self, tmp_path: Path) -> None:
        """Non-dict items in waves list are skipped without error."""
        wave_data: dict[str, object] = {
            "waves": [
                "not-a-dict",
                42,
                {"wave": 1, "status": "complete", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 3
        assert result["completed_waves"] == 1

    def test_wave_details_structure(self, tmp_path: Path) -> None:
        """Wave details contain wave number, status, and shard counts dict."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 3, "status": "pending", "shards": ["s1", "s2", "s3"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 1
        entry = details[0]
        assert entry["wave"] == 3
        assert entry["status"] == "pending"
        shard_counts = entry["shards"]
        assert isinstance(shard_counts, dict)
        assert "total" in shard_counts
        assert shard_counts["total"] == 3

    def test_non_list_shards_treated_as_empty(self, tmp_path: Path) -> None:
        """When shards field is not a list, it is treated as empty."""
        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": "not-a-list"},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        details = result["wave_details"]
        assert isinstance(details, list)
        shard_counts = details[0]["shards"]
        assert isinstance(shard_counts, dict)
        assert shard_counts["total"] == 0

    def test_shard_manifest_with_corrupt_data_handled_gracefully(
        self,
        tmp_path: Path,
    ) -> None:
        """Corrupt shards manifest (non-list shards key) is handled without error."""
        run_path = tmp_path / "run"
        (run_path / "shards").mkdir(parents=True)
        run_path.joinpath("shards", "manifest.yaml").write_text(
            "shards: not-a-list\n",
            encoding="utf-8",
        )

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)

        assert result is not None
        assert result["total_waves"] == 1
        details = result["wave_details"]
        assert isinstance(details, list)
        assert len(details) == 1
        assert details[0]["shards"]["pending"] == 1

    def test_shard_manifest_read_error_is_caught(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """StateError from read_yaml for shard manifest is caught (lines 344-345)."""
        from trw_mcp.exceptions import StateError as TRWStateError

        run_path = tmp_path / "run"
        (run_path / "shards").mkdir(parents=True)
        (run_path / "shards" / "manifest.yaml").write_text(
            "shards: []\n",
            encoding="utf-8",
        )

        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools import _orchestration_phase as phase_mod

        reader = FileStateReader()
        original_read = reader.read_yaml

        def exploding_read(path: Path) -> dict[str, object]:
            if "manifest" in str(path):
                raise TRWStateError("simulated shard manifest read failure")
            return dict(original_read(path))

        monkeypatch.setattr(reader, "read_yaml", exploding_read)
        monkeypatch.setattr(phase_mod, "FileStateReader", lambda: reader)

        wave_data: dict[str, object] = {
            "waves": [
                {"wave": 1, "status": "active", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, run_path)
        assert result is not None
        assert result["total_waves"] == 1
        details = result["wave_details"]
        assert isinstance(details, list)
        assert details[0]["shards"]["pending"] == 1
