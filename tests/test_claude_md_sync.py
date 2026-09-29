"""Tests for CLAUDE.md / AGENTS.md sync: FR13 instructions sync generalization.

Covers:
  - FR13: client parameter routes writes to CLAUDE.md and/or AGENTS.md
  - FR13: auto-detection via detect_ide() drives default behavior
  - FR13: backward compatibility — instructions sync still writes CLAUDE.md
  - FR13: AGENTS.md uses same markers and identical TRW section content
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._ide_detection_isolation import isolate_ide_detection
from tests._layout import requires_monorepo
from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_ide_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``detect_ide`` depend only on what each test seeds into ``tmp_path``.

    ``detect_ide`` mixes two signals: files under the project root, and
    machine-global ones (PRD-CORE-136-FR07). On any developer box with Cursor
    installed, that makes *every* ``tmp_path`` project detect as ``cursor-ide``
    even when the directory is empty, so this file's "nothing detected"
    assertions would be answering a question about the host, not about the
    code. Shared helper: ``tests/_ide_detection_isolation``.
    """
    isolate_ide_detection(monkeypatch)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_sync_args(tmp_path: Path) -> dict[str, object]:
    """Build minimal args for execute_claude_md_sync using tmp_path as root."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    (trw_dir / "patterns").mkdir(exist_ok=True)

    config = TRWConfig(trw_dir=str(trw_dir))
    reader = FileStateReader()
    llm = MagicMock()
    llm.available = False

    return {
        "scope": "root",
        "target_dir": None,
        "config": config,
        "reader": reader,
        "llm": llm,
    }


def _run_sync(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    """Run execute_claude_md_sync with mocked infrastructure."""
    from trw_mcp.state.claude_md._sync import execute_claude_md_sync

    args = _make_sync_args(tmp_path)
    args.update(kwargs)

    with (
        patch("trw_mcp.state.claude_md._sync.collect_promotable_learnings", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_patterns", return_value=[]),
        patch("trw_mcp.state.claude_md._sync.collect_context_data", return_value=({}, {})),
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path / ".trw"),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.state.analytics.update_analytics_sync"),
    ):
        return execute_claude_md_sync(**args)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TestInstructionsSync: FR13
# ---------------------------------------------------------------------------


class TestInstructionsSync:
    """FR13: instructions sync routes writes by ``write_targets``, not by detection alone.

    The shared AGENTS.md goes to the clients whose profile declares
    ``write_targets.agents_md`` — codex, copilot, antigravity-cli, cursor-cli and
    cursor-ide. opencode is not one of them any more (PRD-CORE-240-FR04); it owns
    ``.opencode/INSTRUCTIONS.md``.
    """

    def test_fr13_default_client_writes_agents_md_not_claude_md(self, tmp_path: Path) -> None:
        """Calling without client parameter writes the claude-code carrier: AGENTS.md, never CLAUDE.md."""
        result = _run_sync(tmp_path)

        assert result["status"] == "synced"
        assert result["agents_md_synced"] is True
        content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert TRW_MARKER_START in content
        assert TRW_MARKER_END in content
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_fr13_opencode_dir_detected_does_not_write_shared_agents_md(self, tmp_path: Path) -> None:
        """With .opencode/ auto-detected, the shared AGENTS.md is NOT written.

        PRD-CORE-240-FR04 withdrew opencode's claim on the shared surface
        (``writes_shared_agents_md=False``); it owns ``.opencode/INSTRUCTIONS.md``.
        Until 47aa22ae2b the auto path keyed on "some sync-capable client was
        detected" rather than on the profile flag, so it kept writing the surface
        the profile had withdrawn — and this test asserted that as correct.
        """
        (tmp_path / ".opencode").mkdir()

        result = _run_sync(tmp_path, client="auto")

        assert result["agents_md_synced"] is False
        assert not (tmp_path / "AGENTS.md").exists(), "opencode no longer claims the shared AGENTS.md"
        assert (tmp_path / ".opencode" / "INSTRUCTIONS.md").is_file(), "opencode still gets its own file"

    def test_fr13_opencode_json_detected_does_not_write_shared_agents_md(self, tmp_path: Path) -> None:
        """The opencode.json detection path reaches the same FR04 conclusion as .opencode/."""
        (tmp_path / "opencode.json").write_text('{"mcp": {}}', encoding="utf-8")

        result = _run_sync(tmp_path, client="auto")

        assert result["agents_md_synced"] is False
        assert not (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / ".opencode" / "INSTRUCTIONS.md").is_file()

    def test_fr13_codex_dir_detected_does_not_write_shared_agents_md(self, tmp_path: Path) -> None:
        """With .codex/ auto-detected, the shared AGENTS.md is NOT written.

        PRD-CORE-240-FR04 withdrew codex's claim, the same way it withdrew
        opencode's above. Codex owns `.codex/INSTRUCTIONS.md`, which
        `.codex/config.toml` points at via `model_instructions_file`. It was kept
        on the shared surface only because PRD-QUAL-113-FR03 capped its own file
        at 2,025 bytes — a token budget whose premise was "AGENTS.md owns generic
        workflow", i.e. the injection itself.
        """
        (tmp_path / ".codex").mkdir()

        result = _run_sync(tmp_path, client="auto")

        assert result["agents_md_synced"] is False
        assert not (tmp_path / "AGENTS.md").exists(), "codex no longer claims the shared AGENTS.md"
        codex_file = tmp_path / ".codex" / "INSTRUCTIONS.md"
        assert codex_file.is_file(), "codex still gets its own file"
        assert "OpenAI developer docs MCP server" in codex_file.read_text(encoding="utf-8")

    def test_fr13_claude_plus_codex_writes_agents_md_and_the_codex_file(self, tmp_path: Path) -> None:
        """Each client gets ITS surface: claude-code the shared AGENTS.md, codex its own file."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".codex").mkdir()

        result = _run_sync(tmp_path, client="auto")

        assert TRW_MARKER_START in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert (tmp_path / ".codex" / "INSTRUCTIONS.md").is_file()
        assert result["agents_md_synced"] is True
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_fr13_claude_plus_opencode_writes_agents_md_and_the_opencode_file(self, tmp_path: Path) -> None:
        """claude-code's claim writes AGENTS.md; opencode still gets only its own file."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".opencode").mkdir()

        result = _run_sync(tmp_path, client="auto")

        assert TRW_MARKER_START in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert (tmp_path / ".opencode" / "INSTRUCTIONS.md").is_file()
        assert result["agents_md_synced"] is True
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_fr13_client_override_opencode_only(self, tmp_path: Path) -> None:
        """client='opencode' writes its OWN instruction file, not CLAUDE.md or AGENTS.md.

        PRD-CORE-240-FR04: opencode no longer receives the shared AGENTS.md; it owns .opencode/INSTRUCTIONS.md, referenced from opencode.json.
        """
        (tmp_path / "CLAUDE.md").write_text("# Existing\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="opencode")

        assert result["agents_md_synced"] is False
        assert not (tmp_path / "AGENTS.md").exists()
        assert (tmp_path / ".opencode" / "INSTRUCTIONS.md").is_file()

        # CLAUDE.md should not have TRW markers injected
        claude_md = tmp_path / "CLAUDE.md"
        claude_content = claude_md.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in claude_content, "CLAUDE.md should NOT be modified when client='opencode'"

    def test_fr13_client_override_codex_only(self, tmp_path: Path) -> None:
        """client='codex' writes its OWN instruction file, not CLAUDE.md or AGENTS.md."""
        (tmp_path / "CLAUDE.md").write_text("# Existing\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="codex")

        assert result["agents_md_synced"] is False
        assert not (tmp_path / "AGENTS.md").exists()

        content = (tmp_path / ".codex" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        assert "OpenAI developer docs MCP server" in content
        assert ("Agent " + "Teams") not in content

        assert TRW_MARKER_START not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    def test_fr13_codex_profile_keeps_its_codex_specific_template(self, tmp_path: Path) -> None:
        """A real Codex profile should still render the Codex-specific AGENTS.md template."""
        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "reflections").mkdir(exist_ok=True)
        (trw_dir / "context").mkdir(exist_ok=True)
        (trw_dir / "patterns").mkdir(exist_ok=True)

        config = TRWConfig(trw_dir=str(trw_dir))
        object.__setattr__(config, "target_platforms", ["codex"])

        result = _run_sync(tmp_path, client="codex", config=config)

        # The Codex-specific template still renders — into codex's OWN file now.
        # This is the assertion that proves withdrawing AGENTS.md moved the
        # content rather than dropping it.
        content = (tmp_path / ".codex" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
        # PRD-CORE-301-FR02: codex framing around the shared block, not a codex-only workflow.
        assert content.startswith("# Codex TRW Instructions\n")
        assert "## Workflow" in content
        assert "OpenAI developer docs MCP server" in content
        assert result["agents_md_synced"] is False

    def test_no_sync_capable_client_claims_the_shared_agents_md(self) -> None:
        """The end state of PRD-CORE-240-FR04 — and a live dead-code warning.

        Three tests here used to exercise the sync path's AGENTS.md writer
        (markers, result path, user-content preservation) using whichever client
        still claimed the surface as the trigger: codex, then antigravity-cli.
        Both are now withdrawn, along with opencode and copilot, so NO
        sync-capable client claims it and that writer is unreachable from
        `client="auto"` or from any per-client override.

        cursor-cli still gets an AGENTS.md, but through the INSTALL path
        (`generate_cursor_cli_agents_md`), not this one — so those tests were
        asserting behavior no caller can reach, which is worse than no test.
        They are replaced by this invariant. If a client ever re-claims the
        shared surface, this fails and the writer's coverage must come back with
        it.
        """
        from trw_mcp.models.config._profiles import resolve_client_profile
        from trw_mcp.state.claude_md._instruction_clients import INSTRUCTION_SYNC_CLIENT_IDS

        claimers = [
            client for client in INSTRUCTION_SYNC_CLIENT_IDS if resolve_client_profile(client).write_targets.agents_md
        ]
        assert claimers == [], (
            f"{claimers} re-claimed the shared AGENTS.md; the sync-path writer is reachable again "
            "and needs the marker/result-path/preservation coverage restored"
        )

    def test_fr13_client_override_claude_code_only(self, tmp_path: Path) -> None:
        """client='claude-code' writes AGENTS.md and never touches a user's CLAUDE.md."""
        (tmp_path / "CLAUDE.md").write_text("# My Project\n", encoding="utf-8")

        result = _run_sync(tmp_path, client="claude-code")

        assert result["agents_md_synced"] is True
        assert TRW_MARKER_START in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == "# My Project\n"

    def test_fr13_client_all_writes_both(self, tmp_path: Path) -> None:
        """client='all' writes AGENTS.md regardless of detection, and no CLAUDE.md."""
        result = _run_sync(tmp_path, client="all")

        assert not (tmp_path / "CLAUDE.md").exists()
        assert (tmp_path / "AGENTS.md").exists()
        assert result["agents_md_synced"] is True

    def test_fr13_agents_md_has_platform_generic_content(self, tmp_path: Path) -> None:
        """AGENTS.md links to a platform-generic instructions file."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".opencode").mkdir()

        from trw_mcp.models.config import TRWConfig

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "reflections").mkdir(exist_ok=True)
        (trw_dir / "context").mkdir(exist_ok=True)
        (trw_dir / "patterns").mkdir(exist_ok=True)
        config = TRWConfig(trw_dir=str(trw_dir))

        _run_sync(tmp_path, client="all", config=config)

        assert TRW_MARKER_START in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        # PRD-CORE-341: AGENTS.md holds the link; the platform-generic block is the file it names.
        agents_content = (tmp_path / ".trw" / "INSTRUCTIONS.md").read_text(encoding="utf-8")

        # The instructions file should have platform-generic content (no Claude-specific terms)
        assert ("Agent " + "Teams") not in agents_content
        assert "subagents" not in agents_content
        assert "/trw-ceremony-guide" not in agents_content
        # PRD-CORE-301-FR13 dropped the MCP intro paragraph; the block points at the live surface.
        assert 'trw_status(detail="surface")' in agents_content

    def test_fr13_auto_no_ide_defaults_to_claude(self, tmp_path: Path) -> None:
        """With client='auto' and no IDE dirs, defaults to claude-code: AGENTS.md only."""
        result = _run_sync(tmp_path, client="auto")

        assert TRW_MARKER_START in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert not (tmp_path / "CLAUDE.md").exists()
        assert result["agents_md_synced"] is True

    def test_fr13_result_agents_md_path_none_when_not_written(self, tmp_path: Path) -> None:
        """Result has agents_md_path=None when AGENTS.md is not written."""
        result = _run_sync(tmp_path, client="opencode")

        assert result["agents_md_path"] is None

    def test_fr13_tool_accepts_client_parameter(self, tmp_path: Path) -> None:
        """``execute_claude_md_sync`` (PRD-CORE-300 S6b: ``trw-mcp instructions sync``) accepts a client parameter."""
        import inspect

        from trw_mcp.state.claude_md import execute_claude_md_sync

        sig = inspect.signature(execute_claude_md_sync)
        assert "client" in sig.parameters, "execute_claude_md_sync must accept a 'client' parameter"


# ---------------------------------------------------------------------------
# PRD-QUAL-075 FR11: marker preservation and sync target scope.
# ---------------------------------------------------------------------------


class TestMarkerPreservation:
    """FR11: trw:start / trw:end markers must survive a sync cycle."""

    def test_markers_preserved_after_sync(self, tmp_path: Path) -> None:
        """Running sync twice is idempotent — markers remain exactly once."""
        claude_md = tmp_path / "AGENTS.md"
        claude_md.write_text("# Project\n\nUser prose.\n", encoding="utf-8")

        _run_sync(tmp_path)
        first = claude_md.read_text(encoding="utf-8")
        assert first.count(TRW_MARKER_START) == 1
        assert first.count(TRW_MARKER_END) == 1

        _run_sync(tmp_path)
        second = claude_md.read_text(encoding="utf-8")
        assert second.count(TRW_MARKER_START) == 1, "sync duplicated the start marker"
        assert second.count(TRW_MARKER_END) == 1, "sync duplicated the end marker"
        # User content preserved.
        assert "User prose." in second

    def test_sync_does_not_recreate_markers_in_nested_agents_md(self, tmp_path: Path) -> None:
        """FR05/FR11: root sync operates on the project root AGENTS.md only — it
        must not rewrite a package-local ``trw-mcp/AGENTS.md``.
        """
        claude_md = tmp_path / "AGENTS.md"
        claude_md.write_text("# Project\n", encoding="utf-8")

        # Simulate the package-local file under a nested path — sync should
        # not touch it because its target is the root-level AGENTS.md.
        nested = tmp_path / "trw-mcp"
        nested.mkdir()
        nested_claude = nested / "AGENTS.md"
        nested_claude.write_text("# trw-mcp\n\nNo markers here.\n", encoding="utf-8")

        _run_sync(tmp_path)

        # Root got markers.
        assert TRW_MARKER_START in claude_md.read_text(encoding="utf-8")
        # Nested did not.
        nested_content = nested_claude.read_text(encoding="utf-8")
        assert TRW_MARKER_START not in nested_content
        assert TRW_MARKER_END not in nested_content


# ---------------------------------------------------------------------------
# PRD-QUAL-075 FR06: second-profile parity check (opencode).
# ---------------------------------------------------------------------------


_FIXTURE_DIR = Path(__file__).parent / "fixtures"
_OPENCODE_SHA_PATH = _FIXTURE_DIR / "opencode_agents_md_baseline.sha256"


class TestOpencodeParity:
    """FR06 acceptance: sanity-check a second profile's rendered artifact is stable."""

    def test_opencode_parity(self, tmp_path: Path) -> None:
        """Render AGENTS.md via opencode profile; assert SHA256 matches baseline.

        The baseline is a committed fixture: a missing one fails rather than being captured and skipped.
        """
        (tmp_path / ".opencode").mkdir()

        _run_sync(tmp_path, client="opencode")

        agents_md = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        assert agents_md.exists(), "opencode sync must produce its own instruction file"

        content = agents_md.read_bytes()
        actual_sha = hashlib.sha256(content).hexdigest()

        assert _OPENCODE_SHA_PATH.is_file(), (
            f"{_OPENCODE_SHA_PATH.name} is the committed baseline; regenerate it deliberately"
        )
        expected_sha = _OPENCODE_SHA_PATH.read_text(encoding="utf-8").strip()
        assert actual_sha == expected_sha, (
            f"opencode AGENTS.md SHA256 drifted: expected {expected_sha}, got {actual_sha}. "
            "If change is intentional, regenerate opencode_agents_md_baseline.sha256."
        )

    def test_opencode_agents_md_has_no_claude_code_literal(self, tmp_path: Path) -> None:
        """PRD-CORE-149 FR08 acceptance: the written AGENTS.md must not carry
        any literal 'Claude Code' string.

        This is the sync-pipeline end-to-end check the parity SHA alone cannot
        provide — a baseline captured with the literal present would freeze
        the bug into the fixture. Grepping the written file directly closes
        the profile-awareness regression surface at the actual sync output,
        not just the renderer.
        """
        (tmp_path / ".opencode").mkdir()
        _run_sync(tmp_path, client="opencode")

        # PRD-CORE-240-FR04: the leak assertion is unchanged in intent; what
        # changed is WHICH file opencode reads. It owns .opencode/INSTRUCTIONS.md
        # and no longer receives the shared AGENTS.md.
        agents_md = tmp_path / ".opencode" / "INSTRUCTIONS.md"
        assert agents_md.exists(), "opencode sync must produce its own instruction file"
        assert not (tmp_path / "AGENTS.md").exists(), "the shared file must be untouched"

        content = agents_md.read_text(encoding="utf-8")
        assert "Claude Code" not in content, (
            "opencode AGENTS.md contains a literal 'Claude Code' string — "
            "profile-awareness regression. Every nudge/protocol template "
            "must use {client_display_name} substitution (PRD-CORE-149 FR02)."
        )


# ---------------------------------------------------------------------------
# PRD-CORE-291-FR09: phantom ``build_check_result`` field claim corrected
# ---------------------------------------------------------------------------


@requires_monorepo
def test_deliver_gate_text_uses_real_fields() -> None:
    """The deliver-gate condition names real trw_build_check response fields.

    ``trw_build_check`` (trw-mcp/src/trw_mcp/tools/build/_registration.py)
    never returns a ``build_check_result`` field — that name is an internal
    ceremony-state attribute (``_ceremony_state_model.py``), not part of the
    tool's typed response. The deliver-gate prose (both the generator source
    and every file it regenerates) must instead cite ``tests_passed`` /
    ``static_checks_clean``, the fields the tool actually returns. A prior
    audit found the phantom field string still live in
    ``docs/documentation/tool-lifecycle.md``; this is the regression guard
    that keeps it from coming back.
    """
    repo_root = Path(__file__).resolve().parents[2]
    phantom = "build_check_result=pass"
    real_fields = ("tests_passed", "static_checks_clean")

    checked = [
        repo_root / "docs" / "documentation" / "tool-lifecycle.md",
        repo_root / "trw-mcp" / "src" / "trw_mcp" / "state" / "claude_md" / "sections" / "_tool_lifecycle.py",
        repo_root / "trw-mcp" / "src" / "trw_mcp" / "state" / "claude_md" / "renderers" / "_review_and_opencode.py",
        repo_root / "trw-mcp" / "src" / "trw_mcp" / "data" / "surfaces" / "tool-lifecycle.md",
    ]

    offenders_with_phantom: list[str] = []
    offenders_missing_real_fields: list[str] = []
    for path in checked:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if phantom in text:
            offenders_with_phantom.append(str(path))
        if "Do NOT call `trw_deliver` unless" in text and not all(field in text for field in real_fields):
            offenders_missing_real_fields.append(str(path))

    assert not offenders_with_phantom, f"Phantom field string {phantom!r} still present in: {offenders_with_phantom}"
    assert not offenders_missing_real_fields, (
        "Deliver-gate condition text does not cite the real trw_build_check "
        f"response fields {real_fields}: {offenders_missing_real_fields}"
    )


@requires_monorepo
def test_tool_lifecycle_doc_mirror_regenerated_matches_committed_copy() -> None:
    """Regenerate-and-diff: the docs/ mirror is byte-identical to a fresh render.

    ``docs/documentation/tool-lifecycle.md`` is a monorepo-only path — the
    public trw-mcp package does not ship ``docs/``.

    FR09's own pass_condition calls for "a regenerate-and-diff test", not a
    static grep of already-committed files — a grep cannot catch the doc
    mirror drifting from its canonical source if someone hand-edits
    ``docs/documentation/tool-lifecycle.md`` without regenerating it.
    ``docs/documentation/tool-lifecycle.md`` is a byte mirror of the bundled
    canonical ``trw-mcp/src/trw_mcp/data/surfaces/tool-lifecycle.md``
    (direction flipped under PRD-QUAL-104 FR02, see
    ``scripts/sync-instruction-surfaces.py``'s module docstring) -- this test
    regenerates the mirror the same way that script does (read the canonical
    bundled source through the production loader, not a second copy of the
    read logic) and diffs it against the tracked mirror.
    """
    from trw_mcp.state.claude_md.sections._tool_lifecycle import load_tool_lifecycle

    repo_root = Path(__file__).resolve().parents[2]
    mirror_path = repo_root / "docs" / "documentation" / "tool-lifecycle.md"

    regenerated = load_tool_lifecycle()
    committed = mirror_path.read_text(encoding="utf-8")

    assert regenerated == committed, (
        "docs/documentation/tool-lifecycle.md has drifted from the canonical bundled source "
        "(trw-mcp/src/trw_mcp/data/surfaces/tool-lifecycle.md) it should be a byte mirror of. "
        "Run scripts/sync-instruction-surfaces.py and commit both."
    )
    assert "build_check_result=pass" not in regenerated, (
        "The regenerated (not just the committed) tool-lifecycle body still carries the "
        "phantom build_check_result field."
    )
    assert "tests_passed" in regenerated and "static_checks_clean" in regenerated


def test_deliver_gate_statement_regenerated_from_canonical_source() -> None:
    """The two named generator functions, called fresh, never emit the phantom field.

    Calls the actual production renderers -- ``render_deliver_gate_statement``
    (``_tool_lifecycle.py``, sourced from the bundled canonical file at
    runtime) and ``render_antigravity_instructions`` (``_review_and_opencode.py``,
    which carries its own literal deliver-gate block) -- rather than grepping
    their source text, so a future edit that reintroduces the phantom field
    through either path is caught even if it does not touch the string this
    test's grep-based sibling checks for verbatim.
    """
    from trw_mcp.state.claude_md.renderers._review_and_opencode import render_antigravity_instructions
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

    for rendered in (render_deliver_gate_statement(), render_antigravity_instructions()):
        assert "build_check_result=pass" not in rendered
        assert "tests_passed" in rendered
        assert "static_checks_clean" in rendered


def test_deliver_gate_phantom_field_grep_repo_wide() -> None:
    """A repo-wide grep for the literal phantom string returns zero matches.

    Acceptance criterion (PRD-CORE-291-FR09): "a grep for the literal string
    build_check_result=pass across docs/documentation and
    trw-mcp/src/trw_mcp/state/claude_md returns zero matches."

    ``docs/documentation`` is monorepo-only (the public package ships no
    ``docs/``); ``src/trw_mcp/state/claude_md`` ships in the package and is
    addressed from :data:`PACKAGE_ROOT` so this still runs in the public
    layout. A missing grep target previously made ``grep`` exit 2 (usage
    error, not "no matches") and the assertion misread that as a finding.
    """
    import subprocess

    from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

    targets = [PACKAGE_ROOT / "src" / "trw_mcp" / "state" / "claude_md"]
    if MONOREPO_ROOT is not None:
        targets.append(MONOREPO_ROOT / "docs" / "documentation")
    targets = [t for t in targets if t.is_dir()]
    assert targets, "no grep targets resolved (package layout changed?)"

    result = subprocess.run(
        ["grep", "-r", "-l", "build_check_result=pass", *(str(t) for t in targets)],
        capture_output=True,
        text=True,
        check=False,
    )
    # grep exit code 1 means "no matches" — that is the passing case.
    assert result.returncode == 1, f"Found phantom field references: {result.stdout}"
