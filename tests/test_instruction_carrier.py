"""PRD-CORE-203, narrowed by PRD-QUAL-143-FR01: instruction-file carrier.

The externalization carrier (writing the TRW block into a ``.trw``
sidecar behind an ``@`` import) is gone — the block is always ``INLINE``
unless the target is a thin single-source pointer file (``POINTER_SKIP``).
Covers what remains: file classification (FR03/NFR04), the two-mode
carrier decision, the shared pointer-skip guard used by both appenders,
pointer healing (FR06), and the dispatcher integration that reports
``carrier_mode``/``pointer_skips`` end-to-end (including the cache-hit
path).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md._instruction_carrier import (
    CarrierMode,
    InstructionFileClass,
    InstructionFileClassification,
    apply_carrier,
    classify_instruction_file,
    heal_pointer,
    pointer_skip_guard,
    resolve_carrier_mode,
)
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    merge_trw_section,
)

# A representative rendered TRW block (markers + auto-comment + inner content).
_RENDERED_BLOCK = (
    f"{TRW_AUTO_COMMENT}\n"
    f"{TRW_MARKER_START}\n"
    "\n## TRW Behavioral Protocol\n\nCall `trw_session_start()` first.\n\n"
    f"{TRW_MARKER_END}\n"
)


def _clobbered_pointer(target: str = "AGENTS.md") -> str:
    """A pointer file with a stale appended TRW block (old clobber behaviour)."""
    return f"@{target}\n\n{_RENDERED_BLOCK}"


# ---------------------------------------------------------------------------
# FR03 / NFR04 — classifier
# ---------------------------------------------------------------------------


class TestClassifyInstructionFile:
    @pytest.mark.parametrize(
        ("name", "text", "expected", "targets"),
        [
            ("pointer_only", "@AGENTS.md\n", InstructionFileClass.POINTER, ("AGENTS.md",)),
            (
                "pointer_with_heading_blanks",
                "# Project\n\n@AGENTS.md\n\n",
                InstructionFileClass.POINTER,
                ("AGENTS.md",),
            ),
            (
                "pointer_multi",
                "@AGENTS.md\n@docs/EXTRA.md\n",
                InstructionFileClass.POINTER,
                ("AGENTS.md", "docs/EXTRA.md"),
            ),
            (
                "content_directive_plus_prose",
                "@AGENTS.md\n\nReal prose describing the repo.\n",
                InstructionFileClass.CONTENT,
                (),
            ),
            ("content_inline_mention", "See @AGENTS.md inline in this sentence.\n", InstructionFileClass.CONTENT, ()),
            ("empty_whitespace", "\n\n   \n", InstructionFileClass.EMPTY, ()),
            ("headings_only", "# Title\n## Section\n", InstructionFileClass.EMPTY, ()),
            ("clobbered_pointer", _clobbered_pointer(), InstructionFileClass.POINTER, ("AGENTS.md",)),
            (
                "marker_region_ignored_regardless_of_content",
                _RENDERED_BLOCK.replace(
                    "## TRW Behavioral Protocol\n\nCall `trw_session_start()` first.\n\n", "@.trw/INSTRUCTIONS.md\n"
                ),
                InstructionFileClass.EMPTY,
                (),
            ),
            # P2-2: a line that OPENS but does not CLOSE an HTML comment is not a
            # full-line comment, so it counts as substantive content (=> CONTENT).
            ("partial_html_comment", "<!-- opens here\n@AGENTS.md\n", InstructionFileClass.CONTENT, ()),
        ],
    )
    def test_table(
        self,
        tmp_path: Path,
        name: str,
        text: str,
        expected: InstructionFileClass,
        targets: tuple[str, ...],
    ) -> None:
        p = tmp_path / f"{name}.md"
        p.write_text(text, encoding="utf-8")
        result = classify_instruction_file(p)
        assert result.kind is expected, f"{name}: {result.kind}"
        assert result.import_targets == targets

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert classify_instruction_file(tmp_path / "nope.md").kind is InstructionFileClass.EMPTY

    def test_classify_is_pure_no_write(self, tmp_path: Path) -> None:
        """NFR04: classification reads only, never writes; deterministic."""
        p = tmp_path / "c.md"
        p.write_text("@AGENTS.md\n", encoding="utf-8")
        before = p.read_text(encoding="utf-8")
        a = classify_instruction_file(p)
        b = classify_instruction_file(p)
        assert p.read_text(encoding="utf-8") == before  # no write
        assert a == b  # deterministic


# ---------------------------------------------------------------------------
# FR04 / FR05 — carrier-mode decision (PRD-QUAL-143-FR01: pure two-mode gate)
# ---------------------------------------------------------------------------


class TestResolveCarrierMode:
    @pytest.mark.parametrize(
        ("kind", "expected"),
        [
            (InstructionFileClass.POINTER, CarrierMode.POINTER_SKIP),
            (InstructionFileClass.CONTENT, CarrierMode.INLINE),
            (InstructionFileClass.EMPTY, CarrierMode.INLINE),
        ],
    )
    def test_decision(self, kind: InstructionFileClass, expected: CarrierMode) -> None:
        assert resolve_carrier_mode(InstructionFileClassification(kind)) is expected


# ---------------------------------------------------------------------------
# FR04 — pointer-skip guard in both appenders
# ---------------------------------------------------------------------------


class TestPointerSkipGuard:
    def test_merge_trw_section_skips_clean_pointer(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text("@AGENTS.md\n", encoding="utf-8")
        merge_trw_section(p, _RENDERED_BLOCK, max_lines=500)
        assert p.read_text(encoding="utf-8") == "@AGENTS.md\n"  # byte-identical, no append

    def test_merge_trw_section_heals_clobbered_pointer(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text(_clobbered_pointer(), encoding="utf-8")
        merge_trw_section(p, _RENDERED_BLOCK, max_lines=500)
        # The stale block must NOT be replaced (re-clobber); it is stripped.
        out = p.read_text(encoding="utf-8")
        assert out == "@AGENTS.md\n"
        assert TRW_MARKER_START not in out

    def test_bootstrap_appender_skips_pointer(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._template_claude_md import _update_claude_md_trw_section

        p = tmp_path / "CLAUDE.md"
        p.write_text("@AGENTS.md\n", encoding="utf-8")
        result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}
        _update_claude_md_trw_section(p, result)
        assert p.read_text(encoding="utf-8") == "@AGENTS.md\n"
        assert str(p) in result["preserved"]
        assert not result["errors"]

    def test_guard_returns_none_for_content(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text("# Real content here\n\nProse.\n", encoding="utf-8")
        assert pointer_skip_guard(p) is None

    def test_guard_returns_classification_for_pointer(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text("@AGENTS.md\n", encoding="utf-8")
        cl = pointer_skip_guard(p)
        assert cl is not None and cl.kind is InstructionFileClass.POINTER


# ---------------------------------------------------------------------------
# FR06 — heal previously-clobbered pointers
# ---------------------------------------------------------------------------


class TestHealPointer:
    def test_heals_clobbered_pointer(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text(_clobbered_pointer(), encoding="utf-8")
        assert heal_pointer(p) is True
        assert p.read_text(encoding="utf-8") == "@AGENTS.md\n"

    def test_heal_is_idempotent(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text(_clobbered_pointer(), encoding="utf-8")
        heal_pointer(p)
        first = p.read_text(encoding="utf-8")
        assert heal_pointer(p) is False  # nothing left to strip
        assert p.read_text(encoding="utf-8") == first  # byte-identical

    def test_heal_noop_on_clean_pointer(self, tmp_path: Path) -> None:
        p = tmp_path / "CLAUDE.md"
        p.write_text("@AGENTS.md\n", encoding="utf-8")
        assert heal_pointer(p) is False


# ---------------------------------------------------------------------------
# ``apply_carrier`` — the single shared entry point for both modes
# ---------------------------------------------------------------------------


class TestApplyCarrier:
    def test_pointer_target_is_healed_and_skipped(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text(_clobbered_pointer(), encoding="utf-8")
        outcome = apply_carrier(target, _RENDERED_BLOCK, 500)
        assert outcome.mode is CarrierMode.POINTER_SKIP
        assert outcome.healed is True
        assert outcome.pointer_targets == ("AGENTS.md",)
        assert target.read_text(encoding="utf-8") == "@AGENTS.md\n"

    def test_content_target_is_inlined(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Content\n", encoding="utf-8")
        outcome = apply_carrier(target, _RENDERED_BLOCK, 500)
        assert outcome.mode is CarrierMode.INLINE
        out = target.read_text(encoding="utf-8")
        assert TRW_MARKER_START in out
        assert "TRW Behavioral Protocol" in out

    def test_dry_run_writes_nothing_for_pointer(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text(_clobbered_pointer(), encoding="utf-8")
        before = target.read_text(encoding="utf-8")
        outcome = apply_carrier(target, _RENDERED_BLOCK, 500, dry_run=True)
        assert outcome.mode is CarrierMode.POINTER_SKIP
        assert outcome.healed is False
        assert target.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# FR05 / FR07 / FR08 — dispatcher integration
# ---------------------------------------------------------------------------


@contextmanager
def _sync_env(trw_dir: Path, project_root: Path) -> Iterator[None]:
    """Patch the path resolvers + analytics so a sync runs hermetically."""
    with (
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project_root),
        patch("trw_mcp.state.analytics.update_analytics_sync"),
    ):
        yield


def _run_claude_sync(config: TRWConfig, trw_dir: Path, project_root: Path) -> dict[str, object]:
    from trw_mcp.state.claude_md import execute_claude_md_sync
    from trw_mcp.state.persistence import FileStateReader

    llm = MagicMock()
    llm.available = False
    with _sync_env(trw_dir, project_root):
        return dict(
            execute_claude_md_sync(
                scope="root",
                target_dir=None,
                config=config,
                reader=FileStateReader(),
                llm=llm,
                client="claude-code",
            )
        )


class TestDispatcherIntegration:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True)
        return trw_dir, tmp_path

    def test_content_claude_md_is_inlined(self, tmp_path: Path) -> None:
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("# Project\n\nHuman docs.\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir))
        result = _run_claude_sync(cfg, trw_dir, root)

        assert result["carrier_mode"] == "inline"
        claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
        assert TRW_MARKER_START in claude
        assert "# Project" in claude  # user content preserved
        assert not (root / ".trw" / "INSTRUCTIONS.md").exists()

    def test_pointer_skip_reported(self, tmp_path: Path) -> None:
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir))
        result = _run_claude_sync(cfg, trw_dir, root)

        assert result["carrier_mode"] == "pointer_skip"  # FR07
        skips = result["pointer_skips"]
        assert isinstance(skips, list) and skips and skips[0]["import_targets"] == ["AGENTS.md"]
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"  # un-clobbered

    def test_second_sync_is_cache_hit_and_byte_identical(self, tmp_path: Path) -> None:
        """FR08: a no-op re-sync is a cache hit, byte-identical, and still reports carrier_mode (P1-1/P1-3)."""
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("# Project\n\nHuman docs.\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir))
        _run_claude_sync(cfg, trw_dir, root)
        claude_1 = (root / "CLAUDE.md").read_text(encoding="utf-8")

        result2 = _run_claude_sync(cfg, trw_dir, root)
        assert result2["status"] == "unchanged"
        assert result2["carrier_mode"] == "inline"  # FR07 reported on cache hit (P1-1)
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == claude_1

    def test_pointer_skip_reported_on_cache_hit(self, tmp_path: Path) -> None:
        """FR07 P1-1: a pointer is reported as pointer_skip even on the cache-hit path."""
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir))
        _run_claude_sync(cfg, trw_dir, root)
        result2 = _run_claude_sync(cfg, trw_dir, root)
        assert result2["status"] == "unchanged"
        assert result2["carrier_mode"] == "pointer_skip"
        assert result2["pointer_skips"][0]["import_targets"] == ["AGENTS.md"]


# ---------------------------------------------------------------------------
# FR07 — doctor surface
# ---------------------------------------------------------------------------


class TestDoctorPointerReport:
    def test_doctor_reports_pointer_as_unclobbered(self, tmp_path: Path) -> None:
        from trw_mcp.server._subcommands_doctor import _check_instruction_gate
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        (tmp_path / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        # AGENTS.md carries the real block with the deliver gate so the check PASSes.
        (tmp_path / "AGENTS.md").write_text(
            f"# Agents\n\n{TRW_MARKER_START}\n{DELIVER_GATE_PHRASE} — build check required.\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )
        result = _check_instruction_gate(tmp_path, TRWConfig(trw_dir=str(tmp_path / ".trw")))
        assert result.status == "PASS"
        assert "un-clobbered" in result.message
        assert "CLAUDE.md" in result.message and "AGENTS.md" in result.message

    def test_doctor_on_clobbered_pointer_not_failed(self, tmp_path: Path) -> None:
        """P2-5: a CLAUDE.md still carrying a stale block classifies as POINTER (block stripped first), not a gate FAIL."""
        from trw_mcp.server._subcommands_doctor import _check_instruction_gate
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        (tmp_path / "CLAUDE.md").write_text(_clobbered_pointer(), encoding="utf-8")
        (tmp_path / "AGENTS.md").write_text(
            f"# Agents\n\n{TRW_MARKER_START}\n{DELIVER_GATE_PHRASE} — build check required.\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )
        result = _check_instruction_gate(tmp_path, TRWConfig(trw_dir=str(tmp_path / ".trw")))
        assert result.status != "FAIL"
        assert "un-clobbered" in result.message
