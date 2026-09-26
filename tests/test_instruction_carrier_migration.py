"""Migration behavior for the retired externalization carrier — PRD-QUAL-143-FR01.

The externalization carrier used to write the TRW block into a ``.trw``
sidecar (``.trw/INSTRUCTIONS.md``, or ``.trw/COPILOT-INSTRUCTIONS.md`` for
Copilot) and leave only an ``@`` import in CLAUDE.md/AGENTS.md. That carrier
is gone — the block is inline again. What matters now is that an EXISTING
install shaped like the old carrier converts cleanly on ``update-project``:
the sidecar import is stripped, the block is folded back inline, the
now-orphaned sidecar file is deleted, the user's own prose survives
untouched, and a repeat update is a no-op.

The doctor-side ``classify_carrier_state`` / ``_check_instruction_carrier_state``
checks this file used to cover are gone with the carrier they diagnosed; the
instruction gate now reads the marker region directly (see
``test_instruction_carrier.py::TestDoctorPointerReport``).

The orphan-strip coverage at the bottom (PRD-QUAL-131-FR06) is unrelated to
the carrier and is retained as-is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_START = "<!-- trw:start -->"
_END = "<!-- trw:end -->"


#: This test's own commits run no git hooks: init_project installs TRW's post-commit hook, whose
#: background worker auto-starts a memory daemon after the test has returned (rc9 C2 FR07 leaks).
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


class TestLegacySidecarImportConvertsToInline:
    """The FR01 migration: an old sidecar-import install folds back inline.

    Drives the real bootstrap path (``init_project`` + ``update_project``)
    against a project shaped like a pre-FR01 install: a CLAUDE.md carrying
    only the ``@.trw/INSTRUCTIONS.md`` import inside the TRW markers, plus the
    sidecar file itself holding the old protocol text.
    """

    def _make_legacy_install(self, tmp_path: Path) -> tuple[Path, Path]:
        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="claude-code")

        sidecar = tmp_path / ".trw" / "INSTRUCTIONS.md"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            "<!-- TRW AUTO-GENERATED \u2014 do not edit. -->\n"
            "Call `trw_session_start()` first.\nDo NOT call `trw_deliver` unless\n",
            encoding="utf-8",
        )
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            f"# My Project\n\nMy own rules. Keep these.\n\n{_START}\n@.trw/INSTRUCTIONS.md\n{_END}\n",
            encoding="utf-8",
        )
        # Committed, as a real legacy install is: update-project never rewrites
        # an UNCOMMITTED file it did not write (PRD-INFRA-190 FR04).
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                *_NO_HOOKS,
                "commit",
                "-qm",
                "legacy",
            ],
            check=True,
        )
        return claude_md, sidecar

    def test_legacy_import_converts_to_inline_and_keeps_user_content(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import update_project

        claude_md, sidecar = self._make_legacy_install(tmp_path)

        update_project(tmp_path)

        after = claude_md.read_text(encoding="utf-8")
        # The whole point: TRW's carrier text leaves, the user's stays.
        assert "My own rules. Keep these." in after
        assert "@.trw/INSTRUCTIONS.md" not in after
        assert _START in after and "trw_session_start" in after
        assert not sidecar.exists()
        assert sidecar.with_name("INSTRUCTIONS.md.retired").is_file(), "set aside, never deleted"

    def test_conversion_is_idempotent(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import update_project

        claude_md, _sidecar = self._make_legacy_install(tmp_path)

        update_project(tmp_path)
        first = claude_md.read_text(encoding="utf-8")
        update_project(tmp_path)

        assert claude_md.read_text(encoding="utf-8") == first


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
        from pathlib import Path as _Path

        src = _Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
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
