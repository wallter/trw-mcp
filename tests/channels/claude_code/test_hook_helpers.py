"""Tests for channels/claude_code/_hook_helpers.py (PRD-DIST-2405 FR06, FR29)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trw_mcp.channels.claude_code._hook_helpers import (
    _CEREMONY_MODE_FIELD,
    DEFAULT_SKIP_EXTENSIONS,
    CC03_HINTS_DIR,
    format_t0_beacon,
    format_t1_hint,
    format_t2_hint,
    prune_hint_files,
    read_cc03_config,
    write_hint_file,
)


class TestCeremonyModeField:
    def test_ceremony_mode_field_name(self) -> None:
        """FR06 (P1-02 fix): field name is 'ceremony_mode'."""
        assert _CEREMONY_MODE_FIELD == "ceremony_mode"

    def test_ceremony_mode_field_exists_in_config(self) -> None:
        """Verify ceremony_mode is a real TRWConfig field."""
        from trw_mcp.models.config._fields_ceremony import _CeremonyFields

        # _CeremonyFields is a plain class (mixin), not a Pydantic model
        # Check via __annotations__ — populated by type annotations in the class body
        assert hasattr(_CeremonyFields, "__annotations__"), \
            "_CeremonyFields should have __annotations__"
        assert "ceremony_mode" in _CeremonyFields.__annotations__


class TestDefaultSkipExtensions:
    def test_md_in_skip_list(self) -> None:
        """P0-10 fix: .md files are skipped by default."""
        assert ".md" in DEFAULT_SKIP_EXTENSIONS

    def test_txt_in_skip_list(self) -> None:
        assert ".txt" in DEFAULT_SKIP_EXTENSIONS

    def test_rst_in_skip_list(self) -> None:
        assert ".rst" in DEFAULT_SKIP_EXTENSIONS

    def test_lock_in_skip_list(self) -> None:
        assert ".lock" in DEFAULT_SKIP_EXTENSIONS

    def test_yaml_not_in_skip_list(self) -> None:
        """P0-10 fix: YAML files must NOT be in skip list (they have blast radius)."""
        assert ".yaml" not in DEFAULT_SKIP_EXTENSIONS

    def test_json_not_in_skip_list(self) -> None:
        assert ".json" not in DEFAULT_SKIP_EXTENSIONS

    def test_toml_not_in_skip_list(self) -> None:
        assert ".toml" not in DEFAULT_SKIP_EXTENSIONS

    def test_py_not_in_skip_list(self) -> None:
        assert ".py" not in DEFAULT_SKIP_EXTENSIONS


class TestReadCc03Config:
    def test_defaults_when_no_config_file(self, tmp_path: Path) -> None:
        """FR09: cc03_hook_enabled defaults to False (opt-in)."""
        config = read_cc03_config(tmp_path)
        assert config["cc03_hook_enabled"] is False

    def test_enabled_when_config_true(self, tmp_path: Path) -> None:
        """FR09: cc03_hook_enabled=True when config says so."""
        config_file = tmp_path / ".trw" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("cc03_hook_enabled: true\n", encoding="utf-8")
        config = read_cc03_config(tmp_path)
        assert config["cc03_hook_enabled"] is True

    def test_skip_extensions_default(self, tmp_path: Path) -> None:
        config = read_cc03_config(tmp_path)
        assert ".md" in config["skip_extensions"]

    def test_fail_open_on_parse_error(self, tmp_path: Path) -> None:
        """Fail-open: parse error returns safe defaults."""
        config_file = tmp_path / ".trw" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("{{invalid yaml}}", encoding="utf-8")
        config = read_cc03_config(tmp_path)
        assert config["cc03_hook_enabled"] is False


class TestFormatters:
    def test_t0_beacon_short(self) -> None:
        """FR28: T0 output ≤ 20 tokens (≤ ~100 chars)."""
        output = format_t0_beacon()
        assert len(output) <= 120

    def test_t1_hint_with_learnings(self) -> None:
        learnings = [
            {"summary": "Use structlog, not logging", "detail": "..."},
            {"summary": "350 LOC gate enforced", "detail": "..."},
        ]
        output = format_t1_hint(learnings)
        assert "structlog" in output or "350" in output

    def test_t1_hint_without_learnings(self) -> None:
        output = format_t1_hint([])
        assert "No learnings" in output or "trw_before_edit_hint" in output

    def test_t2_hint_includes_risk_score(self) -> None:
        output = format_t2_hint(
            file_path="src/module.py",
            risk_score=0.82,
            hotspot_warnings=["DO-NOT-REMOVE markers"],
            co_change_neighbors=["src/_schema.py"],
            inferred_tests=["tests/test_module.py"],
        )
        assert "0.82" in output

    def test_t2_hint_within_320_chars(self) -> None:
        """FR28: T2 output ≤ 80 tokens (~320 chars)."""
        output = format_t2_hint(
            file_path="src/module.py",
            risk_score=0.82,
            hotspot_warnings=["warn1", "warn2", "warn3"],
            co_change_neighbors=["a.py", "b.py"],
            inferred_tests=["tests/test_m.py"],
        )
        assert len(output) <= 320

    def test_t2_hint_hard_cap_at_320(self) -> None:
        """FR32: output is hard-capped at 320 chars."""
        long_warning = "A" * 400
        output = format_t2_hint(
            file_path="src/module.py",
            risk_score=0.5,
            hotspot_warnings=[long_warning, long_warning, long_warning],
            co_change_neighbors=[],
            inferred_tests=[],
        )
        assert len(output) <= 320


class TestWriteHintFile:
    def test_hint_file_written_with_tool_use_id(self, tmp_path: Path) -> None:
        """FR29 (P1-04): hint file is keyed on tool_use_id."""
        hints_dir = tmp_path / "hints"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id="tool-001",
            file_path="/repo/src/module.py",
            tier="T2",
            hint_emitted=True,
            tokens_emitted=68,
            distill_status="hint_available",
        )
        hint_file = hints_dir / "tool-001.json"
        assert hint_file.exists()
        data = json.loads(hint_file.read_text(encoding="utf-8"))
        assert data["tool_use_id"] == "tool-001"
        assert data["file_path"] == "/repo/src/module.py"
        assert data["hint_emitted"] is True
        assert data["tier"] == "T2"

    def test_no_cross_contamination_between_tools(self, tmp_path: Path) -> None:
        """FR08 (P1-04): two concurrent hint files don't cross-contaminate."""
        hints_dir = tmp_path / "hints"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id="tool-001",
            file_path="/repo/file_a.py",
            tier="T2",
            hint_emitted=True,
            tokens_emitted=50,
            distill_status="hint_available",
        )
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id="tool-002",
            file_path="/repo/file_b.py",
            tier="T1",
            hint_emitted=True,
            tokens_emitted=30,
            distill_status="tier_required",
        )
        data_001 = json.loads((hints_dir / "tool-001.json").read_text(encoding="utf-8"))
        data_002 = json.loads((hints_dir / "tool-002.json").read_text(encoding="utf-8"))
        assert data_001["file_path"] == "/repo/file_a.py"
        assert data_002["file_path"] == "/repo/file_b.py"

    def test_hint_file_schema(self, tmp_path: Path) -> None:
        """FR29: hint file has required schema fields."""
        hints_dir = tmp_path / "hints"
        write_hint_file(
            hints_dir=hints_dir,
            tool_use_id="tool-abc",
            file_path="/repo/x.py",
            tier="T0",
            hint_emitted=False,
            tokens_emitted=0,
            distill_status="sidecar_missing",
        )
        data = json.loads((hints_dir / "tool-abc.json").read_text(encoding="utf-8"))
        required = {"ts", "file_path", "tier", "hint_emitted", "tokens_emitted", "distill_status", "tool_use_id"}
        assert required.issubset(data.keys())


class TestPruneHintFiles:
    def test_prune_old_files(self, tmp_path: Path) -> None:
        """FR35: files older than TTL are pruned."""
        hints_dir = tmp_path / "hints"
        hints_dir.mkdir()
        old_file = hints_dir / "old.json"
        old_file.write_text('{"ts": "old"}', encoding="utf-8")
        # Set mtime to 2 days ago
        old_time = time.time() - 2 * 86400
        import os

        os.utime(old_file, (old_time, old_time))
        removed = prune_hint_files(hints_dir, ttl_seconds=86400)
        assert removed == 1
        assert not old_file.exists()

    def test_keep_fresh_files(self, tmp_path: Path) -> None:
        hints_dir = tmp_path / "hints"
        hints_dir.mkdir()
        fresh_file = hints_dir / "fresh.json"
        fresh_file.write_text('{"ts": "now"}', encoding="utf-8")
        removed = prune_hint_files(hints_dir, ttl_seconds=86400)
        assert removed == 0
        assert fresh_file.exists()

    def test_prune_nonexistent_dir(self, tmp_path: Path) -> None:
        """Pruning a non-existent directory returns 0 without error."""
        removed = prune_hint_files(tmp_path / "nonexistent")
        assert removed == 0
