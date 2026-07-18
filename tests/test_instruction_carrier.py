"""PRD-CORE-203: instruction-file carrier (pointer detection, externalization, healing).

Covers FR01-FR08 + NFR01/NFR02/NFR04. Unit tests target the pure carrier
functions; a small set of integration tests drive ``execute_claude_md_sync`` to
prove the dispatcher wiring (FR05/FR07/FR08/NFR01) end-to-end.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state.claude_md._instruction_carrier import (
    AT_IMPORT_PREFIX,
    CarrierMode,
    InstructionFileClass,
    InstructionFileClassification,
    apply_carrier,
    classify_instruction_file,
    externalize_block,
    heal_pointer,
    is_path_within,
    pointer_skip_guard,
    render_import_region,
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
    return f"{AT_IMPORT_PREFIX}{target}\n\n{_RENDERED_BLOCK}"


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
                "externalized_region",
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
# FR01 — per-profile import capability
# ---------------------------------------------------------------------------


class TestProfileImportSyntax:
    def test_claude_code_is_at_path(self) -> None:
        assert resolve_client_profile("claude-code").instruction_import_syntax == "at_path"

    @pytest.mark.parametrize("client", ["opencode", "codex", "copilot", "antigravity-cli", "cursor-ide", "cursor-cli"])
    def test_import_incapable_profiles_are_none(self, client: str) -> None:
        assert resolve_client_profile(client).instruction_import_syntax == "none"


# ---------------------------------------------------------------------------
# FR02 — config knobs
# ---------------------------------------------------------------------------


class TestExternalizeConfigKnobs:
    def test_defaults(self, tmp_path: Path) -> None:
        c = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        assert c.instruction_externalize == "auto"
        assert c.instruction_external_filename == ".trw/INSTRUCTIONS.md"

    def test_constructor_override(self, tmp_path: Path) -> None:
        c = TRWConfig(trw_dir=str(tmp_path / ".trw"), instruction_externalize="off")
        assert c.instruction_externalize == "off"

    def test_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_INSTRUCTION_EXTERNALIZE", "on")
        monkeypatch.setenv("TRW_INSTRUCTION_EXTERNAL_FILENAME", ".trw/custom.md")
        c = TRWConfig(trw_dir=str(tmp_path / ".trw"))
        assert c.instruction_externalize == "on"
        assert c.instruction_external_filename == ".trw/custom.md"


# ---------------------------------------------------------------------------
# FR04 / FR05 — carrier-mode decision
# ---------------------------------------------------------------------------


class TestResolveCarrierMode:
    @pytest.mark.parametrize(
        ("kind", "import_syntax", "externalize", "scope", "expected"),
        [
            (InstructionFileClass.POINTER, "at_path", "auto", "root", CarrierMode.POINTER_SKIP),
            (InstructionFileClass.POINTER, "none", "off", "root", CarrierMode.POINTER_SKIP),  # pointer always wins
            (InstructionFileClass.CONTENT, "at_path", "auto", "root", CarrierMode.IMPORT),
            (InstructionFileClass.CONTENT, "at_path", "on", "root", CarrierMode.IMPORT),
            (InstructionFileClass.CONTENT, "at_path", "off", "root", CarrierMode.INLINE),
            (InstructionFileClass.CONTENT, "none", "auto", "root", CarrierMode.INLINE),
            (
                InstructionFileClass.CONTENT,
                "none",
                "on",
                "root",
                CarrierMode.INLINE,
            ),  # NFR01: import-incapable never externalizes
            (InstructionFileClass.CONTENT, "at_path", "auto", "sub", CarrierMode.INLINE),
            (InstructionFileClass.EMPTY, "at_path", "auto", "root", CarrierMode.IMPORT),
        ],
    )
    def test_decision(
        self,
        kind: InstructionFileClass,
        import_syntax: str,
        externalize: str,
        scope: str,
        expected: CarrierMode,
    ) -> None:
        c = InstructionFileClassification(kind)
        assert resolve_carrier_mode(c, import_syntax=import_syntax, externalize=externalize, scope=scope) is expected


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
# FR05 — externalization
# ---------------------------------------------------------------------------


class TestExternalizeBlock:
    def test_sidecar_written_and_import_region_placed(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text("# My Project\n\nHuman content.\n", encoding="utf-8")
        sidecar = tmp_path / ".trw" / "INSTRUCTIONS.md"
        externalize_block(
            target,
            rendered_block=_RENDERED_BLOCK,
            sidecar_path=sidecar,
            sidecar_relpath=".trw/INSTRUCTIONS.md",
            max_lines=500,
        )
        assert sidecar.exists()
        assert "TRW Behavioral Protocol" in sidecar.read_text(encoding="utf-8")
        out = target.read_text(encoding="utf-8")
        assert "# My Project" in out  # user content preserved
        assert "@.trw/INSTRUCTIONS.md" in out
        assert "TRW Behavioral Protocol" not in out  # block is NOT inline

    def test_migrates_prior_inline_block(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text(f"# Top\n\n{_RENDERED_BLOCK}", encoding="utf-8")
        sidecar = tmp_path / ".trw" / "INSTRUCTIONS.md"
        externalize_block(
            target,
            rendered_block=_RENDERED_BLOCK,
            sidecar_path=sidecar,
            sidecar_relpath=".trw/INSTRUCTIONS.md",
            max_lines=500,
        )
        out = target.read_text(encoding="utf-8")
        # The prior inline block is replaced by exactly one import directive.
        assert out.count("@.trw/INSTRUCTIONS.md") == 1
        assert "TRW Behavioral Protocol" not in out
        assert "# Top" in out

    def test_apply_carrier_import_mode(self, tmp_path: Path) -> None:
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Content\n", encoding="utf-8")
        outcome = apply_carrier(
            target,
            _RENDERED_BLOCK,
            500,
            import_syntax="at_path",
            externalize="auto",
            scope="root",
            external_filename=".trw/INSTRUCTIONS.md",
            project_root=tmp_path,
        )
        assert outcome.mode is CarrierMode.IMPORT
        assert outcome.external_path == ".trw/INSTRUCTIONS.md"
        assert (tmp_path / ".trw" / "INSTRUCTIONS.md").exists()

    def test_apply_carrier_falls_back_to_inline_on_failure(self, tmp_path: Path) -> None:
        """NFR02: an externalization failure degrades to inline, never a dangling import."""
        target = tmp_path / "CLAUDE.md"
        target.write_text("# Content\n", encoding="utf-8")
        with patch(
            "trw_mcp.state.claude_md._instruction_carrier.externalize_block",
            side_effect=OSError("disk full"),
        ):
            outcome = apply_carrier(
                target,
                _RENDERED_BLOCK,
                500,
                import_syntax="at_path",
                externalize="auto",
                scope="root",
                external_filename=".trw/INSTRUCTIONS.md",
                project_root=tmp_path,
            )
        out = target.read_text(encoding="utf-8")
        assert outcome.mode is CarrierMode.INLINE
        assert "TRW Behavioral Protocol" in out  # inlined
        assert "@.trw/INSTRUCTIONS.md" not in out  # no dangling import

    def test_render_import_region_shape(self) -> None:
        region = render_import_region(".trw/INSTRUCTIONS.md")
        assert TRW_MARKER_START in region
        assert TRW_MARKER_END in region
        assert "@.trw/INSTRUCTIONS.md" in region

    def test_path_traversal_filename_falls_back_inline(self, tmp_path: Path) -> None:
        """P0-1/NFR02: a sidecar filename escaping the root degrades to inline, writes nothing outside."""
        root = tmp_path / "proj"
        root.mkdir()
        target = root / "CLAUDE.md"
        target.write_text("# Content\n", encoding="utf-8")
        escape = tmp_path / "escape.md"
        outcome = apply_carrier(
            target,
            _RENDERED_BLOCK,
            500,
            import_syntax="at_path",
            externalize="auto",
            scope="root",
            external_filename="../escape.md",
            project_root=root,
        )
        assert outcome.mode is CarrierMode.INLINE
        assert not escape.exists()  # nothing written outside the project root
        assert "TRW Behavioral Protocol" in target.read_text(encoding="utf-8")  # inlined

    def test_is_path_within(self, tmp_path: Path) -> None:
        assert is_path_within(tmp_path, tmp_path / ".trw" / "INSTRUCTIONS.md")
        assert not is_path_within(tmp_path, tmp_path / ".." / "escape.md")


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
# FR08 — hash-cache carrier awareness
# ---------------------------------------------------------------------------


class TestSyncHashCarrier:
    def test_toggling_externalize_changes_hash(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._sync import _compute_sync_hash

        off = TRWConfig(trw_dir=str(tmp_path / ".trw"), instruction_externalize="off")
        on = TRWConfig(trw_dir=str(tmp_path / ".trw"), instruction_externalize="auto")
        assert _compute_sync_hash(off) != _compute_sync_hash(on)

    def test_no_config_is_legacy_version_only(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._sync import _compute_sync_hash

        # Legacy callers (no config) get a stable version-only digest; supplying
        # a config must differ (it folds in the externalize knob).
        legacy = _compute_sync_hash()
        with_cfg = _compute_sync_hash(TRWConfig(trw_dir=str(tmp_path / ".trw")))
        assert legacy != with_cfg
        assert _compute_sync_hash() == legacy  # deterministic


# ---------------------------------------------------------------------------
# FR05 / FR07 / FR08 / NFR01 — dispatcher integration
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

    def test_externalizes_content_claude_md(self, tmp_path: Path) -> None:
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("# Project\n\nHuman docs.\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir), instruction_externalize="auto")
        result = _run_claude_sync(cfg, trw_dir, root)

        assert result["carrier_mode"] == "import"  # FR07
        assert result["external_path"] == ".trw/INSTRUCTIONS.md"  # FR07
        assert (root / ".trw" / "INSTRUCTIONS.md").exists()  # FR05 sidecar
        claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@.trw/INSTRUCTIONS.md" in claude
        assert "# Project" in claude  # user content preserved

    def test_pointer_skip_reported(self, tmp_path: Path) -> None:
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir), instruction_externalize="auto")
        result = _run_claude_sync(cfg, trw_dir, root)

        assert result["carrier_mode"] == "pointer_skip"  # FR07
        skips = result["pointer_skips"]
        assert isinstance(skips, list) and skips and skips[0]["import_targets"] == ["AGENTS.md"]
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md\n"  # un-clobbered

    def test_off_inlines_block_nfr01(self, tmp_path: Path) -> None:
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir), instruction_externalize="off")
        result = _run_claude_sync(cfg, trw_dir, root)

        assert result["carrier_mode"] == "inline"
        claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
        assert TRW_MARKER_START in claude
        assert "@.trw/INSTRUCTIONS.md" not in claude
        assert not (root / ".trw" / "INSTRUCTIONS.md").exists()

    def test_second_sync_is_cache_hit_and_byte_identical(self, tmp_path: Path) -> None:
        """FR08: a no-op re-sync is a cache hit, byte-identical, and still reports carrier_mode (P1-1/P1-3)."""
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("# Project\n\nHuman docs.\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir), instruction_externalize="auto")
        _run_claude_sync(cfg, trw_dir, root)
        claude_1 = (root / "CLAUDE.md").read_text(encoding="utf-8")
        sidecar_1 = (root / ".trw" / "INSTRUCTIONS.md").read_text(encoding="utf-8")

        result2 = _run_claude_sync(cfg, trw_dir, root)
        assert result2["status"] == "unchanged"
        assert result2["carrier_mode"] == "import"  # FR07 reported on cache hit (P1-1)
        assert result2["external_path"] == ".trw/INSTRUCTIONS.md"
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == claude_1
        assert (root / ".trw" / "INSTRUCTIONS.md").read_text(encoding="utf-8") == sidecar_1

    def test_pointer_skip_reported_on_cache_hit(self, tmp_path: Path) -> None:
        """FR07 P1-1: a pointer is reported as pointer_skip even on the cache-hit path."""
        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        cfg = TRWConfig(trw_dir=str(trw_dir), instruction_externalize="auto")
        _run_claude_sync(cfg, trw_dir, root)
        result2 = _run_claude_sync(cfg, trw_dir, root)
        assert result2["status"] == "unchanged"
        assert result2["carrier_mode"] == "pointer_skip"
        assert result2["pointer_skips"][0]["import_targets"] == ["AGENTS.md"]

    def test_legacy_hash_causes_cache_miss_and_rerenders(self, tmp_path: Path) -> None:
        """FR08 P1-4: a pre-203 (no-config) stored hash forces a re-render + externalization on upgrade."""
        from trw_mcp.state.claude_md._sync import _compute_sync_hash, _write_stored_hash

        trw_dir, root = self._setup(tmp_path)
        (root / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
        # Simulate the hash an OLDER trw-mcp wrote (version only, no config folded in).
        _write_stored_hash(trw_dir, _compute_sync_hash())
        cfg = TRWConfig(trw_dir=str(trw_dir), instruction_externalize="auto")
        result = _run_claude_sync(cfg, trw_dir, root)
        assert result["status"] == "synced"  # cache MISS — legacy hash differs
        assert result["carrier_mode"] == "import"
        assert (root / ".trw" / "INSTRUCTIONS.md").exists()


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
