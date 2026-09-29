"""Per-client include matrix — PRD-CORE-240 FR04, FR05, FR06.

TRW writes into files it does not own, so each client gets the TRW block in the
file that client actually loads, and nowhere else. PRD-QUAL-143-FR01 made the block
inline for every client, because an instruction file that names an include the
client cannot resolve exists, parses, reports success, and carries nothing.
PRD-CORE-341 reintroduced one include, deliberately: the block lives in the
TRW-owned ``.trw/INSTRUCTIONS.md`` and AGENTS.md holds a link to it (an ``@``
import Claude Code expands, plus a sentence naming the file for clients that
cannot). The dedicated carriers stay inline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state.claude_md._instructions_link import INSTRUCTIONS_RELPATH, LINK_BODY
from trw_mcp.state.claude_md._parser import TRW_MARKER_END, TRW_MARKER_START

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

# T2: no in-file include syntax, but the client's own config names which files to
# load — so the TRW artifact is registered there and the shared AGENTS.md is left
# entirely alone.
_T2_CLIENTS = ("opencode", "codex")


#: This test's own commits run no git hooks: init_project installs TRW's post-commit hook, whose
#: background worker auto-starts a memory daemon after the test has returned (rc9 C2 FR07 leaks).
_NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


def _commit_all(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *_NO_HOOKS, "commit", "-qm", "fixture"],
        check=True,
        capture_output=True,
    )


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

    cursor-cli cannot resolve any include, so the file its AGENTS.md link names
    (``.trw/INSTRUCTIONS.md``) is its ONLY protocol carrier and the text has to be
    there. That makes minimising it the obligation for a light client profile, chosen by
    ceremony mode. The choice now lives in ONE function (``render_instructions_body``) that sync,
    init and every client installer call; the installer used to pick its own body and so
    rewrote what sync wrote.

    FRAMEWORK.md sets the floor this cannot cross: for a light client the
    generated instruction file IS the protocol carrier, so the deliver gate and
    the rigid tool set stay in it verbatim. Minimise down to that, not past it.
    """

    def test_the_body_follows_the_client_profile_in_one_function(self, tmp_path: Path) -> None:
        """A light client profile gets the minimal body, any other the full section, from ONE renderer."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.claude_md._instructions_link import render_instructions_body
        from trw_mcp.state.claude_md._static_sections import (
            render_agents_trw_section,
            render_minimal_protocol,
        )

        light = TRWConfig(target_platforms=["cursor-cli"])
        assert light.effective_ceremony_mode == "light"
        assert render_instructions_body(tmp_path, light) == render_minimal_protocol()
        assert render_instructions_body(tmp_path, TRWConfig()) == render_agents_trw_section()
        assert len(render_minimal_protocol()) < len(render_agents_trw_section())

    def test_cursor_cli_init_writes_the_shared_body(self, tmp_path: Path) -> None:
        """No writer renders a private variant: the installer's file is the one ``instructions sync`` writes."""
        import subprocess

        from trw_mcp.bootstrap import init_project
        from trw_mcp.state.claude_md._instructions_link import render_instructions_body, render_instructions_file

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="cursor-cli")

        text = (tmp_path / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")
        assert LINK_BODY in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert text == render_instructions_file(render_instructions_body(tmp_path))

    def test_minimising_did_not_drop_the_protocol_floor(self, tmp_path: Path) -> None:
        """The gate and session-start mandate survive the reduction."""
        import subprocess

        from trw_mcp.bootstrap import init_project
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        init_project(tmp_path, ide="cursor-cli")

        text = (tmp_path / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")

        assert DELIVER_GATE_PHRASE in text
        assert "trw_session_start" in text

    def test_body_follows_the_profile_not_a_literal(self) -> None:
        """The size choice is derived from ceremony_mode, so a profile change is honoured."""
        from trw_mcp.bootstrap._cursor_cli import _cursor_cli_trw_section
        from trw_mcp.state.claude_md._static_sections import render_minimal_protocol

        assert resolve_client_profile("cursor-cli").ceremony_mode == "light"
        assert _cursor_cli_trw_section() == render_minimal_protocol()


class TestClaudeMdWrittenOnlyWhereRead:
    """TRW 8.0 writes no CLAUDE.md for any client.

    Claude Code reads AGENTS.md natively and expands its ``@.trw/INSTRUCTIONS.md``
    link, so claude-code shares the AGENTS.md carrier; every other client has its own. A legacy CLAUDE.md that holds only
    TRW content is retired; one with user content is left byte-identical.
    """

    def _install(self, root: Path, client: str) -> str | None:
        import subprocess

        from trw_mcp.bootstrap import init_project

        subprocess.run(["git", "init", "-q", str(root)], check=True)
        init_project(root, ide=client)
        _commit_all(root)
        claude_md = root / "CLAUDE.md"
        return claude_md.read_text(encoding="utf-8") if claude_md.is_file() else None

    @pytest.mark.parametrize("client", ["claude-code", "codex", "copilot", "cursor-cli", "opencode", "antigravity-cli"])
    def test_no_client_gets_a_claude_md(self, tmp_path: Path, client: str) -> None:
        assert self._install(tmp_path, client) is None

    def test_claude_code_gets_the_block_in_agents_md(self, tmp_path: Path) -> None:
        self._install(tmp_path, "claude-code")

        assert LINK_BODY in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "trw_session_start" in (tmp_path / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")

    def test_cursor_ide_carries_its_protocol_in_the_always_applied_rule(self, tmp_path: Path) -> None:
        """cursor-ide's protocol lives in `.cursor/rules/trw-ceremony.mdc` (`alwaysApply: true`)."""
        from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

        text = self._install(tmp_path, "cursor-ide")
        rule = (tmp_path / ".cursor" / "rules" / "trw-ceremony.mdc").read_text(encoding="utf-8")

        assert "alwaysApply: true" in rule
        assert DELIVER_GATE_PHRASE in rule
        assert "trw_session_start" in rule
        assert text is None

    def test_a_claude_md_with_user_prose_is_left_untouched(self, tmp_path: Path) -> None:
        """HB-2: a legacy CLAUDE.md holding user content is never edited or deleted."""
        from trw_mcp.state.claude_md._agents_md import retire_legacy_claude_md

        claude_md = tmp_path / "CLAUDE.md"
        original = (
            f"# My Project\n\nKeep this paragraph.\n\n{TRW_MARKER_START}\nstale protocol\n{TRW_MARKER_END}\n"
            "\nAnd this one.\n"
        )
        claude_md.write_text(original, encoding="utf-8")

        assert retire_legacy_claude_md(tmp_path) == "kept"
        assert claude_md.read_text(encoding="utf-8") == original

    def test_a_trw_only_claude_md_is_retired(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._agents_md import retire_legacy_claude_md

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(f"@AGENTS.md\n\n{TRW_MARKER_START}\nprotocol\n{TRW_MARKER_END}\n", encoding="utf-8")

        assert retire_legacy_claude_md(tmp_path) == "removed"
        assert not claude_md.exists()


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
        _commit_all(root)

    @pytest.mark.parametrize("client", ["codex", "opencode", "cursor-cli"])
    def test_update_project_does_not_reinject(self, tmp_path: Path, client: str) -> None:
        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, client)
        update_project(tmp_path, ide=client)
        update_project(tmp_path, ide=client)

        assert not (tmp_path / "CLAUDE.md").exists()

    def test_claude_code_keeps_its_block_across_updates(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, "claude-code")
        update_project(tmp_path, ide="claude-code")

        assert LINK_BODY in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert "trw_session_start" in (tmp_path / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_install_records_the_chosen_clients(self, tmp_path: Path) -> None:
        """Without the record there is nothing to prefer over poisoned detection."""
        import yaml

        self._init(tmp_path, "codex")
        data = yaml.safe_load((tmp_path / ".trw" / "config.yaml").read_text(encoding="utf-8"))

        assert data["target_platforms"] == ["codex"]

    def test_recorded_targets_beat_detection(self, tmp_path: Path) -> None:
        """The whole point: our own install artifacts must not outvote the record.

        Since PRD-INFRA-192 FR09 an explicit non-claude install no longer
        scaffolds `.claude/`, so the false-positive claude-code detection this
        test needs now comes from the case that still produces it: a `.claude/`
        tree an OLDER install left in the project.
        """
        from trw_mcp.bootstrap._template_claude_md import _recorded_or_detected_targets
        from trw_mcp.bootstrap._utils import detect_ide

        self._init(tmp_path, "opencode")
        (tmp_path / ".claude" / "skills").mkdir(parents=True)

        # Detection sees claude-code because of the leftover `.claude/`.
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
        _commit_all(root)

    @pytest.mark.parametrize("client", ["codex", "opencode", "cursor-cli"])
    def test_bare_update_does_not_reinject(self, tmp_path: Path, client: str) -> None:
        """No `ide=` — the invocation every doc and 164 of 191 test call sites use."""
        from trw_mcp.bootstrap import update_project

        self._init(tmp_path, client)
        update_project(tmp_path)
        update_project(tmp_path)

        assert not (tmp_path / "CLAUDE.md").exists()

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

        # A TRW-written opencode install missing from the record is adopted on file-by-file proof;
        # a bare marker the user created (opencode.json = "{}") is not proof and is not adopted.
        self._init(tmp_path, "opencode")
        config = tmp_path / ".trw" / "config.yaml"
        data = yaml.safe_load(config.read_text(encoding="utf-8"))
        data["target_platforms"] = ["codex"]
        config.write_text(yaml.safe_dump(data), encoding="utf-8")
        result = update_project(tmp_path)

        recorded = yaml.safe_load(config.read_text(encoding="utf-8"))
        assert "opencode" in recorded["target_platforms"], result.get("warnings")

    @pytest.mark.parametrize("client", ["codex", "opencode", "cursor-cli"])
    def test_instructions_sync_defaults_do_not_reinject(self, tmp_path: Path, client: str) -> None:
        """``trw-mcp instructions sync`` client="auto" is what the protocol tells agents to call.

        TRW 8.0 writes no CLAUDE.md, and the sync must not fabricate one.
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

        assert not (tmp_path / "CLAUDE.md").exists()

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
        # Committed, as a real pre-withdrawal project is: update-project never
        # rewrites an UNCOMMITTED file it did not write (PRD-INFRA-190 FR04).
        _commit_all(tmp_path)
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
        """cursor-cli still declares AGENTS.md, so its link and protocol must survive an update.

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

        # PRD-CORE-341: the claimed surface keeps its link and the protocol stays
        # in the file the link names. Asserted on the link body and the protocol,
        # not the marker, so a marker assertion cannot pass vacuously.
        assert LINK_BODY in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
        assert DELIVER_GATE_PHRASE in (tmp_path / INSTRUCTIONS_RELPATH).read_text(encoding="utf-8")


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
