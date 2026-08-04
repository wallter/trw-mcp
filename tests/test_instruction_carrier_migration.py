"""Installer-side detection of instruction-file carrier state — PRD-CORE-240-FR07.

A project that installed TRW before externalization shipped is sitting on injected
framework text and has no way to know. Detection makes that visible so `update-project`
can convert it.

The rule that matters is what detection is allowed to READ. Three installer state
files could each plausibly answer "has this been migrated?" and all three are wrong:

- `.trw/installer-meta.yaml` is documented as history-only, "never a current runtime
  authority" (PRD-INFRA-164 D-26)
- `.trw/installed-version.json` is a reload nudge
- `.trw/managed-artifacts.yaml` tracks bundled-artifact content hashes

Keying off any of them reports a project as migrated because an installer once said
so, rather than because its file actually carries an include — the same
"we-never-checked reads as we-checked-and-it's-fine" shape the wiring-defect catalogue
is about. Detection therefore classifies the live file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server._subcommands_doctor import (
    _check_instruction_carrier_state,
    classify_carrier_state,
)

_START = "<!-- trw:start -->"
_END = "<!-- trw:end -->"
_HEADER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


def _write(root: Path, body: str) -> Path:
    target = root / "CLAUDE.md"
    target.write_text(body, encoding="utf-8")
    return target


class TestClassifyCarrierState:
    def test_doctor_reports_legacy_inline_vs_migrated(self, tmp_path: Path) -> None:
        """The FR07 headline: the two states must be distinguishable."""
        legacy = _write(
            tmp_path,
            f"# Project\n\nUser prose.\n\n{_HEADER}\n{_START}\n"
            "Call `trw_session_start()` first.\nDo NOT call `trw_deliver` unless\n"
            f"{_END}\n",
        )
        assert classify_carrier_state(legacy) == "legacy_inline"

        migrated = _write(
            tmp_path,
            f"# Project\n\nUser prose.\n\n{_HEADER}\n{_START}\n@.trw/INSTRUCTIONS.md\n{_END}\n",
        )
        assert classify_carrier_state(migrated) == "migrated"

    def test_absent_when_no_trw_block(self, tmp_path: Path) -> None:
        assert classify_carrier_state(_write(tmp_path, "# Project\n\nJust user prose.\n")) == "absent"

    def test_absent_when_file_missing(self, tmp_path: Path) -> None:
        assert classify_carrier_state(tmp_path / "nope.md") == "absent"

    def test_malformed_region_reports_legacy_not_migrated(self, tmp_path: Path) -> None:
        """FR07 explicitly: a half-written block must NOT read as migrated.

        "We could not tell" must not resolve to the reassuring answer — that is the
        exact failure shape this detection exists to surface.
        """
        half = _write(tmp_path, f"# Project\n\n{_START}\n@.trw/INSTRUCTIONS.md\n")

        assert classify_carrier_state(half) != "migrated"

    def test_block_with_an_import_plus_prose_is_legacy(self, tmp_path: Path) -> None:
        """A partially-converted block is not converted."""
        mixed = _write(
            tmp_path,
            f"{_START}\n@.trw/INSTRUCTIONS.md\nDo NOT call `trw_deliver` unless\n{_END}\n",
        )

        assert classify_carrier_state(mixed) == "legacy_inline"


class TestDoctorCarrierCheck:
    def test_legacy_only_project_warns_with_a_next_action(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            f"# P\n\n{_START}\nCall `trw_session_start()` first.\n{_END}\n",
        )

        result = _check_instruction_carrier_state(tmp_path, TRWConfig())

        assert result.status == "WARN"
        assert "inline" in result.message
        assert "update-project" in result.message, "a warning must name the next action"

    def test_migrated_project_passes(self, tmp_path: Path) -> None:
        _write(tmp_path, f"# P\n\n{_START}\n@.trw/INSTRUCTIONS.md\n{_END}\n")

        result = _check_instruction_carrier_state(tmp_path, TRWConfig())

        assert result.status == "PASS"
        assert "referencing" in result.message

    def test_no_surface_skips_rather_than_failing(self, tmp_path: Path) -> None:
        result = _check_instruction_carrier_state(tmp_path, TRWConfig())

        assert result.status == "SKIP"

    def test_inline_is_never_a_failure(self, tmp_path: Path) -> None:
        """Inline is CORRECT for a client that cannot resolve an include.

        Failing it would train operators to ignore the check on the majority of
        projects — a gate with a high false-positive rate and no recourse.
        """
        _write(tmp_path, f"# P\n\n{_START}\nCall `trw_session_start()` first.\n{_END}\n")

        assert _check_instruction_carrier_state(tmp_path, TRWConfig()).status != "FAIL"


class TestDetectionReadsTheFileNotInstallerState:
    """FR07's binding constraint, asserted rather than described."""

    @pytest.mark.parametrize(
        "state_file",
        [".trw/installer-meta.yaml", ".trw/installed-version.json", ".trw/managed-artifacts.yaml"],
    )
    def test_installer_state_files_are_not_opened(self, tmp_path: Path, state_file: str) -> None:
        """A state file claiming otherwise must not change the verdict."""
        _write(tmp_path, f"# P\n\n{_START}\nCall `trw_session_start()` first.\n{_END}\n")
        path = tmp_path / state_file
        path.parent.mkdir(parents=True, exist_ok=True)
        # Content that would flip the answer if it were consulted.
        path.write_text('{"migrated": true, "carrier": "import"}\n', encoding="utf-8")

        assert classify_carrier_state(tmp_path / "CLAUDE.md") == "legacy_inline", (
            f"{state_file} must not influence detection; the live file is the authority"
        )

    def test_verdict_follows_the_file_when_it_changes(self, tmp_path: Path) -> None:
        """Convert the file and the verdict must move without any state-file update."""
        target = _write(tmp_path, f"# P\n\n{_START}\nCall `trw_session_start()` first.\n{_END}\n")
        assert classify_carrier_state(target) == "legacy_inline"

        target.write_text(f"# P\n\n{_START}\n@.trw/INSTRUCTIONS.md\n{_END}\n", encoding="utf-8")

        assert classify_carrier_state(target) == "migrated"


class TestExistingInstallMigrates:
    """The other half of FR07: detection must be followed by an actual conversion.

    Detection that reports "legacy" forever, with no path off it, would be a
    diagnosis without a treatment. This drives the real bootstrap path against a
    project shaped like a pre-externalization install.
    """

    def test_legacy_install_converts_on_update_and_keeps_user_content(self, tmp_path: Path) -> None:
        import subprocess

        from trw_mcp.bootstrap import init_project, update_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="claude-code")

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# My Project\n\nMy own rules. Keep these.\n\n"
            f"{_START}\n"
            "Call `trw_session_start()` first.\n"
            "Do NOT call `trw_deliver` unless\n"
            "lots of injected protocol text\n"
            f"{_END}\n",
            encoding="utf-8",
        )
        assert classify_carrier_state(claude_md) == "legacy_inline", "precondition"
        assert _check_instruction_carrier_state(tmp_path, TRWConfig()).status == "WARN"

        update_project(tmp_path)

        after = claude_md.read_text(encoding="utf-8")
        assert classify_carrier_state(claude_md) == "migrated"
        assert _check_instruction_carrier_state(tmp_path, TRWConfig()).status == "PASS"

        # The whole point: TRW's text leaves, the user's stays.
        assert "My own rules. Keep these." in after
        assert "lots of injected protocol text" not in after
        assert [ln.strip() for ln in after.splitlines() if ln.strip().startswith("@")] == ["@.trw/INSTRUCTIONS.md"]
        assert "trw_session_start" in (tmp_path / ".trw" / "INSTRUCTIONS.md").read_text(encoding="utf-8")

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        import subprocess

        from trw_mcp.bootstrap import init_project, update_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="claude-code")
        update_project(tmp_path)
        first = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

        update_project(tmp_path)

        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == first


class TestOrphanStripSurfaceIsClaudeMdOnly:
    """PRD-QUAL-131-FR06: only the CLAUDE.md orphan-strip exists, and it runs.

    ``strip_orphaned_agents_md_block`` was removed. Six tests used to exercise
    it here and they proved nothing about the product: the function had ZERO
    production call sites, so the AGENTS.md orphan-strip they verified never
    ran. Its commit message claimed "Verified: opencode-only detection strips
    and is idempotent" -- a property of a function nothing calls.

    The one assertion worth keeping is repointed rather than deleted. The
    prose-marker case is a real safety regression (a substring marker matcher
    once destroyed 705 ROADMAP lines), and ``_strip_trw_section`` is shared, so
    it is now exercised through the sibling that production actually invokes --
    strictly better coverage than testing it through dead code.
    """

    _S = "<!-- trw:start -->"
    _E = "<!-- trw:end -->"

    def test_the_agents_md_orphan_strip_is_wired_not_merely_defined(self) -> None:
        """It was deleted for having zero callers. It is back — with a caller.

        The original shipped with six passing tests and no production call site:
        every test invoked the helper directly, so they proved the code worked
        and said nothing about whether it ran. Deleting it was correct. The NEED
        was real though (a project installed before opencode's AGENTS.md was
        withdrawn keeps a frozen block forever), so it is restored and called
        from the update path. This asserts the CALL SITE, since that is the part
        that was missing; the behavior itself is covered end-to-end through
        `update_project` in test_instruction_include_matrix.py.
        """
        import inspect

        from trw_mcp.bootstrap import _template_updater
        from trw_mcp.state.claude_md import _orphan_strip

        assert hasattr(_orphan_strip, "strip_orphaned_agents_md_block")
        assert "strip_orphaned_agents_md_block(" in inspect.getsource(_template_updater._update_mcp_config), (
            "restored but unwired again"
        )

    def test_the_surviving_sibling_has_production_call_sites(self) -> None:
        """The distinction FR06 turns on: called vs merely defined.

        This is what made one of the pair removable and the other not, so it is
        asserted rather than left to the commit message.
        """
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
        callers = sorted(
            path.name
            for path in src.rglob("*.py")
            if "__pycache__" not in path.as_posix()
            and path.name not in {"_orphan_strip.py", "_agents_md.py"}
            and "strip_orphaned_claude_md_block" in path.read_text(encoding="utf-8")
        )
        assert callers == ["_init_project.py", "_template_updater.py"]

    def test_a_marker_mentioned_in_prose_does_not_delete_user_content(self, tmp_path: Path) -> None:
        """``_strip_trw_section`` DELETES what it spans, so matching must be line-anchored.

        Repointed from the removed AGENTS.md entry point to the live CLAUDE.md
        one. Same shared helper, but now reached through a path production runs.
        """
        from unittest.mock import patch

        from trw_mcp.state.claude_md import _agents_md as am_mod

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            f"# Doc\n\nWe delimit with `{self._S}`.\n\nKeep me.\n\n{self._S}\nblock\n{self._E}\n",
            encoding="utf-8",
        )

        with patch.object(am_mod, "detect_ide", return_value=["codex"]):
            am_mod.strip_orphaned_claude_md_block(tmp_path)

        text = claude.read_text(encoding="utf-8")
        assert "Keep me." in text, "content around a prose mention was deleted"
        assert f"We delimit with `{self._S}`." in text

    def test_user_content_outside_the_markers_survives(self, tmp_path: Path) -> None:
        """CONSTITUTION HB-2: only the TRW-marked region may be removed.

        Also repointed to the live sibling, so the boundary guarantee is proved
        on the path that actually strips files.
        """
        from unittest.mock import patch

        from trw_mcp.state.claude_md import _agents_md as am_mod

        claude = tmp_path / "CLAUDE.md"
        claude.write_text(
            f"# Mine\n\nMy own rules.\n\n{self._S}\nStale injected TRW text\n{self._E}\n\nMore of mine.\n",
            encoding="utf-8",
        )

        with patch.object(am_mod, "detect_ide", return_value=["codex"]):
            assert am_mod.strip_orphaned_claude_md_block(tmp_path) is True

        text = claude.read_text(encoding="utf-8")
        assert "Stale injected TRW text" not in text
        assert "My own rules." in text
        assert "More of mine." in text
