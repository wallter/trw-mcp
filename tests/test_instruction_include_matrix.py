"""Per-client include matrix — PRD-CORE-240 FR04, FR05, FR06.

TRW writes into files it does not own. The goal of PRD-CORE-240 is that a client
instruction file *references* framework text instead of embedding it — but only
where the client can actually resolve a reference. Getting that wrong in either
direction is a defect:

- Embedding where an include works is the injection this PRD removes.
- Referencing where an include does NOT work ships an instruction file that
  exists, parses, reports success, and carries nothing — a P5 success-shaped
  failure, and strictly worse than the injection it replaced.

So the matrix itself is the contract, and it is asserted here rather than
described in prose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config._profiles import _PROFILES, resolve_client_profile
from trw_mcp.state.claude_md._instruction_carrier import INCLUDE_INCAPABLE_CLIENTS
from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

# T2: no in-file include syntax, but the client's own config names which files to
# load — so the TRW artifact is registered there and the shared AGENTS.md is left
# entirely alone.
_T2_CLIENTS = ("opencode", "codex")

# T1: resolves an in-file include eagerly. copilot was here until its capability
# was re-verified against the IDE docs — see
# TestCopilotCannotUseAnInclude.
_T1_CLIENTS = ("claude-code",)


class TestT2ClientsOwnTheirInstructionFile:
    """FR04 is DEFERRED — see test_t2_agents_md_removal_is_blocked_on_a_prd_conflict.

    What FR04 asked for (opencode and codex contribute zero TRW bytes to the
    shared AGENTS.md) is implementable, and was implemented, but it deletes a
    surface that PRD-CORE-215/218-FR06 requires. Rather than break the older
    implemented contract silently, the T2 half that has no conflict — routing
    each client's own config at its own file (FR05) — ships, and the AGENTS.md
    removal waits on an operator decision.
    """

    @pytest.mark.parametrize("client", _T2_CLIENTS)
    def test_t2_client_owns_a_dedicated_instruction_file(self, client: str) -> None:
        """Each T2 client has a TRW-authored file with no user content at risk."""
        profile = resolve_client_profile(client)

        assert profile.write_targets.instruction_path.endswith("INSTRUCTIONS.md")
        assert profile.write_targets.claude_md is False

    def test_opencode_no_longer_receives_the_shared_agents_md(self) -> None:
        """PRD-CORE-240-FR04, resolved by operator decision 2026-07-28: WITHDRAWN.

        opencode owns `.opencode/INSTRUCTIONS.md`, and since the FR05 fix that file
        is actually referenced from `opencode.json`'s `instructions` array. The
        shared AGENTS.md was injection into a user-owned file no client needed.
        PRD-CORE-074's mandate predates that fix and is amended accordingly.
        """
        assert resolve_client_profile("opencode").write_targets.agents_md is False

    def test_codex_no_longer_receives_the_shared_agents_md(self) -> None:
        """WITHDRAWN — and the blocker turned out to be TRW's own budget.

        This previously asserted the opposite, on the grounds that codex's
        capability appendix (PRD-CORE-218-FR06) had nowhere else to go:
        `model_instructions_file` is single-valued and points at
        `.codex/INSTRUCTIONS.md`, which PRD-QUAL-113-FR03 capped at 2,025 bytes
        against a 5,043-byte appendix. That cap was a token budget, not a vendor
        limit, and its stated premise was "AGENTS.md owns generic workflow" —
        i.e. it presupposed the very injection this PRD removes. Retiring the
        cap frees the appendix, and codex's own file carries everything.

        Still true and still the reason no include is used: neither the AGENTS.md
        spec nor Codex's config reference documents any import syntax, and
        `project_doc_fallback_filenames` is "additional filenames to try when
        AGENTS.md is missing" — not a third slot.
        """
        assert resolve_client_profile("codex").write_targets.agents_md is False

    def test_codex_is_not_repointed_away_from_its_own_file(self) -> None:
        """FR04 guard: codex's key is single-valued, so repointing destroys user content.

        ``model_instructions_file`` names exactly one path. Aiming it at a TRW
        artifact would silently drop whatever the user's AGENTS.md held —
        CONSTITUTION HB-2. Codex keeps its own generated file instead.
        """
        assert resolve_client_profile("codex").write_targets.instruction_path == ".codex/INSTRUCTIONS.md"


class TestOpencodeInstructionsArrayRegistration:
    """FR05: an existing opencode.json must gain the TRW entry, not be ignored."""

    @staticmethod
    def _entry() -> dict[str, object]:
        return {"type": "local", "command": ["trw-mcp"], "enabled": True}

    def test_opencode_merge_appends_without_dropping_user_entry(self) -> None:
        """The user's entry keeps its index; the TRW artifact is appended exactly once.

        Before this, the merge path preserved the user's ``instructions`` key by
        never touching it — and so never added the TRW file either. Any project
        that had an opencode.json before installing TRW got
        ``.opencode/INSTRUCTIONS.md`` written and referenced by nothing.
        """
        from trw_mcp.bootstrap._opencode import merge_opencode_json

        existing = {"instructions": ["docs/HOUSE-RULES.md"], "model": "user-model"}

        merged = merge_opencode_json(existing, self._entry())  # type: ignore[arg-type]

        instructions = merged["instructions"]
        assert instructions.index("docs/HOUSE-RULES.md") == 0, "user entry must keep its position"
        assert instructions.count(".opencode/INSTRUCTIONS.md") == 1
        assert merged["model"] == "user-model", "unrelated user keys must survive"

    def test_second_merge_appends_nothing(self) -> None:
        from trw_mcp.bootstrap._opencode import merge_opencode_json

        first = merge_opencode_json({"instructions": ["a.md"]}, self._entry())  # type: ignore[arg-type]
        second = merge_opencode_json(first, self._entry())

        assert first["instructions"] == second["instructions"]

    def test_config_without_an_instructions_key_gains_one(self) -> None:
        from trw_mcp.bootstrap._opencode import merge_opencode_json

        merged = merge_opencode_json({"mcp": {}}, self._entry())  # type: ignore[arg-type]

        assert merged["instructions"] == [".opencode/INSTRUCTIONS.md"]

    def test_fresh_install_and_merge_reference_the_same_artifact(self, tmp_path: Path) -> None:
        """A drift between the seed and the merge would leave one path dangling."""
        from trw_mcp.bootstrap._opencode import generate_opencode_config

        generate_opencode_config(tmp_path)
        seeded = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))

        assert ".opencode/INSTRUCTIONS.md" in seeded["instructions"]


class TestT4ClientsAreDeliberatelyExcluded:
    """FR06: the exclusion set is the mechanism, not a comment."""

    def test_t4_clients_are_deliberately_excluded(self) -> None:
        assert INCLUDE_INCAPABLE_CLIENTS == ("copilot", "cursor-cli", "cursor-ide", "antigravity-cli")

    def test_every_excluded_client_is_a_real_profile(self) -> None:
        for client in INCLUDE_INCAPABLE_CLIENTS:
            assert client in _PROFILES, f"{client} is excluded but is not a real client profile"

    def test_excluded_clients_declare_no_import_syntax(self) -> None:
        """The declared set must match what the profiles actually say.

        If a profile later gains ``at_path`` while still listed here, the two
        sources disagree and one of them is silently wrong.
        """
        for client in INCLUDE_INCAPABLE_CLIENTS:
            assert resolve_client_profile(client).instruction_import_syntax == "none", (
                f"{client} is in the exclusion set but its profile claims an import syntax"
            )

    def test_exclusion_set_is_exactly_the_import_incapable_profiles(self) -> None:
        """Totality: no client may be import-incapable without being declared.

        This is the assertion that makes the set a mechanism. A new client added
        with the default ``instruction_import_syntax="none"`` fails here until
        someone decides — explicitly — which tier it belongs to.
        """
        incapable = {
            client_id
            for client_id in _PROFILES
            if resolve_client_profile(client_id).instruction_import_syntax == "none"
        }
        declared = set(INCLUDE_INCAPABLE_CLIENTS) | set(_T2_CLIENTS)

        assert incapable == declared, (
            f"undeclared import-incapable client(s): {sorted(incapable - declared)}; "
            f"declared-but-capable: {sorted(declared - incapable)}"
        )

    @pytest.mark.parametrize("client", INCLUDE_INCAPABLE_CLIENTS)
    def test_excluded_client_resolves_to_inline(self, client: str) -> None:
        """An excluded client must never be handed an import it cannot resolve."""
        from trw_mcp.state.claude_md._carrier_classify import (
            InstructionFileClass,
            InstructionFileClassification,
        )
        from trw_mcp.state.claude_md._instruction_carrier import CarrierMode, resolve_carrier_mode

        mode = resolve_carrier_mode(
            InstructionFileClassification(InstructionFileClass.CONTENT),
            import_syntax=resolve_client_profile(client).instruction_import_syntax,
            externalize="auto",
            scope="root",
        )

        assert mode is CarrierMode.INLINE

    def test_inline_block_still_carries_the_protocol(self) -> None:
        """FRAMEWORK-CORE: for a light client the instruction file IS the carrier.

        Minimising the block must not drop what makes it a protocol carrier —
        the deliver gate and the rigid tool set. An excluded client has no
        sidecar to fall back on.
        """
        from trw_mcp.state.claude_md.sections._tool_lifecycle import (
            DELIVER_GATE_PHRASE,
            render_deliver_gate_statement,
        )

        rendered = render_deliver_gate_statement()

        assert DELIVER_GATE_PHRASE in rendered
        assert "trw_deliver" in rendered


class TestT1ClientsResolveToImport:
    """FR03: a client that CAN resolve an include must actually be given one."""

    @pytest.mark.parametrize("client", _T1_CLIENTS)
    def test_import_capable_client_resolves_to_import(self, client: str) -> None:
        from trw_mcp.state.claude_md._carrier_classify import (
            InstructionFileClass,
            InstructionFileClassification,
        )
        from trw_mcp.state.claude_md._instruction_carrier import CarrierMode, resolve_carrier_mode

        mode = resolve_carrier_mode(
            InstructionFileClassification(InstructionFileClass.CONTENT),
            import_syntax=resolve_client_profile(client).instruction_import_syntax,
            externalize="auto",
            scope="root",
        )

        assert mode is CarrierMode.IMPORT, f"{client} declares an include syntax but the carrier still inlines for it"

    def test_copilot_declares_no_include_because_its_ide_surface_has_none(self) -> None:
        """Capability is per-SURFACE here, and the profile is per-CLIENT.

        The Copilot CLI docs do document `@relpath`. The repository-instructions
        docs and VS Code's custom-instructions docs document no inclusion syntax
        at all for `.github/copilot-instructions.md` — only inline Markdown, with
        links being references a human follows. Since one profile serves both
        surfaces, the weaker surface governs: an include the IDE cannot resolve
        ships a file that exists, parses, reports success and carries nothing.

        The include-free path is `.github/instructions/*.instructions.md` with
        `applyTo: "**"`, which Copilot loads itself.
        """
        assert resolve_client_profile("copilot").instruction_import_syntax == "none"

    def test_the_emitted_import_is_repo_relative(self) -> None:
        """Whatever the sidecar is named, the directive TRW writes must stay in-repo."""
        from trw_mcp.state.claude_md._instruction_carrier import render_import_region

        region = render_import_region(".trw/INSTRUCTIONS.md")
        directive = next(ln.strip() for ln in region.splitlines() if ln.strip().startswith("@"))
        target = directive[1:]

        assert not target.startswith("/"), "absolute import is rejected by Copilot"
        assert not target.startswith("~"), "home-rooted import is rejected by Copilot"
        assert not target.startswith(".."), "an escaping relative path leaves the repo"


class TestCopilotCannotUseAnInclude:
    """The include was shipped, then withdrawn when the docs were actually read.

    `instruction_import_syntax="at_path_repo_relative"` was set on the strength
    of GitHub's Copilot **CLI** documentation, which does describe `@relpath`.
    But one profile serves two surfaces — it also writes `.vscode/mcp.json` and
    is documented as covering GitHub Copilot generally — and neither GitHub's
    repository-instructions page nor VS Code's custom-instructions page
    describes any file-inclusion syntax for `.github/copilot-instructions.md`.
    Markdown links are there, but a link is a reference a human follows, not
    content pulled into the prompt.

    So the emitted `@` line was a dangling literal for every Copilot Chat user:
    a file that exists, parses, reports success, and carries nothing — the P5
    success-shaped failure, strictly worse than the injection it replaced.

    The lesson is in the shape, not the client: capability was declared
    per-CLIENT when it actually varies per-SURFACE, and the stronger surface's
    docs were the ones consulted.
    """

    def _install(self, tmp_path: Path) -> str:
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="copilot")
        return (tmp_path / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")

    def test_no_unresolvable_import_directive_is_emitted(self, tmp_path: Path) -> None:
        text = self._install(tmp_path)

        assert not any(line.strip().startswith("@") for line in text.splitlines()), (
            "an @ directive here is inert text for Copilot Chat"
        )

    def test_the_protocol_is_actually_present(self, tmp_path: Path) -> None:
        """The always-on file must carry the content, since nothing can pull it in.

        GitHub's docs call `.github/copilot-instructions.md` always-on and
        "automatically included in every chat request" — which is what makes
        inline correct here rather than merely acceptable.
        """
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        text = self._install(tmp_path)

        assert DELIVER_GATE_PHRASE in text
        assert "trw_session_start" in text

    def test_no_orphaned_sidecar_is_left_behind(self, tmp_path: Path) -> None:
        """Withdrawing the include must not leave the file it pointed at."""
        self._install(tmp_path)

        assert not (tmp_path / ".trw" / "COPILOT-INSTRUCTIONS.md").exists()


class TestT4BlockIsMinimised:
    """FR06's other half: if the block must stay inline, it must be the small one.

    cursor-cli cannot resolve any include, so AGENTS.md is its ONLY protocol
    carrier and the text has to be there. That makes minimising it the obligation
    instead — and it was not being met: the install path passed the FULL AGENTS.md
    section to a client whose own profile declares `ceremony_mode="light"`, so a
    light client carried the heavy body while the sync path (which picks by
    ceremony mode) would have given it the compact one.

    FRAMEWORK-CORE sets the floor this cannot cross: for a light client the
    generated instruction file IS the protocol carrier, so the deliver gate and
    the rigid tool set stay in it verbatim. Minimise down to that, not past it.
    """

    def test_cursor_cli_block_uses_the_light_body(self, tmp_path: Path) -> None:
        import subprocess

        from trw_mcp.bootstrap import init_project
        from trw_mcp.state.claude_md._static_sections import (
            render_agents_trw_section,
            render_minimal_protocol,
        )

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="cursor-cli")

        text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        assert len(text) < len(render_agents_trw_section()), "the full section is not the minimal one"
        assert len(render_minimal_protocol()) <= len(text)

    def test_minimising_did_not_drop_the_protocol_floor(self, tmp_path: Path) -> None:
        """The gate and session-start mandate survive the reduction."""
        import subprocess

        from trw_mcp.bootstrap import init_project
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="cursor-cli")

        text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

        assert DELIVER_GATE_PHRASE in text
        assert "trw_session_start" in text

    def test_body_follows_the_profile_not_a_literal(self) -> None:
        """The size choice is derived from ceremony_mode, so a profile change is honoured."""
        from trw_mcp.bootstrap._cursor_cli import _cursor_cli_trw_section
        from trw_mcp.state.claude_md._static_sections import render_minimal_protocol

        assert resolve_client_profile("cursor-cli").ceremony_mode == "light"
        assert _cursor_cli_trw_section() == render_minimal_protocol()


class TestClaudeMdWrittenOnlyWhereRead:
    """The registry decides who gets a CLAUDE.md block — not the writer's habit.

    Only claude-code declares `write_targets.claude_md`. The MCP sync path
    honoured that (`_determine_write_target_decision`); bootstrap did not, and
    scaffolded the full protocol into CLAUDE.md for every client. A codex or
    opencode project therefore carried a THIRD copy of the framework text in a
    file none of its clients load — which then froze in place while the
    surfaces those clients do read moved on. Two entry points, one file,
    opposite decisions.
    """

    def _install(self, root: Path, client: str) -> str | None:
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(root)], check=True)
        init_project(root, ide=client)
        claude_md = root / "CLAUDE.md"
        return claude_md.read_text(encoding="utf-8") if claude_md.is_file() else None

    @pytest.mark.parametrize("client", ["codex", "copilot", "cursor-cli", "opencode", "antigravity-cli"])
    def test_unclaimed_client_gets_no_trw_block(self, tmp_path: Path, client: str) -> None:
        """PRD-CORE-262-FR05: a codex-only install gets no CLAUDE.md at all.

        None of the other four unclaimed clients here changed -- they still
        receive the import-free scaffold shell this class exists to verify.
        Only codex-only drops the file entirely, so "no TRW block" holds
        trivially (there is no file for one to appear in).
        """
        text = self._install(tmp_path, client)

        if client == "codex":
            assert text is None, "codex-only must get no root CLAUDE.md (FR05)"
            return

        assert text is not None, f"{client} must still receive the scaffolded CLAUDE.md shell"
        assert TRW_MARKER_START not in text
        assert "trw_session_start" not in text

    def test_the_scaffold_itself_survives(self, tmp_path: Path) -> None:
        """Skipping the block must not stop CLAUDE.md being scaffolded.

        Uses copilot, not codex: PRD-CORE-262-FR05 made codex-only the ONE
        selection that gets no CLAUDE.md at all (see
        ``test_unclaimed_client_gets_no_trw_block``), so it can no longer
        stand in for "an unclaimed client whose file still scaffolds."
        copilot is unclaimed the same way and is unaffected by FR05.
        """
        text = self._install(tmp_path, "copilot")

        assert text is not None
        assert "# Project Instructions" in text

    def test_claude_code_still_gets_the_import(self, tmp_path: Path) -> None:
        text = self._install(tmp_path, "claude-code")

        assert "@.trw/INSTRUCTIONS.md" in text

    def test_cursor_ide_carries_its_protocol_in_the_always_applied_rule(self, tmp_path: Path) -> None:
        """cursor-ide's CLAUDE.md fallback is gone — but only because the rule replaced it.

        The fallback existed for a client with nowhere else to put the
        protocol. `.cursor/rules/trw-ceremony.mdc` is `alwaysApply: true`, so
        Cursor resolves it eagerly; it now carries the full shared protocol
        including the deliver gate, which it previously did NOT (it was built
        from the CLAUDE.md scaffold template rather than the shared renderer).
        Assert the replacement before asserting the removal — in that order,
        because the removal is only safe if the replacement holds.
        """
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        text = self._install(tmp_path, "cursor-ide")
        rule = (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").read_text(encoding="utf-8")

        assert "alwaysApply: true" in rule
        assert DELIVER_GATE_PHRASE in rule
        assert "trw_session_start" in rule
        # Assert the replacement BEFORE the removal, in that order: withdrawing
        # the CLAUDE.md block is only safe because the rule above carries the
        # protocol. Retiring this needed the record to become trustworthy first
        # — install used to launder `which cursor` into `target_platforms`.
        assert TRW_MARKER_START not in text

    def test_the_decision_is_derived_from_profiles(self) -> None:
        """No hardcoded client name: a profile flag flip must be honoured here."""
        from trw_mcp.state.claude_md._agents_md import _any_client_writes_claude_md

        assert _any_client_writes_claude_md(["claude-code"]) is True
        assert _any_client_writes_claude_md(["codex"]) is False
        assert _any_client_writes_claude_md(["codex", "claude-code"]) is True
        # An empty set is the default scaffold, not an unclaimed surface.
        assert _any_client_writes_claude_md([]) is True

    def test_a_frozen_block_is_removed_without_touching_user_prose(self, tmp_path: Path) -> None:
        """HB-2: only the marked region goes, and only when nobody claims it."""
        from trw_mcp.state.claude_md._agents_md import strip_orphaned_claude_md_block

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            f"# My Project\n\nKeep this paragraph.\n\n{TRW_MARKER_START}\nstale protocol\n{TRW_MARKER_END}\n\nAnd this one.\n",
            encoding="utf-8",
        )

        assert strip_orphaned_claude_md_block(tmp_path, ["codex"]) is True

        text = claude_md.read_text(encoding="utf-8")
        assert "Keep this paragraph." in text
        assert "And this one." in text
        assert "stale protocol" not in text

    def test_a_claimed_surface_is_left_alone(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._agents_md import strip_orphaned_claude_md_block

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(f"{TRW_MARKER_START}\nprotocol\n{TRW_MARKER_END}\n", encoding="utf-8")

        assert strip_orphaned_claude_md_block(tmp_path, ["claude-code"]) is False
        assert "protocol" in claude_md.read_text(encoding="utf-8")


class TestTheDecisionSurvivesReinstall:
    """A fix that the next `update-project` undoes is not a fix.

    This is the shape that let the carrier be reverted before (W1/W11): one
    entry point decides, another overwrites, and the installer reports success
    either way. The extra hazard here is that detection cannot answer "who
    reads this file?" after an install — TRW writes `.claude/` (agents, hooks,
    skills) into every project whatever the client, so a codex-only project
    reports claude-code from then on and the decision flips back on re-run.
    """

    def _init(self, root: Path, client: str) -> None:
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(root)], check=True)
        init_project(root, ide=client)

    @pytest.mark.parametrize("client", ["codex", "opencode", "cursor-cli"])
    def test_update_project_does_not_reinject(self, tmp_path: Path, client: str) -> None:
        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, client)
        update_project(tmp_path, ide=client)
        update_project(tmp_path, ide=client)

        assert TRW_MARKER_START not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    def test_claude_code_keeps_its_block_across_updates(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, "claude-code")
        update_project(tmp_path, ide="claude-code")

        assert "@.trw/INSTRUCTIONS.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    def test_install_records_the_chosen_clients(self, tmp_path: Path) -> None:
        """Without the record there is nothing to prefer over poisoned detection."""
        import yaml

        self._init(tmp_path, "codex")
        data = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8"))

        assert data["target_platforms"] == ["codex"]

    def test_recorded_targets_beat_detection(self, tmp_path: Path) -> None:
        """The whole point: our own install artifacts must not outvote the record.

        Uses opencode, not codex: PRD-CORE-262-FR05 made codex-only the one
        selection that no longer scaffolds `.claude/`, so a codex-only install
        can no longer manufacture the false-positive claude-code detection
        this test needs to prove the record wins over it. opencode is
        unaffected by FR05 and still gets the full `.claude/` scaffold, so the
        same false-positive-detection setup still applies.
        """
        from trw_mcp.bootstrap._template_claude_md import _recorded_or_detected_targets
        from trw_mcp.bootstrap._utils import detect_ide

        self._init(tmp_path, "opencode")

        # Detection sees claude-code because installing created `.claude/`.
        assert "claude-code" in detect_ide(tmp_path)
        assert _recorded_or_detected_targets(tmp_path) == ["opencode"]

    def test_detection_is_the_fallback_when_nothing_was_recorded(self, tmp_path: Path) -> None:
        """Projects predating the record still resolve, just less reliably."""
        from trw_mcp.bootstrap._template_claude_md import _recorded_or_detected_targets

        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("target_platforms:\n", encoding="utf-8")

        assert _recorded_or_detected_targets(tmp_path)


class TestReviewerFoundReinjection:
    """An independent review falsified the "durable" claim. These pin the fix.

    I verified durability with `update_project(root, ide=client)` — always
    passing the client. The DOCUMENTED invocation is bare `update-project`, and
    with no override it fed raw detection into an append-only recorder. Since
    TRW writes `.claude/` into every project (hooks and skills are universal),
    detection reported claude-code, the record gained it permanently, and the
    CLAUDE.md block came back. Passing the argument was exactly what hid it.
    """

    def _init(self, root: Path, client: str) -> None:
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(root)], check=True)
        init_project(root, ide=client)

    @pytest.mark.parametrize("client", ["codex", "opencode", "cursor-cli"])
    def test_bare_update_does_not_reinject(self, tmp_path: Path, client: str) -> None:
        """No `ide=` — the invocation every doc and 164 of 191 test call sites use."""
        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, client)
        update_project(tmp_path)
        update_project(tmp_path)

        assert TRW_MARKER_START not in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")

    @pytest.mark.parametrize("client", ["codex", "opencode"])
    def test_bare_update_does_not_record_claude_code(self, tmp_path: Path, client: str) -> None:
        """The record is append-only, so one bad append is permanent."""
        import yaml

        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, client)
        update_project(tmp_path)

        recorded = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8"))
        assert "claude-code" not in recorded["target_platforms"]

    def test_a_genuinely_adopted_client_is_still_recorded(self, tmp_path: Path) -> None:
        """The fix must not cost real adoption — that is the tradeoff to guard."""
        import yaml

        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, "codex")
        (tmp_path / "opencode.json").write_text("{}", encoding="utf-8")
        update_project(tmp_path)

        recorded = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8"))
        assert "opencode" in recorded["target_platforms"]

    @pytest.mark.parametrize("client", ["codex", "opencode", "cursor-cli"])
    def test_instructions_sync_defaults_do_not_reinject(self, tmp_path: Path, client: str) -> None:
        """`trw_instructions_sync()` client="auto" is what the protocol tells agents to call.

        PRD-CORE-262-FR05: a codex-only ``init_project`` no longer scaffolds a
        root CLAUDE.md at all, so there is nothing for the sync to reinject
        into -- and the sync call must not fabricate one either, since codex
        reads `.codex/INSTRUCTIONS.md`, not CLAUDE.md.
        """
        import os

        from trw_mcp.models.config import _reset_config, get_config
        from trw_mcp.state.claude_md import execute_claude_md_sync
        from trw_mcp.state.persistence import FileStateReader

        self._init(tmp_path, client)
        cwd = os.getcwd()
        os.chdir(tmp_path)
        _reset_config()
        try:
            execute_claude_md_sync("root", None, get_config(), FileStateReader(), None, "auto")
        finally:
            os.chdir(cwd)
            _reset_config()

        claude_md = tmp_path / "CLAUDE.md"
        if client == "codex":
            assert not claude_md.exists(), "codex-only must get no root CLAUDE.md, sync must not fabricate one (FR05)"
            return

        assert TRW_MARKER_START not in claude_md.read_text(encoding="utf-8")

    def test_a_second_well_formed_block_is_reported_not_silently_frozen(self, tmp_path: Path) -> None:
        """merge_trw_section binds the FIRST pair; the rest go stale without a word.

        Refusing here is not better — the caller's malformed path appends, which
        would add a third block. But a silently frozen live block is the exact
        failure this work exists to remove, so it must be visible.
        """
        import structlog

        from trw_mcp.state.claude_md._parser import merge_trw_section

        target = tmp_path / "CLAUDE.md"
        target.write_text(
            f"# Doc\n\n{TRW_MARKER_START}\nONE\n{TRW_MARKER_END}\n\nUser text.\n\n{TRW_MARKER_START}\nTWO\n{TRW_MARKER_END}\n",
            encoding="utf-8",
        )

        with structlog.testing.capture_logs() as logs:
            merge_trw_section(target, "FRESH", 500)

        assert any(entry.get("event") == "trw_block_duplicate_markers" for entry in logs)
        assert "User text." in target.read_text(encoding="utf-8")


class TestOrphanedAgentsMdBlockIsRemovedByUpdate:
    """Drives `update_project`, deliberately — the first version tested the helper.

    `strip_orphaned_agents_md_block` shipped once with six passing tests and ZERO
    production callers. Every test called the function directly, so they proved
    the code worked and said nothing about whether it ever ran; it was later
    deleted for exactly that. These tests call the entry point a user calls, so
    they fail if the wiring is ever dropped again.
    """

    def _opencode_project_with_a_stale_block(self, tmp_path: Path) -> Path:
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="opencode")
        # What a project installed BEFORE the withdrawal looks like: TRW's block
        # frozen in AGENTS.md, with user content on both sides of it.
        agents = tmp_path / "AGENTS.md"
        agents.write_text(
            f"# House rules\n\nKeep this.\n\n{TRW_MARKER_START}\nstale protocol\n{TRW_MARKER_END}\n\nAnd this.\n",
            encoding="utf-8",
        )
        return agents

    def test_update_removes_the_block_no_client_claims(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import update_project

        agents = self._opencode_project_with_a_stale_block(tmp_path)
        update_project(tmp_path, ide="opencode")

        assert "stale protocol" not in agents.read_text(encoding="utf-8")

    def test_update_keeps_user_content_on_both_sides(self, tmp_path: Path) -> None:
        """HB-2: only the marked region goes."""
        from trw_mcp.bootstrap import update_project

        agents = self._opencode_project_with_a_stale_block(tmp_path)
        update_project(tmp_path, ide="opencode")

        text = agents.read_text(encoding="utf-8")
        assert "Keep this." in text
        assert "And this." in text

    def test_update_leaves_a_claimed_surface_alone(self, tmp_path: Path) -> None:
        """cursor-cli still declares AGENTS.md, so its block must survive an update.

        The control for the strip above: without a client that still claims the
        surface, "removes the block" would pass even if the code removed it
        unconditionally. cursor-cli is the example because codex was withdrawn.
        """
        import subprocess

        from trw_mcp.bootstrap import init_project, update_project
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="cursor-cli")
        update_project(tmp_path, ide="cursor-cli")

        # Asserted on the protocol, not the marker: TRW owns cursor-cli's
        # AGENTS.md wholesale and writes it without a trw:start/end pair, so a
        # marker assertion would pass vacuously whatever the strip did.
        assert DELIVER_GATE_PHRASE in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


class TestUpdateDoesNotScaffoldFromPath:
    """A binary on the developer's PATH must not add surfaces to a project.

    Closes the residual left open by 6430e79d61. All five `_update_*_artifacts`
    functions re-resolved their own targets through `resolve_ide_targets`, which
    falls through to `detect_ide` when no `--ide` is given — and detection fires
    on `shutil.which("cursor")`. So a bare `update-project` in a codex-only
    project scaffolded `.cursor/`, and that TRW-created directory then became the
    next run's "evidence", appending cursor-ide to an append-only record forever.
    """

    def test_bare_update_does_not_create_another_clients_directory(self, tmp_path: Path) -> None:
        import subprocess

        from trw_mcp.bootstrap import init_project, update_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="codex")
        update_project(tmp_path)
        update_project(tmp_path)

        assert not (tmp_path / ".cursor").exists(), "PATH detection scaffolded a surface this project never chose"

    def test_the_record_stays_what_the_user_chose(self, tmp_path: Path) -> None:
        """target_platforms is append-only, so one bad append is permanent."""
        import subprocess

        import yaml

        from trw_mcp.bootstrap import init_project, update_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="codex")
        update_project(tmp_path)
        update_project(tmp_path)

        recorded = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8"))
        assert recorded["target_platforms"] == ["codex"]

    def test_an_explicit_override_still_wins(self, tmp_path: Path) -> None:
        """Record-first must not stop a user deliberately adding a client."""
        import subprocess

        from trw_mcp.bootstrap import init_project, update_project

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="codex")
        update_project(tmp_path, ide="cursor-ide")

        assert (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").is_file()
