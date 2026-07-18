"""PRD-QUAL-104: instruction-surface hygiene — size/density gate, bundled
tool-lifecycle/memory-routing sync, light-client gate injection, and lint.

Test map (PRD §6):
- Precondition: size-gate logic extracted to _agents_md_size_gate.py sibling.
- FR01: brownfield truth table (a=block, b=warn, c=block) + explicit override.
- FR02: bundled-surface loader via importlib.resources + fail-open fallback.
- FR03: each of four light-client generators emit session-start + deliver-gate;
        config (deliver_gate_mode=advisory) does not suppress the gate text;
        render_minimal_protocol contains the deliver-gate string.
- FR04: lint reports oversized / missing_gate / stale_sync; --strict exits 1;
        inline-prose marker mention is NOT matched (whole-line anchoring).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DELIVER_GATE_PHRASE = "Do NOT call `trw_deliver` unless"
_SESSION_START_PHRASE = "trw_session_start"


# ---------------------------------------------------------------------------
# Precondition — module decomposition
# ---------------------------------------------------------------------------


def test_size_gate_extracted() -> None:
    """The brownfield resolver + block/abort logic live in the sibling module."""
    from trw_mcp.state.claude_md import _agents_md_size_gate as gate

    # The sibling owns the public size-gate helpers FR01 wires in.
    assert hasattr(gate, "resolve_instruction_size_gate_mode")
    assert hasattr(gate, "enforce_size_gate")


def test_agents_md_under_effective_loc_gate() -> None:
    """Both _agents_md.py and the new sibling stay under the 350 eLOC gate."""

    def effective_loc(path: Path) -> int:
        src = path.read_text(encoding="utf-8")
        in_doc = False
        quote = None
        n = 0
        for line in src.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if in_doc:
                if quote in s:
                    in_doc = False
                continue
            for q in ('"""', "'''"):
                if s.startswith(q):
                    rest = s[3:]
                    if q in rest:
                        break
                    in_doc = True
                    quote = q
                    break
            else:
                n += 1
        return n

    base = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "state" / "claude_md"
    assert effective_loc(base / "_agents_md.py") < 350
    assert effective_loc(base / "_agents_md_size_gate.py") < 350


# ---------------------------------------------------------------------------
# FR01 — brownfield truth table
# ---------------------------------------------------------------------------


class TestBrownfieldTruthTable:
    """resolve_instruction_size_gate_mode honors the binding truth table."""

    def test_case_a_no_config_resolves_block(self, tmp_path: Path) -> None:
        """(a) no .trw/config.yaml -> block."""
        from trw_mcp.state.claude_md._agents_md_size_gate import resolve_instruction_size_gate_mode

        mode = resolve_instruction_size_gate_mode(project_root=tmp_path, configured_mode=None)
        assert mode == "block"

    def test_case_b_explicit_max_auto_lines_resolves_warn(self, tmp_path: Path) -> None:
        """(b) config sets max_auto_lines explicitly -> warn (brownfield permissive)."""
        from trw_mcp.state.claude_md._agents_md_size_gate import resolve_instruction_size_gate_mode

        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("max_auto_lines: 300\n", encoding="utf-8")
        mode = resolve_instruction_size_gate_mode(project_root=tmp_path, configured_mode=None)
        assert mode == "warn"

    def test_case_c_config_without_max_auto_lines_resolves_block(self, tmp_path: Path) -> None:
        """(c) config present but no max_auto_lines -> block."""
        from trw_mcp.state.claude_md._agents_md_size_gate import resolve_instruction_size_gate_mode

        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("ceremony_mode: full\n", encoding="utf-8")
        mode = resolve_instruction_size_gate_mode(project_root=tmp_path, configured_mode=None)
        assert mode == "block"

    def test_explicit_override_wins_over_case_a(self, tmp_path: Path) -> None:
        """Explicit instruction_size_gate_mode=warn beats the resolved block (case a)."""
        from trw_mcp.state.claude_md._agents_md_size_gate import resolve_instruction_size_gate_mode

        mode = resolve_instruction_size_gate_mode(project_root=tmp_path, configured_mode="warn")
        assert mode == "warn"

    def test_explicit_override_wins_over_case_b(self, tmp_path: Path) -> None:
        """Explicit instruction_size_gate_mode=block beats the resolved warn (case b)."""
        from trw_mcp.state.claude_md._agents_md_size_gate import resolve_instruction_size_gate_mode

        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("max_auto_lines: 300\n", encoding="utf-8")
        mode = resolve_instruction_size_gate_mode(project_root=tmp_path, configured_mode="block")
        assert mode == "block"


class TestSizeGateEnforcement:
    """enforce_size_gate blocks or warns per resolved mode."""

    def test_block_oversized_returns_error_and_does_not_write(self) -> None:
        """block mode returns the structured error dict for an oversized section."""
        from trw_mcp.state.claude_md._agents_md_size_gate import enforce_size_gate

        err = enforce_size_gate(file_label="AGENTS.md", lines=350, limit=300, mode="block")
        assert err is not None
        assert err["error_code"] == "instruction_surface_oversized"
        assert err["file"] == "AGENTS.md"
        assert err["lines"] == 350
        assert err["limit"] == 300

    def test_block_within_limit_returns_none(self) -> None:
        """block mode allows a within-limit section (no error)."""
        from trw_mcp.state.claude_md._agents_md_size_gate import enforce_size_gate

        assert enforce_size_gate(file_label="AGENTS.md", lines=100, limit=300, mode="block") is None

    def test_warn_oversized_returns_none(self) -> None:
        """warn mode never returns an error even when oversized (brownfield)."""
        from trw_mcp.state.claude_md._agents_md_size_gate import enforce_size_gate

        assert enforce_size_gate(file_label="AGENTS.md", lines=350, limit=300, mode="warn") is None


# ---------------------------------------------------------------------------
# FR02 — bundled-surface loader + fail-open fallback
# ---------------------------------------------------------------------------


class TestBundledSurfaceLoader:
    """importlib.resources loaders for tool-lifecycle / memory-routing."""

    def test_tool_lifecycle_loader_contains_deliver_gate(self) -> None:
        """load_tool_lifecycle() returns the bundled body with the gate phrase."""
        from trw_mcp.state.claude_md.sections._tool_lifecycle import load_tool_lifecycle

        body = load_tool_lifecycle()
        assert _DELIVER_GATE_PHRASE in body

    def test_render_closing_reminder_uses_bundled_source(self) -> None:
        """render_closing_reminder() surfaces the bundled deliver-gate phrase."""
        from trw_mcp.state.claude_md.sections._tool_lifecycle import render_closing_reminder

        assert _DELIVER_GATE_PHRASE in render_closing_reminder()

    def test_tool_lifecycle_fallback_on_read_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When the bundled resource cannot be read, the fallback string is used."""
        from trw_mcp.state.claude_md.sections import _tool_lifecycle as tl

        def _boom(*_args: object, **_kwargs: object) -> str:
            raise OSError("simulated packaging anomaly")

        monkeypatch.setattr(tl, "_read_bundled_surface", _boom)
        body = tl.load_tool_lifecycle()
        # Fallback constant MUST carry the deliver-gate phrase (FR02 NFR02).
        assert _DELIVER_GATE_PHRASE in body
        assert body == tl._FALLBACK_TOOL_LIFECYCLE

    def test_memory_routing_loader_contains_routing_text(self) -> None:
        """load_memory_routing() returns the bundled memory-routing body."""
        from trw_mcp.state.claude_md.sections._memory_routing import load_memory_routing

        assert "trw_learn()" in load_memory_routing()

    def test_memory_routing_fallback_on_read_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """memory-routing loader falls open to the in-module fallback constant."""
        from trw_mcp.state.claude_md.sections import _memory_routing as mr

        def _boom(*_args: object, **_kwargs: object) -> str:
            raise OSError("simulated packaging anomaly")

        monkeypatch.setattr(mr, "_read_bundled_surface", _boom)
        body = mr.load_memory_routing()
        assert body == mr._FALLBACK_MEMORY_ROUTING

    def test_bundled_files_present_in_package_data(self) -> None:
        """The bundled copies ship under trw_mcp/data/surfaces/."""
        base = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "surfaces"
        assert (base / "tool-lifecycle.md").is_file()
        assert (base / "memory-routing.md").is_file()


# ---------------------------------------------------------------------------
# FR03 — light-client gate injection
# ---------------------------------------------------------------------------


class TestLightClientGateInjection:
    """Every light-client instruction file carries the gate + session-start."""

    def test_opencode_instructions_gate_present(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._opencode import generate_opencode_instructions

        generate_opencode_instructions(tmp_path, "generic", force=True)
        body = (tmp_path / ".opencode" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        assert _DELIVER_GATE_PHRASE in body
        assert _SESSION_START_PHRASE in body

    def test_codex_instructions_gate_present(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._opencode import generate_codex_instructions

        generate_codex_instructions(tmp_path, force=True)
        body = (tmp_path / ".codex" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        assert _DELIVER_GATE_PHRASE in body
        assert _SESSION_START_PHRASE in body

    def test_copilot_instructions_gate_present(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._copilot import generate_copilot_instructions

        generate_copilot_instructions(tmp_path, force=True)
        body = (tmp_path / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
        assert _DELIVER_GATE_PHRASE in body
        assert _SESSION_START_PHRASE in body

    def test_gate_text_survives_advisory_deliver_gate_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """deliver_gate_mode=advisory does NOT suppress the gate text (binding)."""
        from trw_mcp.models.config import get_config, reload_config

        cfg = get_config().model_copy(update={"deliver_gate_mode": "advisory"})
        reload_config(cfg)
        try:
            from trw_mcp.bootstrap._copilot import generate_copilot_instructions
            from trw_mcp.bootstrap._opencode import (
                generate_codex_instructions,
                generate_opencode_instructions,
            )

            generate_opencode_instructions(tmp_path, "generic", force=True)
            generate_codex_instructions(tmp_path, force=True)
            generate_copilot_instructions(tmp_path, force=True)

            for rel in (
                ".opencode/INSTRUCTIONS.md",
                ".codex/INSTRUCTIONS.md",
                ".github/copilot-instructions.md",
            ):
                body = (tmp_path / rel).read_text(encoding="utf-8")
                assert _DELIVER_GATE_PHRASE in body, f"gate text suppressed in {rel}"
        finally:
            reload_config(None)

    def test_render_minimal_protocol_contains_gate(self) -> None:
        """The light-ceremony render path also carries the deliver-gate text."""
        from trw_mcp.state.claude_md._static_sections import render_minimal_protocol

        assert _DELIVER_GATE_PHRASE in render_minimal_protocol()


# ---------------------------------------------------------------------------
# FR03 — P1 audit fix: no render entry point may bypass the canonical gate
# ---------------------------------------------------------------------------

# The whole-line content-hash marker emitted ONLY by the single canonical
# ``render_deliver_gate_statement()`` (FR04). Asserting its presence proves a
# render path used the canonical statement and not a hand-copied inline string
# that could silently drift gate-less or stale.
_SYNC_MARKER_PREFIX = "<!-- trw:lifecycle-sync:sha256-"


def _all_light_render_entry_points() -> dict[str, object]:
    """Enumerate EVERY function that emits a light-client protocol carrier.

    P1 audit (2026-06-11, delivered != wired): a light/minimal render path
    (``ProtocolRenderer.render_minimal_protocol``) bypassed
    ``render_deliver_gate_statement()`` with a hardcoded inline gate copy. To
    guarantee no path can silently bypass again, this parametrization covers
    every render symbol that produces a protocol-carrier surface — the
    section-level facade, the class method directly, and the per-light-client
    instruction renderers. The bundled-source loader feeds them all.
    """
    from trw_mcp.models.config._client_profile import ClientProfile
    from trw_mcp.state.claude_md._renderer import ProtocolRenderer
    from trw_mcp.state.claude_md._static_sections import (
        render_agents_trw_section,
        render_codex_instructions,
        render_codex_trw_section,
        render_minimal_protocol,
        render_opencode_instructions,
    )
    from trw_mcp.state.claude_md.sections._tool_lifecycle import (
        render_deliver_gate_statement,
    )

    def _minimal_via_class() -> str:
        renderer = ProtocolRenderer(
            client_profile=ClientProfile(client_id="generic", display_name="generic"),
            ceremony_mode="MINIMAL",
        )
        return renderer.render_minimal_protocol()

    return {
        # The canonical statement itself — the single source of gate truth.
        "render_deliver_gate_statement": render_deliver_gate_statement,
        # Section-level facade used by AGENTS.md light-ceremony call sites
        # (_agents_md light path, _ide_targets, _init_project_ide).
        "render_minimal_protocol(section)": render_minimal_protocol,
        # The class method directly — the path the P1 audit flagged as bypassing.
        "ProtocolRenderer.render_minimal_protocol": _minimal_via_class,
        # Per-light-client instruction renderers (Codex / OpenCode carriers).
        "render_codex_instructions": render_codex_instructions,
        "render_opencode_instructions": lambda: render_opencode_instructions("generic"),
        # THIRD bypass instance (2026-06-11): the AGENTS.md ROOT full-ceremony
        # renderers hand-copied a divergent gate string ("for coding/rca/eval
        # tasks" interjection) that failed the exact-phrase lint -> missing_gate
        # on AGENTS.md. Both now route through render_deliver_gate_statement().
        "render_agents_trw_section": render_agents_trw_section,
        "render_codex_trw_section": render_codex_trw_section,
    }


_LIGHT_RENDER_IDS = list(_all_light_render_entry_points().keys())


class TestNoRenderEntryPointBypassesGate:
    """Every light-client render entry point routes through the canonical gate.

    Closes the P1 ``delivered != wired`` finding: the minimal/light render path
    must emit BOTH the non-negotiable deliver-gate phrase AND the FR04 sync
    marker that only ``render_deliver_gate_statement()`` produces — proving the
    output is the canonical bundled-derived statement, not a drift-prone inline
    hardcode.
    """

    @pytest.mark.parametrize("entry_id", _LIGHT_RENDER_IDS)
    def test_entry_point_emits_gate_phrase(self, entry_id: str) -> None:
        fn = _all_light_render_entry_points()[entry_id]
        out = fn()  # type: ignore[operator]
        assert _DELIVER_GATE_PHRASE in out, f"{entry_id} produced a gate-less surface"
        assert _SESSION_START_PHRASE in out, f"{entry_id} omitted session-start mandate"

    @pytest.mark.parametrize("entry_id", _LIGHT_RENDER_IDS)
    def test_entry_point_uses_canonical_sync_marker(self, entry_id: str) -> None:
        """The FR04 sync marker proves the canonical statement was used.

        A hand-copied inline gate string (the P1 defect) lacks this marker, so
        its presence on every entry point is what makes silent re-bypass
        impossible.
        """
        fn = _all_light_render_entry_points()[entry_id]
        out = fn()  # type: ignore[operator]
        assert _SYNC_MARKER_PREFIX in out, (
            f"{entry_id} lacks the FR04 lifecycle-sync marker — it is NOT routing "
            "through render_deliver_gate_statement() (inline-hardcode bypass)"
        )

    def test_minimal_protocol_gate_survives_advisory_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """deliver_gate_mode=advisory does NOT strip the gate from the light path.

        The class-method light render is the carrier the audit flagged; assert
        the gate text is non-negotiable regardless of configured deliver mode.
        """
        from trw_mcp.models.config import get_config, reload_config
        from trw_mcp.state.claude_md._static_sections import render_minimal_protocol

        cfg = get_config().model_copy(update={"deliver_gate_mode": "advisory"})
        reload_config(cfg)
        try:
            out = render_minimal_protocol()
            assert _DELIVER_GATE_PHRASE in out
            assert _SYNC_MARKER_PREFIX in out
        finally:
            reload_config(None)


# ---------------------------------------------------------------------------
# FR04 — lint script
# ---------------------------------------------------------------------------

_LINT_SCRIPT = _REPO_ROOT / "scripts" / "lint-instruction-surfaces.py"


def _run_lint(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_LINT_SCRIPT), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _bundled_lifecycle_hash_prefix() -> str:
    from trw_mcp.state.claude_md.sections._tool_lifecycle import bundled_lifecycle_hash_prefix

    return bundled_lifecycle_hash_prefix()


class TestLintInstructionSurfaces:
    """scripts/lint-instruction-surfaces.py size / gate / stale-sync checks."""

    def test_oversized_strict_exits_1(self, tmp_path: Path) -> None:
        """An oversized TRW block triggers an oversized finding + exit 1."""
        block = "\n".join(["<!-- trw:start -->", *[f"line {i}" for i in range(400)], "<!-- trw:end -->"])
        (tmp_path / "CLAUDE.md").write_text(block + "\n", encoding="utf-8")
        proc = _run_lint("--strict", "--max-lines", "300", cwd=tmp_path)
        assert proc.returncode == 1
        assert "oversized" in proc.stdout
        assert "CLAUDE.md" in proc.stdout

    def test_missing_gate_reported(self, tmp_path: Path) -> None:
        """A TRW block missing the deliver-gate phrase is reported missing_gate."""
        block = "<!-- trw:start -->\nsome protocol text without the gate\n<!-- trw:end -->\n"
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "INSTRUCTIONS.md").write_text(block, encoding="utf-8")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1
        assert "missing_gate" in proc.stdout

    def test_stale_sync_reported(self, tmp_path: Path) -> None:
        """A lifecycle-sync marker whose prefix differs from canonical is stale."""
        stale_marker = "<!-- trw:lifecycle-sync:sha256-000000000000 -->"
        block = f"<!-- trw:start -->\n{stale_marker}\n{_DELIVER_GATE_PHRASE} ...\n<!-- trw:end -->\n"
        (tmp_path / "CLAUDE.md").write_text(block, encoding="utf-8")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1
        assert "stale_sync" in proc.stdout

    def test_inline_marker_in_prose_not_matched(self, tmp_path: Path) -> None:
        """A marker mentioned only inline in prose is NOT treated as a real marker.

        Incident-driven (2026-06-11 ROADMAP 705-line truncation): whole-line
        anchoring only. Inline mention -> no stale_sync false positive.
        """
        good_prefix = _bundled_lifecycle_hash_prefix()
        # The marker appears ONLY inline inside backticks within a sentence, never
        # on its own line. Lint must ignore it -> no stale_sync. A real, correct
        # whole-line marker is present so the surface is otherwise clean.
        block = (
            "<!-- trw:start -->\n"
            f"See the marker `<!-- trw:lifecycle-sync:sha256-deadbeefcafe -->` mentioned here inline.\n"
            f"<!-- trw:lifecycle-sync:sha256-{good_prefix} -->\n"
            f"{_DELIVER_GATE_PHRASE} ...\n"
            "<!-- trw:end -->\n"
        )
        (tmp_path / "CLAUDE.md").write_text(block, encoding="utf-8")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "stale_sync" not in proc.stdout, proc.stdout
        assert proc.returncode == 0, proc.stdout

    def test_prose_boundary_marker_before_real_block_extracts_real_block(self, tmp_path: Path) -> None:
        """An inline-prose mention of the START marker before the real block does

        not mis-anchor extraction. The real block (line-anchored boundaries) is
        the one whose content is linted. F1 (instruction-surfaces review): raw
        ``content.find()`` would latch onto the prose ``<!-- trw:start -->`` and
        slice an empty/garbage region; whole-line anchoring (mirroring
        ``index_sync._find_marker_line``) skips it and lints the real block, whose
        missing deliver-gate phrase is correctly reported.
        """
        good_prefix = _bundled_lifecycle_hash_prefix()
        content = (
            "# Header\n"
            "This file documents the `<!-- trw:start -->` and `<!-- trw:end -->` markers "
            "inline so readers know the convention.\n"
            "\n"
            "<!-- trw:start -->\n"
            f"<!-- trw:lifecycle-sync:sha256-{good_prefix} -->\n"
            "protocol text WITHOUT the gate phrase\n"
            "<!-- trw:end -->\n"
        )
        (tmp_path / "CLAUDE.md").write_text(content, encoding="utf-8")
        proc = _run_lint("--strict", cwd=tmp_path)
        # The REAL block lacks the deliver-gate phrase -> missing_gate from the
        # real block (proves the real block, not the prose location, was extracted).
        assert "missing_gate" in proc.stdout, proc.stdout
        # And NOT stale_sync: the real block carries the fresh whole-line marker,
        # which is only reachable if extraction anchored on the real boundaries.
        assert "stale_sync" not in proc.stdout, proc.stdout
        assert proc.returncode == 1, proc.stdout

    def test_clean_corpus_exits_0(self, tmp_path: Path) -> None:
        """A compliant surface (size ok, gate present, marker fresh) exits 0."""
        good_prefix = _bundled_lifecycle_hash_prefix()
        block = (
            "<!-- trw:start -->\n"
            f"<!-- trw:lifecycle-sync:sha256-{good_prefix} -->\n"
            f"{_DELIVER_GATE_PHRASE} at least one of (a)/(b)/(c).\n"
            "<!-- trw:end -->\n"
        )
        (tmp_path / "CLAUDE.md").write_text(block, encoding="utf-8")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 0, proc.stdout

    def test_externalized_claude_carrier_lints_imported_gate(self, tmp_path: Path) -> None:
        """An @.trw carrier is compliant when its imported protocol has the gate."""
        good_prefix = _bundled_lifecycle_hash_prefix()
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "INSTRUCTIONS.md").write_text(
            f"<!-- trw:lifecycle-sync:sha256-{good_prefix} -->\n{_DELIVER_GATE_PHRASE} at least one of (a)/(b)/(c).\n",
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            "<!-- trw:start -->\n@.trw/INSTRUCTIONS.md\n<!-- trw:end -->\n",
            encoding="utf-8",
        )

        proc = _run_lint("--strict", cwd=tmp_path)

        assert proc.returncode == 0, proc.stdout

    def test_standard_externalized_carrier_lints_in_clean_clone(self, tmp_path: Path) -> None:
        """The canonical runtime sidecar may be absent before the first sync."""
        (tmp_path / "CLAUDE.md").write_text(
            "<!-- trw:start -->\n@.trw/INSTRUCTIONS.md\n<!-- trw:end -->\n",
            encoding="utf-8",
        )

        proc = _run_lint("--strict", cwd=tmp_path)

        assert proc.returncode == 0, proc.stdout

    def test_arbitrary_missing_import_still_fails_closed(self, tmp_path: Path) -> None:
        """Only the standard generated sidecar receives canonical fallback."""
        (tmp_path / "CLAUDE.md").write_text(
            "<!-- trw:start -->\n@missing-instructions.md\n<!-- trw:end -->\n",
            encoding="utf-8",
        )

        proc = _run_lint("--strict", cwd=tmp_path)

        assert proc.returncode == 1, proc.stdout
        assert "missing_gate" in proc.stdout

    def test_default_mode_exits_0_with_report(self, tmp_path: Path) -> None:
        """Default (non-strict) mode exits 0 even with findings, printing a report."""
        block = "<!-- trw:start -->\nno gate here\n<!-- trw:end -->\n"
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "INSTRUCTIONS.md").write_text(block, encoding="utf-8")
        proc = _run_lint(cwd=tmp_path)
        assert proc.returncode == 0
        assert "missing_gate" in proc.stdout


def test_marker_hash_helper_is_stable() -> None:
    """The marker hash helper matches a direct sha256 prefix of the bundled body."""
    from trw_mcp.state.claude_md.sections._tool_lifecycle import (
        bundled_lifecycle_hash_prefix,
        load_tool_lifecycle,
    )

    body = load_tool_lifecycle()
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    assert bundled_lifecycle_hash_prefix() == expected


# ---------------------------------------------------------------------------
# PRD-QUAL-112 FR02 + NFR02 — drift-detection checks (duplicate_block,
# glued_marker, version_drift, hardcoded_count, machine_path) + suppression.
#
# These exercise the lint as a library (import scan/drift helpers) AND end-to-end
# via the CLI subprocess so we prove the new checks are wired into the report and
# the strict exit path. Behavior-asserting: each check has detect-on-defect AND
# clean-on-good; line-anchored NFR02 regression; per-kind suppression.
# ---------------------------------------------------------------------------


def _write_framework_md(root: Path, version_line: str = "v26.1_TRW — MODEL-AGNOSTIC FRAMEWORK") -> None:
    """Write a fake canonical FRAMEWORK.md under <root>/.trw/frameworks/."""
    fw = root / ".trw" / "frameworks"
    fw.mkdir(parents=True, exist_ok=True)
    (fw / "FRAMEWORK.md").write_text(version_line + "\nVersion date: 2026-06-10\n", encoding="utf-8")


def _kinds_in(stdout: str) -> set[str]:
    """Parse the finding kinds present in the lint report stdout."""
    kinds: set[str] = set()
    for line in stdout.splitlines():
        if ":" in line and " — " in line:
            kinds.add(line.split(":", 1)[0].strip())
    return kinds


class TestDuplicateBlock:
    """duplicate_block: >=2 whole-line TRW start markers (any variant) in one file."""

    def test_legacy_uppercase_plus_lowercase_block_flagged(self, tmp_path: Path) -> None:
        """A dead legacy uppercase block + the live lowercase block -> duplicate_block."""
        content = (
            "<!-- TRW:BEGIN -->\n"
            "dead legacy block\n"
            "<!-- TRW:END -->\n"
            "\n"
            "<!-- trw:start -->\n"
            f"{_DELIVER_GATE_PHRASE} ...\n"
            "<!-- trw:end -->\n"
        )
        (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "duplicate_block" in proc.stdout, proc.stdout
        assert "AGENTS.md" in proc.stdout

    def test_single_block_clean(self, tmp_path: Path) -> None:
        """A file with exactly one TRW block is not flagged duplicate_block."""
        content = f"<!-- trw:start -->\n{_DELIVER_GATE_PHRASE} ...\n<!-- trw:end -->\n"
        (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "duplicate_block" not in proc.stdout, proc.stdout

    def test_autogen_comment_plus_start_not_duplicate(self, tmp_path: Path) -> None:
        """The NORMAL rendered pattern — the AUTO-GENERATED comment on its own line
        directly above <!-- trw:start --> — is ONE block, never duplicate_block.

        Regression: the AUTO-GENERATED comment is a sibling of the start marker, not a
        block delimiter; counting it as a start marker false-flagged every well-formed
        instruction file (PRD-QUAL-112)."""
        content = (
            "# Repo Guidelines\n"
            "Some prose.\n"
            "\n"
            "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
            "<!-- trw:start -->\n"
            f"{_DELIVER_GATE_PHRASE} ...\n"
            "<!-- trw:end -->\n"
        )
        (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "duplicate_block" not in proc.stdout, proc.stdout
        assert "glued_marker" not in proc.stdout, proc.stdout


class TestGluedMarker:
    """glued_marker: marker text present but NOT on its own line (glued to prose)."""

    def test_marker_glued_midline_flagged(self, tmp_path: Path) -> None:
        """A start marker glued onto the end of a prose line -> glued_marker."""
        content = (
            "# Notes\nApprove this before review.<!-- TRW AUTO-GENERATED — do not edit between markers -->\nmore text\n"
        )
        (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "glued_marker" in proc.stdout, proc.stdout

    def test_marker_on_own_line_clean(self, tmp_path: Path) -> None:
        """The same marker on its own line is a legitimate boundary, not glued."""
        content = "<!-- TRW AUTO-GENERATED — do not edit between markers -->\nbody\n<!-- /TRW AUTO-GENERATED -->\n"
        (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "glued_marker" not in proc.stdout, proc.stdout


class TestVersionDrift:
    """version_drift: a vN[.M]_TRW / TRW vN[.M] token disagreeing with canon."""

    def test_stale_version_flagged_with_correct_version(self, tmp_path: Path) -> None:
        """v25_TRW token while canonical is v26.1 -> version_drift."""
        (tmp_path / "AGENTS.md").write_text("This follows the v25_TRW protocol.\n", encoding="utf-8")
        _write_framework_md(tmp_path, "v26.1_TRW — MODEL-AGNOSTIC FRAMEWORK")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "version_drift" in proc.stdout, proc.stdout
        assert "v25_TRW" in proc.stdout
        assert "v26.1" in proc.stdout

    def test_matching_version_clean(self, tmp_path: Path) -> None:
        """A v26.1_TRW token matching canonical v26.1 is not flagged."""
        (tmp_path / "AGENTS.md").write_text("This follows the v26.1_TRW protocol.\n", encoding="utf-8")
        _write_framework_md(tmp_path, "v26.1_TRW — MODEL-AGNOSTIC FRAMEWORK")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "version_drift" not in proc.stdout, proc.stdout

    def test_missing_framework_md_skips_check_no_crash(self, tmp_path: Path) -> None:
        """No FRAMEWORK.md -> version_drift is SKIPPED (no crash, no finding)."""
        # A stale token is present but there is no canonical source to compare to.
        (tmp_path / "AGENTS.md").write_text("Mentions v25_TRW here.\n", encoding="utf-8")
        # Intentionally do NOT write FRAMEWORK.md.
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "version_drift" not in proc.stdout, proc.stdout
        # No crash: only the absence-of-other-findings clean exit is asserted.
        assert proc.returncode == 0, proc.stdout

    def test_trw_v_prose_form_flagged(self, tmp_path: Path) -> None:
        """The 'TRW v25' prose form is also detected (case-insensitive)."""
        (tmp_path / "CLAUDE.md").write_text("Built on TRW v25 conventions.\n", encoding="utf-8")
        _write_framework_md(tmp_path, "v26.1_TRW — MODEL-AGNOSTIC FRAMEWORK")
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "version_drift" in proc.stdout, proc.stdout


class TestHardcodedCount:
    """hardcoded_count: baked-in learning/session counts that drift on write."""

    def test_baked_learning_and_session_counts_flagged(self, tmp_path: Path) -> None:
        """'2407 learnings from 1578 prior sessions' -> hardcoded_count."""
        (tmp_path / "AGENTS.md").write_text("Start with 2407 learnings from 1578 prior sessions.\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "hardcoded_count" in proc.stdout, proc.stdout

    def test_suppression_marker_clears_hardcoded_count(self, tmp_path: Path) -> None:
        """A whole-line trw-lint-allow: hardcoded_count opts the file out."""
        (tmp_path / "AGENTS.md").write_text(
            '<!-- trw-lint-allow: hardcoded_count -->\nNARRATOR: "Across 249 sessions, 492 learnings compound."\n',
            encoding="utf-8",
        )
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "hardcoded_count" not in proc.stdout, proc.stdout

    def test_clean_file_no_counts(self, tmp_path: Path) -> None:
        """A file with no baked counts is not flagged."""
        (tmp_path / "AGENTS.md").write_text("No counts here, just prose.\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "hardcoded_count" not in proc.stdout, proc.stdout


class TestMachinePath:
    """machine_path: committed machine-specific absolute home paths."""

    def test_concrete_home_path_flagged(self, tmp_path: Path) -> None:
        """/home/wallter/projects/x -> machine_path."""
        (tmp_path / "CLAUDE.md").write_text("Run from /home/wallter/projects/x please.\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 1, proc.stdout
        assert "machine_path" in proc.stdout, proc.stdout

    def test_placeholder_path_clean(self, tmp_path: Path) -> None:
        """/home/<user>/x is a placeholder (contains '<') and is NOT flagged."""
        (tmp_path / "CLAUDE.md").write_text("Run from /home/<user>/x please.\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "machine_path" not in proc.stdout, proc.stdout


class TestNFR02LineAnchoring:
    """NFR02: a marker merely MENTIONED inline must not trigger block findings."""

    def test_inline_marker_mention_no_duplicate_or_glued(self, tmp_path: Path) -> None:
        """A start marker quoted in backticks within prose triggers nothing.

        Line-anchored discipline (2026-06-11 ROADMAP incident): an inline mention
        is neither a second block (duplicate_block) nor a glued marker, because
        the same marker also appears legitimately on its own line as a real block.
        """
        content = (
            "Docs mention the `<!-- trw:start -->` marker convention inline.\n"
            "\n"
            "<!-- trw:start -->\n"
            f"{_DELIVER_GATE_PHRASE} ...\n"
            "<!-- trw:end -->\n"
        )
        (tmp_path / "AGENTS.md").write_text(content, encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        kinds = _kinds_in(proc.stdout)
        assert "duplicate_block" not in kinds, proc.stdout
        assert "glued_marker" not in kinds, proc.stdout
        assert proc.returncode == 0, proc.stdout


class TestSuppressionGenerality:
    """NFR02: per-kind suppression suppresses only the named kind(s)."""

    def test_suppress_one_kind_leaves_others(self, tmp_path: Path) -> None:
        """trw-lint-allow: machine_path suppresses machine_path but NOT hardcoded_count."""
        (tmp_path / "CLAUDE.md").write_text(
            "<!-- trw-lint-allow: machine_path -->\nPath /home/wallter/x and 2407 learnings here.\n",
            encoding="utf-8",
        )
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        kinds = _kinds_in(proc.stdout)
        assert "machine_path" not in kinds, proc.stdout
        assert "hardcoded_count" in kinds, proc.stdout

    def test_suppress_all_clears_every_drift_kind(self, tmp_path: Path) -> None:
        """trw-lint-allow: all suppresses every drift kind for the file."""
        (tmp_path / "CLAUDE.md").write_text(
            "<!-- trw-lint-allow: all -->\nPath /home/wallter/x, 2407 learnings, v25_TRW.\n",
            encoding="utf-8",
        )
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        kinds = _kinds_in(proc.stdout)
        assert "machine_path" not in kinds, proc.stdout
        assert "hardcoded_count" not in kinds, proc.stdout
        assert "version_drift" not in kinds, proc.stdout


class TestDriftScanScope:
    """FR02 drift scan discovers nested files; size checks stay root-scoped."""

    def test_nested_claude_md_drift_discovered(self, tmp_path: Path) -> None:
        """A nested subdir/CLAUDE.md is reached by the broader drift scan."""
        nested = tmp_path / "pkg" / "sub"
        nested.mkdir(parents=True)
        (nested / "CLAUDE.md").write_text("Embeds /home/wallter/projects/x path.\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert "machine_path" in proc.stdout, proc.stdout
        assert "pkg/sub/CLAUDE.md" in proc.stdout, proc.stdout

    def test_excluded_dirs_not_scanned(self, tmp_path: Path) -> None:
        """Files under excluded dirs (node_modules, archive) are skipped."""
        for sub in ("node_modules", "docs/requirements-aare-f/archive", "trw-eval/results"):
            d = tmp_path / sub
            d.mkdir(parents=True)
            (d / "CLAUDE.md").write_text("Has /home/wallter/x in it.\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        assert proc.returncode == 0, proc.stdout
        assert "machine_path" not in proc.stdout, proc.stdout

    def test_nested_file_not_size_gated(self, tmp_path: Path) -> None:
        """A nested CLAUDE.md with an oversized TRW block is NOT oversized-flagged.

        Size/gate/stale checks remain scoped to the root surfaces in v1; only the
        drift checks run on nested files.
        """
        nested = tmp_path / "pkg"
        nested.mkdir()
        block = "\n".join(["<!-- trw:start -->", *[f"line {i}" for i in range(400)], "<!-- trw:end -->"])
        (nested / "CLAUDE.md").write_text(block + "\n", encoding="utf-8")
        _write_framework_md(tmp_path)
        proc = _run_lint("--strict", cwd=tmp_path)
        # The nested oversized block must NOT produce an oversized finding.
        for line in proc.stdout.splitlines():
            if line.startswith("oversized:"):
                assert "pkg/CLAUDE.md" not in line, proc.stdout
