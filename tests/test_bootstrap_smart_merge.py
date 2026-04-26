"""Tests for shared marker-based smart-merge utilities and the hardened
Gemini settings.json write path.

Issue 2 (Mac install): the user had a pre-existing ``GEMINI.md`` and the
install ``erred out``. The legacy code paths in ``_gemini.py`` and
``_copilot.py`` had identical smart-merge logic with copy-pasted differences,
and the Gemini ``settings.json`` writer had no schema validation against
user-authored / Gemini-CLI-authored existing files.

This test file covers the consolidated helpers in ``_file_ops.py``:
``smart_merge_marker_section`` and ``write_instruction_file_with_merge``,
plus the hardened ``generate_gemini_mcp_config`` schema-recovery behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._file_ops import (
    smart_merge_marker_section,
    write_instruction_file_with_merge,
)


_START = "<!-- trw:test:start -->"
_END = "<!-- trw:test:end -->"


def _section(body: str) -> str:
    return f"{_START}\n{body}\n{_END}"


# ── smart_merge_marker_section ────────────────────────────────────────────


class TestSmartMergeMarkerSection:
    def test_empty_existing_returns_section_with_newline(self) -> None:
        out = smart_merge_marker_section(
            "", _section("hello"), start_marker=_START, end_marker=_END
        )
        assert out == _section("hello") + "\n"

    def test_replaces_between_markers_preserving_user_content(self) -> None:
        existing = (
            "# My GEMINI.md\n\nuser preamble\n\n"
            + _section("OLD trw body")
            + "\n\nuser postamble\n"
        )
        new = _section("NEW trw body")

        out = smart_merge_marker_section(
            existing, new, start_marker=_START, end_marker=_END
        )

        assert "user preamble" in out
        assert "user postamble" in out
        assert "OLD trw body" not in out
        assert "NEW trw body" in out

    def test_idempotent_when_section_already_present(self) -> None:
        section = _section("identical body")
        existing = "before\n\n" + section + "\n\nafter\n"

        once = smart_merge_marker_section(
            existing, section, start_marker=_START, end_marker=_END
        )
        twice = smart_merge_marker_section(
            once, section, start_marker=_START, end_marker=_END
        )

        assert once == existing
        assert twice == once

    def test_appends_when_no_markers(self) -> None:
        existing = "user-authored prose\nwith multiple lines\n"
        section = _section("trw body")

        out = smart_merge_marker_section(
            existing, section, start_marker=_START, end_marker=_END
        )

        # User content preserved verbatim at the head, TRW section appended.
        assert out.startswith("user-authored prose\nwith multiple lines")
        assert out.rstrip("\n").endswith(_END)

    def test_appends_when_only_start_marker_present(self) -> None:
        existing = f"{_START}\nuser opened the marker by accident\n"
        section = _section("trw body")

        out = smart_merge_marker_section(
            existing, section, start_marker=_START, end_marker=_END
        )

        # Treated as no valid pair → append.
        assert out.endswith(section + "\n")

    def test_appends_when_end_before_start_corrupted(self) -> None:
        existing = f"{_END}\nmangled\n{_START}\n"
        section = _section("trw body")

        out = smart_merge_marker_section(
            existing, section, start_marker=_START, end_marker=_END
        )

        assert out.endswith(section + "\n")

    def test_empty_existing_with_strip(self) -> None:
        out = smart_merge_marker_section(
            "   \n\n", _section("body"), start_marker=_START, end_marker=_END
        )
        # No leading separator because existing strips to empty.
        assert out.startswith(_START)


# ── write_instruction_file_with_merge ────────────────────────────────────


class TestWriteInstructionFileWithMerge:
    def test_creates_when_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=_section("body"),
            start_marker=_START,
            end_marker=_END,
            force=False,
            result=result,
        )

        assert target.exists()
        assert "FOO.md" in result["created"]
        assert result["preserved"] == []
        assert result["errors"] == []

    def test_preserves_when_already_identical(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        section = _section("body")
        target.write_text(section + "\n", encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=section,
            start_marker=_START,
            end_marker=_END,
            force=False,
            result=result,
        )

        assert "FOO.md" in result["preserved"]
        assert result["created"] == []
        assert result["updated"] == []

    def test_updates_when_section_changed(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        target.write_text(
            "user preamble\n\n" + _section("OLD") + "\n", encoding="utf-8"
        )
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=_section("NEW"),
            start_marker=_START,
            end_marker=_END,
            force=False,
            result=result,
        )

        assert "FOO.md" in result["updated"]
        text = target.read_text(encoding="utf-8")
        assert "user preamble" in text
        assert "NEW" in text and "OLD" not in text

    def test_force_rewrites_even_with_user_content(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        target.write_text("user-authored content with no markers\n", encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

        write_instruction_file_with_merge(
            target_path=target,
            rel_path="FOO.md",
            trw_section=_section("trw"),
            start_marker=_START,
            end_marker=_END,
            force=True,
            result=result,
        )

        # force=True overwrites entirely — user content is lost (documented behavior).
        text = target.read_text(encoding="utf-8")
        assert text == _section("trw")
        assert "FOO.md" in result["updated"]

    def test_idempotent_after_initial_create(self, tmp_path: Path) -> None:
        target = tmp_path / "FOO.md"
        section = _section("body")

        for run_idx in range(3):
            result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
            write_instruction_file_with_merge(
                target_path=target,
                rel_path="FOO.md",
                trw_section=section,
                start_marker=_START,
                end_marker=_END,
                force=False,
                result=result,
            )
            if run_idx == 0:
                assert "FOO.md" in result["created"]
            else:
                assert "FOO.md" in result["preserved"]


# ── Gemini settings.json hardening ───────────────────────────────────────


class TestGenerateGeminiMcpConfigHardening:
    """Issue 2 — hardened settings.json schema recovery."""

    def _import_under_test(self):  # noqa: ANN202
        from trw_mcp.bootstrap._gemini import generate_gemini_mcp_config

        return generate_gemini_mcp_config

    def test_writes_fresh_when_settings_missing(self, tmp_path: Path) -> None:
        gen = self._import_under_test()
        result = gen(tmp_path)

        settings = tmp_path / ".gemini" / "settings.json"
        assert settings.is_file()
        data = json.loads(settings.read_text())
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        assert data["mcpServers"]["trw"]["trust"] is True
        assert result["errors"] == []

    def test_preserves_unrelated_user_keys(self, tmp_path: Path) -> None:
        settings = tmp_path / ".gemini" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "mcpServers": {
                        "other-server": {"command": "/usr/bin/other"},
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        gen = self._import_under_test()
        gen(tmp_path)

        data = json.loads(settings.read_text())
        assert data["theme"] == "dark"
        assert "other-server" in data["mcpServers"], "other servers preserved"
        assert "trw" in data["mcpServers"], "trw server added"

    def test_idempotent_second_run_is_preserved(self, tmp_path: Path) -> None:
        gen = self._import_under_test()
        first = gen(tmp_path)
        second = gen(tmp_path)

        # First run created. Second run sees identical bytes → preserved.
        assert any("settings.json" in p for p in first["created"])
        assert any("settings.json" in p for p in second.get("preserved", []))

    def test_recovers_from_invalid_json_with_backup(self, tmp_path: Path) -> None:
        settings = tmp_path / ".gemini" / "settings.json"
        settings.parent.mkdir()
        settings.write_text("this is not { valid json", encoding="utf-8")

        gen = self._import_under_test()
        result = gen(tmp_path)

        # Backup exists with the user content; settings.json is now valid.
        backup = settings.with_suffix(settings.suffix + ".bak")
        assert backup.is_file()
        assert "this is not { valid json" in backup.read_text()
        new_data = json.loads(settings.read_text())
        assert "mcpServers" in new_data
        assert any("not valid JSON" in w for w in result.get("warnings", []))
        # Errors list stays empty — recovery is non-fatal.
        assert result["errors"] == []

    def test_recovers_from_non_object_root_with_backup(self, tmp_path: Path) -> None:
        settings = tmp_path / ".gemini" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

        gen = self._import_under_test()
        result = gen(tmp_path)

        backup = settings.with_suffix(settings.suffix + ".bak")
        assert backup.is_file()
        new_data = json.loads(settings.read_text())
        assert isinstance(new_data, dict)
        assert any("not a JSON object" in w for w in result.get("warnings", []))

    def test_replaces_non_dict_mcpServers(self, tmp_path: Path) -> None:
        settings = tmp_path / ".gemini" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(
            json.dumps({"theme": "light", "mcpServers": "this should be a dict"}),
            encoding="utf-8",
        )

        gen = self._import_under_test()
        gen(tmp_path)

        data = json.loads(settings.read_text())
        assert data["theme"] == "light"
        assert isinstance(data["mcpServers"], dict)
        assert "trw" in data["mcpServers"]


# ── Cross-client parity check ────────────────────────────────────────────


class TestClientParityGoldenPath:
    """Both Gemini and Copilot instruction generators must follow the same
    contract now that they share the underlying helper.
    """

    @pytest.mark.parametrize(
        "module_path,filename,marker_start",
        [
            ("trw_mcp.bootstrap._gemini", "GEMINI.md", "<!-- trw:gemini:start -->"),
            (
                "trw_mcp.bootstrap._copilot",
                ".github/copilot-instructions.md",
                "<!-- trw:copilot:start -->",
            ),
        ],
    )
    def test_smart_merge_preserves_pre_existing_user_content(
        self, tmp_path: Path, module_path: str, filename: str, marker_start: str
    ) -> None:
        import importlib

        module = importlib.import_module(module_path)
        # Both modules expose generate_*_instructions(target_dir, *, force=False).
        if module_path.endswith("_gemini"):
            gen = module.generate_gemini_instructions
        else:
            gen = module.generate_copilot_instructions

        target = tmp_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "# user-authored\nimportant project rules\n",
            encoding="utf-8",
        )

        result = gen(tmp_path)

        text = target.read_text(encoding="utf-8")
        assert "user-authored" in text, "user content preserved"
        assert "important project rules" in text
        assert marker_start in text, "TRW section appended"
        assert result["errors"] == []
