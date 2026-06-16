"""Guard-condition tests for trw_mcp.telemetry.publisher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
from trw_mcp.telemetry.publisher import _HASH_FILE, _load_hashes, publish_learnings


class TestPublishOfflineMode:
    def test_publish_offline_mode_no_url(self) -> None:
        """Empty platform_url returns skipped_reason='offline_mode'."""
        cfg = _make_config(platform_url="", platform_telemetry_enabled=True)
        with patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] == "offline_mode"

    def test_publish_offline_mode_sharing_disabled(self) -> None:
        """PRD-SEC-004-FR05: learning_sharing_enabled=False returns
        skipped_reason='offline_mode' (content gate, independent of usage telemetry)."""
        cfg = _make_config(learning_sharing_enabled=False, platform_telemetry_enabled=True)
        with patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg):
            result = publish_learnings()

        assert result["skipped_reason"] == "offline_mode"


class TestPublishNoEntriesDir:
    def test_publish_no_entries_dir(self, tmp_path: Path) -> None:
        """Non-existent entries dir returns skipped_reason='no_entries'."""
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
        ):
            result = publish_learnings()

        assert result["published"] == 0
        assert result["skipped"] == 0
        assert result["errors"] == 0
        assert result["skipped_reason"] == "no_entries"


class TestLoadHashesFailOpen:
    """The hash sidecar is advisory cache state: _load_hashes must fail open to
    {} for every read/decode/JSON/shape failure and never raise."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _load_hashes(tmp_path) == {}

    def test_valid_object_passes_through(self, tmp_path: Path) -> None:
        (tmp_path / _HASH_FILE).write_text('{"L-a": "deadbeef0000abcd", "L-b": "0011223344556677"}', encoding="utf-8")
        assert _load_hashes(tmp_path) == {"L-a": "deadbeef0000abcd", "L-b": "0011223344556677"}

    def test_non_utf8_bytes_fail_open(self, tmp_path: Path) -> None:
        """Non-UTF-8 bytes raise UnicodeDecodeError (a ValueError, not OSError)."""
        (tmp_path / _HASH_FILE).write_bytes(b"\xff\xfe\x00\x01not utf8")
        assert _load_hashes(tmp_path) == {}

    def test_malformed_json_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / _HASH_FILE).write_text("{not: valid json", encoding="utf-8")
        assert _load_hashes(tmp_path) == {}

    @pytest.mark.parametrize("scalar", ["42", '"a string"', "true", "null", "3.14"])
    def test_scalar_json_fails_open(self, tmp_path: Path, scalar: str) -> None:
        """Scalar JSON would make dict(...) raise TypeError/ValueError — must fail open."""
        (tmp_path / _HASH_FILE).write_text(scalar, encoding="utf-8")
        assert _load_hashes(tmp_path) == {}

    @pytest.mark.parametrize("array", ["[1, 2, 3]", '["a", "b"]', '[["a", "b"]]', "[]"])
    def test_array_json_fails_open(self, tmp_path: Path, array: str) -> None:
        """JSON arrays (even of pairs) are not objects — must fail open, never dict()-coerce."""
        (tmp_path / _HASH_FILE).write_text(array, encoding="utf-8")
        assert _load_hashes(tmp_path) == {}

    def test_object_with_non_string_values_drops_malformed_rows(self, tmp_path: Path) -> None:
        """A partially-corrupt object keeps only well-formed str->str rows."""
        (tmp_path / _HASH_FILE).write_text(
            '{"L-good": "abcd1234abcd1234", "L-int": 123, "L-list": ["x"], "L-null": null}',
            encoding="utf-8",
        )
        assert _load_hashes(tmp_path) == {"L-good": "abcd1234abcd1234"}

    def test_diagnostics_are_structural_only(self, tmp_path: Path) -> None:
        """Failure logging records path/reason/error_class but never raw sidecar content."""
        secret = "s3cr3t-hash-value-should-never-be-logged"
        (tmp_path / _HASH_FILE).write_text(f'"{secret}"', encoding="utf-8")
        with capture_logs() as logs:
            assert _load_hashes(tmp_path) == {}
        failures = [entry for entry in logs if entry.get("event") == "publish_hash_load_failed"]
        assert len(failures) == 1
        assert failures[0]["reason"] == "not_a_json_object"
        assert failures[0]["error_class"] == "str"
        # No raw sidecar content (the secret value) may appear anywhere in the logs.
        assert all(secret not in str(value) for entry in logs for value in entry.values())


class TestPublishSurvivesCorruptSidecar:
    """Integration: a corrupt sidecar must not break publishing through the
    public publish_learnings entry point."""

    def test_corrupt_sidecar_still_publishes(self, tmp_path: Path) -> None:
        cfg = _make_config()
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        _write_learning(entries_dir, "learning.yaml", _make_learning(impact=0.9))
        # Poison the sidecar with non-UTF-8 bytes — the pre-fix code raised here.
        (entries_dir / _HASH_FILE).write_bytes(b"\xff\xfe corrupt sidecar")

        with (
            patch("trw_mcp.telemetry.publisher.get_config", return_value=cfg),
            patch("trw_mcp.telemetry.publisher.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.telemetry.publisher._post_learning", return_value=True),
        ):
            result = publish_learnings()

        assert result["published"] == 1
        assert result["errors"] == 0
        assert result["skipped_reason"] is None
