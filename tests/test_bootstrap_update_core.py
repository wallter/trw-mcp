"""Split bootstrap update core behavior tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.models.config import TRWConfig

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def _bundle_with_override(bundle_root: Path, rel: str, content: str) -> Path:
    """Return a copy of the shipped data dir with one artifact set to *content*.

    Models "bundle version N" for the stale-artifact tests. Installing from this
    directory makes TRW the author of the older content, which is what gives
    ``managed-artifacts.yaml`` a legitimate ownership hash — as opposed to
    hand-writing the file and re-baselining it, which records the writer's bytes
    as TRW's and is the PRD-FIX-121 defect itself.

    *bundle_root* must be OUTSIDE the target project (the repo fixture is
    ``tmp_path`` itself), so use ``tmp_path_factory``.
    """
    import shutil

    from trw_mcp.bootstrap._utils import _DATA_DIR

    old_bundle = bundle_root / "data"
    if not old_bundle.exists():
        shutil.copytree(_DATA_DIR, old_bundle)
    target = old_bundle / rel
    assert target.is_file(), f"{rel} is not a bundled artifact"
    target.write_text(content, encoding="utf-8")
    return old_bundle


def resolve_instruction_text(instruction_file: Path) -> str:
    """Return an instruction file's text with its ``@``-imports resolved.

    The TRW block reaches a client instruction file through a *carrier*
    (PRD-CORE-203): either INLINE between the markers, or externalized to a
    ``.trw/`` sidecar with a single ``@<relpath>`` import left in its place.
    Assertions about protocol content must hold under both, so they run against
    the resolved text rather than the raw file.

    This is deliberately stronger than the previous raw-substring assertions: a
    dangling import (one whose target is missing or empty) contributes nothing
    here, so the protocol assertion fails -- which is the correct outcome and
    exactly the failure a raw read could not distinguish from success.

    Resolution is single-hop and relative to the *containing file's* directory,
    matching Claude Code's documented semantics.
    """
    text = instruction_file.read_text(encoding="utf-8")
    parts = [text]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@") or len(stripped.split()) != 1:
            continue
        target = instruction_file.parent / stripped[1:]
        if target.is_file():
            parts.append(target.read_text(encoding="utf-8"))
    return "\n".join(parts)


class TestUpdateProjectBasics:
    """Test update_project basic behavior."""

    def test_requires_trw_installed(self, tmp_path: Path) -> None:
        """update_project errors if .trw/ does not exist (in a real repo)."""
        (tmp_path / ".git").mkdir()
        result = update_project(tmp_path)
        assert len(result["errors"]) == 1
        assert ".trw/ not found" in result["errors"][0]

    def test_non_git_project_adds_a_second_client_and_warns(self, tmp_path: Path) -> None:
        """Symmetry with init (PRD-INFRA-170-FR06): the installer's second client is an update, outside git too.

        The installer runs ``init-project --ide <first>`` then ``update-project --ide <next>``; refusing the
        update in a directory with no ``.git`` stopped a two-client install halfway.
        """
        target = tmp_path / "scratch"
        target.mkdir()
        assert init_project(target, ide="claude-code")["errors"] == []
        assert not (target / ".codex").exists() and not (target / ".git").exists()

        result = update_project(target, ide="codex")

        assert result["errors"] == []
        assert (target / ".codex" / "config.toml").is_file()
        warnings = [w for w in result["warnings"] if "not a git repository" in w]
        assert len(warnings) == 1 and "git init" in warnings[0], result["warnings"]

    def test_non_git_directory_without_trw_names_init_project(self, tmp_path: Path) -> None:
        """Outside git and never initialised: the one error is the missing install, whose remedy is init-project."""
        result = update_project(tmp_path)
        assert len(result["errors"]) == 1
        assert ".trw/ not found" in result["errors"][0] and "init-project" in result["errors"][0]

    def test_git_repo_gets_no_non_git_warning(self, initialized_repo: Path) -> None:
        result = update_project(initialized_repo)
        assert not [w for w in result["warnings"] if "not a git repository" in w]

    def test_rejects_symlinked_git(self, tmp_path: Path) -> None:
        """A symlinked .git must not satisfy the git-repo guard (symlink-safe)."""
        real_repo = tmp_path / "real"
        real_repo.mkdir()
        (real_repo / ".git").mkdir()
        victim = tmp_path / "victim"
        victim.mkdir()
        (victim / ".trw").mkdir()
        (victim / ".git").symlink_to(real_repo / ".git")
        result = update_project(victim)
        assert any("not a git repository" in e for e in result["errors"])

    def test_no_errors_on_initialized_repo(self, initialized_repo: Path) -> None:
        """update_project succeeds on an initialized repo."""
        result = update_project(initialized_repo)
        assert not result["errors"]

    def test_a_fenced_marker_example_in_agents_md_does_not_roll_the_update_back(self, initialized_repo: Path) -> None:
        """UPDATE-ROLLBACK-FENCED-MARKER: one file TRW cannot safely edit is skipped with a warning; the rest updates."""
        agents = initialized_repo / "AGENTS.md"
        user_text = "# Agents\n\nTo delimit a block write:\n\n```markdown\n<!-- trw:start -->\n```\n"
        agents.write_text(user_text, encoding="utf-8")
        framework = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        framework.write_text("stale\n", encoding="utf-8")

        result = update_project(initialized_repo)

        assert not result["errors"], result["errors"]
        assert agents.read_text(encoding="utf-8") == user_text, "the user's file is left exactly as found"
        assert framework.read_text(encoding="utf-8") != "stale\n", "the rest of the update was kept, not rolled back"
        assert any("AGENTS.md" in w and "ambiguous_markers" in w for w in result["warnings"]), result["warnings"]

    def test_reports_updated_files(self, initialized_repo: Path) -> None:
        """update_project reports exactly the files it changed, repo-relative."""
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        framework = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        framework.write_text("stale\n", encoding="utf-8")
        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        # PRD-INFRA-192 FR10: a manifest-recorded absent path is tombstoned, not
        # recreated (test_bootstrap_tombstones.py). Dropping the record here
        # first models a never-provisioned artifact, so this test still exercises
        # the "created" branch it is named for.
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        data = FileStateReader().read_yaml(manifest_path)
        del data["content_hashes"]["session-start.sh"]
        FileStateWriter().write_yaml(manifest_path, data)
        hook.unlink()

        result = update_project(initialized_repo)

        assert ".trw/frameworks/FRAMEWORK.md" in result["updated"]
        assert ".claude/hooks/session-start.sh" in result["created"]
        assert ".claude/agents/trw-implementer.md" not in result["updated"]  # unchanged → unreported

    def test_reports_preserved_files(self, initialized_repo: Path) -> None:
        """update_project reports user files as preserved."""
        result = update_project(initialized_repo)
        preserved_str = "\n".join(result["preserved"])
        assert "config.yaml" in preserved_str


@pytest.mark.unit
class TestUpdatePreservesUserFiles:
    """Test that update_project never overwrites user-customized files."""

    def test_preserves_config_yaml(self, initialized_repo: Path) -> None:
        """User's config.yaml is never overwritten."""
        config_path = initialized_repo / ".trw" / "config.yaml"
        config_path.write_text("custom_setting: true\n", encoding="utf-8")

        update_project(initialized_repo)

        content = config_path.read_text(encoding="utf-8")
        assert "custom_setting: true" in content

    def test_preserves_learnings(self, initialized_repo: Path) -> None:
        """User's learnings index is never overwritten."""
        index_path = initialized_repo / ".trw" / "learnings" / "index.yaml"
        index_path.write_text("entries:\n- id: L001\n", encoding="utf-8")

        update_project(initialized_repo)

        content = index_path.read_text(encoding="utf-8")
        assert "L001" in content

    def test_preserves_mcp_json(self, initialized_repo: Path) -> None:
        """User's .mcp.json is never overwritten."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text('{"custom": true}\n', encoding="utf-8")

        update_project(initialized_repo)

        content = mcp_path.read_text(encoding="utf-8")
        assert '"custom": true' in content


@pytest.mark.unit
class TestUpdateOverwritesFrameworkFiles:
    """Test that update_project overwrites framework-managed files."""

    def test_updates_framework_md(self, initialized_repo: Path) -> None:
        """FRAMEWORK.md is overwritten with latest version."""
        fw_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        fw_path.write_text("old framework content", encoding="utf-8")

        update_project(initialized_repo)

        content = fw_path.read_text(encoding="utf-8")
        assert content != "old framework content"
        assert TRWConfig().framework_version in content

    def test_updates_hooks(self, initialized_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
        """A stale-but-unedited hook is overwritten with the latest version.

        PRD-FIX-121-FR04. Staleness is established the ONLY legitimate way:
        TRW itself installs an older bundle, so ``managed-artifacts.yaml`` holds
        a hash TRW actually wrote.

        The previous fixture hand-wrote ``"old hook"`` and then called
        ``_write_manifest`` to re-baseline it. That is exactly the laundering
        PRD-FIX-121-FR01 now forbids — the recorder declines content matching
        neither the bundle nor its own prior record — so it can no longer
        manufacture a stale-but-unedited artifact. The behavior under test is
        unchanged and still asserted; only the fixture moved.
        """
        old_bundle = _bundle_with_override(tmp_path_factory.mktemp("bundle-n"), "hooks/session-start.sh", "old hook")
        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"

        # Version N: TRW writes the older hook and records ITS hash as the baseline.
        update_project(initialized_repo, data_dir=old_bundle)
        assert hook_path.read_text(encoding="utf-8") == "old hook"

        # Version N+1: the bundle advances, the user never touched the file.
        result = update_project(initialized_repo)

        content = hook_path.read_text(encoding="utf-8")
        assert content != "old hook"
        # Non-vacuity: refreshed, NOT misclassified as a user edit and preserved.
        assert str(hook_path) not in result.get("modified", [])

    def test_updates_skills(self, initialized_repo: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
        """A stale-but-unedited skill file is overwritten with the latest version.

        PRD-FIX-121-FR04; same fixture migration as :meth:`test_updates_hooks`.
        """
        old_bundle = _bundle_with_override(
            tmp_path_factory.mktemp("bundle-n"), "skills/trw-deliver/SKILL.md", "old skill"
        )
        skill_path = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"

        update_project(initialized_repo, data_dir=old_bundle)
        assert skill_path.read_text(encoding="utf-8") == "old skill"

        result = update_project(initialized_repo)

        content = skill_path.read_text(encoding="utf-8")
        assert content != "old skill"
        assert str(skill_path) not in result.get("modified", [])

    def test_updates_agents(self, initialized_repo: Path) -> None:
        """Framework-managed (unmodified) agents are re-materialized on update.

        A stale on-disk agent left in the raw bundled tier form
        (``model: frontier``) is framework-recognized (not a user edit), so the
        update path self-heals it to the resolved ``model: opus`` line instead
        of freezing it. This proves the PRD-FIX-068-FR05 guard does NOT protect
        framework-managed files (only genuine user edits are preserved).
        """
        from trw_mcp.bootstrap._utils import _DATA_DIR

        agent_path = initialized_repo / ".claude" / "agents" / "trw-lead.md"
        # Raw bundled form carries the unresolved capability tier token.
        raw_bundled = (_DATA_DIR / "agents" / "trw-lead.md").read_text(encoding="utf-8")
        assert "model: frontier" in raw_bundled
        agent_path.write_text(raw_bundled, encoding="utf-8")

        result = update_project(initialized_repo)

        content = agent_path.read_text(encoding="utf-8")
        assert "model: frontier" not in content
        assert "model: opus" in content
        assert str(agent_path) not in result.get("modified", [])


@pytest.mark.unit
class TestUpdateAgentsMdSmartMerge:
    """Test that update_project smart-merges AGENTS.md."""

    def test_preserves_user_sections(self, initialized_repo: Path) -> None:
        """User content above TRW markers is preserved."""
        claude_md = initialized_repo / "AGENTS.md"
        content = claude_md.read_text(encoding="utf-8")

        # Add user content before the TRW section
        user_section = "## My Custom Section\n\nThis is user content.\n\n"
        content = content.replace("<!-- TRW AUTO-GENERATED", user_section + "<!-- TRW AUTO-GENERATED")
        claude_md.write_text(content, encoding="utf-8")

        update_project(initialized_repo)

        updated = claude_md.read_text(encoding="utf-8")
        assert "My Custom Section" in updated
        assert "This is user content." in updated
        # Protocol reachable via the carrier (inline or resolved @-import).
        assert "trw_session_start" in resolve_instruction_text(claude_md)

    def test_updates_trw_section(self, initialized_repo: Path) -> None:
        """TRW auto-generated section is updated."""
        claude_md = initialized_repo / "AGENTS.md"

        update_project(initialized_repo)

        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content
        assert "<!-- trw:end -->" in content
        assert "trw_session_start" in resolve_instruction_text(claude_md)

    def test_appends_trw_section_if_missing(self, initialized_repo: Path) -> None:
        """If AGENTS.md has no TRW markers, append the section."""
        claude_md = initialized_repo / "AGENTS.md"
        claude_md.write_text("# My Project\n\nNo TRW section here.\n", encoding="utf-8")

        update_project(initialized_repo)

        content = claude_md.read_text(encoding="utf-8")
        assert "# My Project" in content
        assert "<!-- trw:start -->" in content
        assert "trw_session_start" in resolve_instruction_text(claude_md)

    def test_creates_agents_md_if_missing(self, initialized_repo: Path) -> None:
        """If AGENTS.md doesn't exist, create it from template."""
        claude_md = initialized_repo / "AGENTS.md"
        claude_md.unlink()

        result = update_project(initialized_repo)
        assert not result["errors"]
        assert claude_md.exists()
        assert "trw_session_start" in resolve_instruction_text(claude_md)


@pytest.mark.unit
class TestUpdateResolvesAgentModelTier:
    """sub_5ctrrLJ: update-project must resolve agent ``model:`` tiers like init.

    The pre-fix update path raw-copied bundled agents, re-materializing the
    unresolvable ``model: frontier`` token so agent spawns failed after upgrades.
    """

    def test_update_resolves_frontier_to_opus(self, initialized_repo: Path) -> None:
        """After update, a bundled agent carries ``model: opus`` — never ``frontier``."""
        agent = initialized_repo / ".claude" / "agents" / "trw-lead.md"
        # Fresh install already resolves; prove update KEEPS it resolved.
        assert "model: opus" in agent.read_text(encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = agent.read_text(encoding="utf-8")
        assert "model: opus" in content
        assert "model: frontier" not in content

    def test_update_heals_agent_left_at_raw_tier(self, initialized_repo: Path) -> None:
        """A resolved-but-unmodified agent (raw framework form on disk) IS updated.

        Simulates a project a pre-fix update left at ``model: frontier`` while the
        manifest still records the resolved hash. The reconciled guard must treat
        the raw framework form as unmodified (not a user edit) and heal it to
        ``model: opus`` — the exact misclassification the fix prevents.
        """
        from trw_mcp.bootstrap import _DATA_DIR

        agent = initialized_repo / ".claude" / "agents" / "trw-lead.md"
        raw_bundled = (_DATA_DIR / "agents" / "trw-lead.md").read_text(encoding="utf-8")
        assert "model: frontier" in raw_bundled
        # Leave the agent in the broken raw-tier state a pre-fix update produced.
        agent.write_text(raw_bundled, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = agent.read_text(encoding="utf-8")
        assert "model: opus" in content
        assert "model: frontier" not in content
        # Must NOT be misclassified as a user modification.
        assert not any("trw-lead.md" in m for m in result.get("modified", []))

    def test_update_preserves_genuinely_user_edited_agent(self, initialized_repo: Path) -> None:
        """A genuinely user-edited agent is preserved and reported, not clobbered.

        Focused unit: exercises the guard directly via ``_update_framework_files``
        with explicit manifest hashes. The end-to-end proof that the LIVE
        ``update_project()`` path now threads these hashes (PRD-FIX-068-FR05) lives
        in :class:`TestUpdateLivePathPreservesUserEdits`.
        """
        from trw_mcp.bootstrap._template_updater import _update_framework_files
        from trw_mcp.bootstrap._version_manifest import _read_manifest

        agent = initialized_repo / ".claude" / "agents" / "trw-lead.md"
        edited = agent.read_text(encoding="utf-8") + "\n\n<!-- user note: do not overwrite -->\n"
        agent.write_text(edited, encoding="utf-8")

        manifest = _read_manifest(initialized_repo)
        assert isinstance(manifest, dict)
        manifest_hashes = manifest["content_hashes"]
        assert isinstance(manifest_hashes, dict)

        from trw_mcp.bootstrap import _DATA_DIR

        result: dict[str, list[str]] = {
            "updated": [],
            "created": [],
            "preserved": [],
            "errors": [],
            "modified": [],
        }
        _update_framework_files(initialized_repo, _DATA_DIR, result, manifest_hashes=manifest_hashes)

        # User edit survives untouched and is reported as modified.
        assert agent.read_text(encoding="utf-8") == edited
        assert any("trw-lead.md" in m for m in result["modified"])


@pytest.mark.unit
class TestUpdateLivePathPreservesUserEdits:
    """PRD-FIX-068-FR05 on the REAL update_project() path.

    Before the fix, ``_run_core_update_phases`` called ``_update_framework_files``
    with ``manifest_hashes=None`` (the manifest was only read later), so the
    user-modification guard was dead on the live path and user-edited agents were
    silently overwritten. These tests drive the full ``update_project()`` entry
    point to prove the prior manifest's content hashes are now threaded through.
    """

    def test_live_path_preserves_user_edited_agent(self, initialized_repo: Path) -> None:
        """A user-edited agent survives update_project() and is reported modified.

        A sibling un-edited agent is still updated (frontier->opus resolution
        intact), proving the guard protects only genuine user edits.
        """
        agents_dir = initialized_repo / ".claude" / "agents"
        edited_agent = agents_dir / "trw-implementer.md"
        # trw-lead is a frontier-tier sibling (resolves to model: opus like
        # trw-implementer); left un-edited it must still update.
        untouched_agent = agents_dir / "trw-lead.md"

        # Genuine user edit — a body change no framework rendering produces.
        original = edited_agent.read_text(encoding="utf-8")
        edited = original + "\n\n<!-- user note: do not overwrite -->\n"
        edited_agent.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        # User edit preserved byte-for-byte and reported in result["modified"].
        assert edited_agent.read_text(encoding="utf-8") == edited
        assert any("trw-implementer.md" in m for m in result.get("modified", []))

        # The un-edited frontier sibling still resolves to the client model.
        untouched = untouched_agent.read_text(encoding="utf-8")
        assert "model: opus" in untouched
        assert "model: frontier" not in untouched
        assert not any("trw-lead.md" in m for m in result.get("modified", []))

    def test_live_path_first_run_without_manifest_refuses(self, initialized_repo: Path) -> None:
        """No prior manifest: update_project() refuses before touching anything.

        Simulates the first update on a project installed before manifest support
        existed (``_read_manifest`` returns None). Superseded behavior: this used
        to assert the reconciled guard still distinguished a genuine user edit
        (preserved) from a raw pre-fix ``model: frontier`` file (healed) with no
        manifest at all. PRD-INFRA-192-NFR02 removes that fail-open degrade path —
        without a current-schema manifest TRW cannot tell which files it owns, so
        it refuses the whole update up front (byte-identical tree) instead of
        guessing per-file ownership.
        """
        from trw_mcp.bootstrap._utils import _DATA_DIR
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE, _read_manifest

        manifest_path = initialized_repo / ".trw" / _MANIFEST_FILE
        if manifest_path.exists():
            manifest_path.unlink()
        assert _read_manifest(initialized_repo) is None

        agents_dir = initialized_repo / ".claude" / "agents"

        # Agent A: genuine user edit — a body change no framework rendering produces.
        edited_agent = agents_dir / "trw-implementer.md"
        edited = edited_agent.read_text(encoding="utf-8") + "\n\n<!-- user note: keep me -->\n"
        edited_agent.write_text(edited, encoding="utf-8")

        # Agent B: raw framework tier a pre-fix update produced (frontier sibling).
        raw_agent = agents_dir / "trw-lead.md"
        raw_bundled = (_DATA_DIR / "agents" / "trw-lead.md").read_text(encoding="utf-8")
        assert "model: frontier" in raw_bundled
        raw_agent.write_text(raw_bundled, encoding="utf-8")

        before = {
            str(p.relative_to(initialized_repo)): p.read_bytes() for p in initialized_repo.rglob("*") if p.is_file()
        }

        result = update_project(initialized_repo)

        assert len(result["errors"]) == 1, result["errors"]
        assert "refusing to update" in result["errors"][0]
        assert "clean reinstall" in result["errors"][0]

        # Neither the genuine edit nor the raw framework file was touched — nothing was.
        assert edited_agent.read_text(encoding="utf-8") == edited
        assert raw_agent.read_text(encoding="utf-8") == raw_bundled
        after = {
            str(p.relative_to(initialized_repo)): p.read_bytes() for p in initialized_repo.rglob("*") if p.is_file()
        }
        assert after == before


def _agents_state(root: Path) -> tuple[bytes, int]:
    """AGENTS.md bytes and the count of its retained pre-write backups."""
    backups = root / TRWConfig().instruction_backup_dir
    copies = list(backups.glob("AGENTS.md.*")) if backups.is_dir() else []
    return (root / "AGENTS.md").read_bytes(), len(copies)


@pytest.mark.integration
class TestUpdateProjectIsIdempotent:
    """E2E-INC-015: an update that changes nothing writes nothing (no rewrite, no backup)."""

    @staticmethod
    def _add_user_footer(root: Path) -> None:
        agents = root / "AGENTS.md"
        footer = "\n## Footer\nuser text after the block\n"
        agents.write_text(agents.read_text(encoding="utf-8") + footer, encoding="utf-8")

    @pytest.mark.parametrize("second_client", [None, "codex"], ids=["claude-code", "claude-code+codex"])
    def test_repeated_updates_leave_agents_md_and_backups_unchanged(
        self, initialized_repo: Path, second_client: str | None
    ) -> None:
        if second_client is not None:
            init_project(initialized_repo, ide=second_client)
        self._add_user_footer(initialized_repo)
        update_project(initialized_repo)  # settles the layout; may legitimately back up once
        settled = _agents_state(initialized_repo)

        for _ in range(3):
            result = update_project(initialized_repo)
            assert not result["errors"]
            assert _agents_state(initialized_repo) == settled

    @pytest.mark.parametrize("blank_lines", [0, 1, 2, 5])
    def test_the_boundary_after_the_block_is_kept_byte_for_byte(self, initialized_repo: Path, blank_lines: int) -> None:
        agents = initialized_repo / "AGENTS.md"
        text = agents.read_text(encoding="utf-8").rstrip("\n")
        agents.write_text(text + "\n" + "\n" * blank_lines + "## Footer\ntext\n", encoding="utf-8")

        update_project(initialized_repo)
        settled = _agents_state(initialized_repo)
        update_project(initialized_repo)

        assert _agents_state(initialized_repo) == settled
        assert b"<!-- trw:end -->\n" + b"\n" * blank_lines + b"## Footer\ntext\n" in settled[0]


@pytest.mark.integration
class TestAgentsMdMergersAgree:
    """E2E-INC-015: the two AGENTS.md block mergers must produce identical bytes for identical input.

    ``generate_agents_md`` (bootstrap) and ``merge_trw_section`` (sync) each rewrote the block; a disagreement
    at the ``trw:end`` boundary made them take turns rewriting, and backing up, the same file.
    """

    @pytest.mark.parametrize("blank_lines", [0, 1, 2, 4])
    @pytest.mark.parametrize("terminal_newline", [True, False], ids=["eof-newline", "no-eof-newline"])
    def test_alternating_writers_settle_without_new_backups(
        self, tmp_path: Path, blank_lines: int, terminal_newline: bool
    ) -> None:
        from trw_mcp.bootstrap._opencode import generate_agents_md
        from trw_mcp.state.claude_md import merge_trw_section
        from trw_mcp.state.claude_md._instructions_link import agents_link_section

        block = agents_link_section()
        footer = "## Footer\nuser text" + ("\n" if terminal_newline else "")
        agents = tmp_path / "AGENTS.md"
        agents.write_text("# Rules\n\n" + block + "\n" * blank_lines + footer, encoding="utf-8")
        (tmp_path / ".trw").mkdir()
        generate_agents_md(tmp_path, client_id="claude-code")
        merge_trw_section(agents, block, None, project_root=tmp_path)  # settle: may normalize the boundary once

        for _ in range(3):
            settled = agents.read_bytes()
            generate_agents_md(tmp_path, client_id="claude-code")
            assert agents.read_bytes() == settled, "generate_agents_md rewrote settled bytes"
            verdict = merge_trw_section(agents, block, None, project_root=tmp_path)
            assert agents.read_bytes() == settled, "merge_trw_section rewrote settled bytes"
            assert verdict.backup_path is None
        assert settled.endswith(footer.encode())


@pytest.mark.integration
class TestReinstallAfterUninstallIsByteStable:
    """E2E-INC-015 (c): init -> uninstall -> init must not grow whitespace in a user's AGENTS.md."""

    def test_repeated_reinstall_settles_after_one_normalization(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._opencode import generate_agents_md
        from trw_mcp.state.claude_md._instructions_link import agents_link_section

        agents = tmp_path / "AGENTS.md"
        (tmp_path / ".trw").mkdir()
        user = "# Team rules\n\nAlways use tabs.\n"
        agents.write_text(user, encoding="utf-8")
        seen = []
        for _ in range(4):
            generate_agents_md(tmp_path, client_id="claude-code")
            seen.append(agents.read_bytes())
            # what uninstall leaves: the block and its header gone, the user's text and the writer's blank lines
            agents.write_text(user + "\n", encoding="utf-8")
        assert len(set(seen)) == 1
        assert seen[0].startswith(user.encode()) and agents_link_section().encode() in seen[0]


class TestUpdateCreatesNewArtifacts:
    """Test that update_project creates new artifacts from newer versions."""

    def test_creates_new_skill(self, initialized_repo: Path) -> None:
        """New skills in bundled data are deployed."""
        # All skills should exist after update
        result = update_project(initialized_repo)
        assert not result["errors"]

        skills_dir = initialized_repo / ".claude" / "skills"
        deployed = sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        # Should have all expected skills
        assert "trw-deliver" in deployed
        assert "trw-learn" in deployed
        assert "trw-project-health" in deployed

    def test_creates_new_agent(self, initialized_repo: Path) -> None:
        """New agents in bundled data are deployed."""
        result = update_project(initialized_repo)
        assert not result["errors"]

        agents_dir = initialized_repo / ".claude" / "agents"
        deployed = sorted(f.name for f in agents_dir.iterdir() if f.suffix == ".md")
        assert "trw-implementer.md" in deployed
        assert "trw-auditor.md" in deployed


@pytest.mark.unit
class TestUpdateWarningsAndVersionCheck:
    """Test update_project warnings, version check, and restart guidance."""

    def test_includes_restart_warning(self, initialized_repo: Path) -> None:
        """update_project always warns about restarting sessions."""
        result = update_project(initialized_repo)
        assert "warnings" in result
        assert any("Restart" in w for w in result["warnings"])

    def test_includes_version_check(self, initialized_repo: Path) -> None:
        """update_project checks installed package version."""
        result = update_project(initialized_repo)
        # Should have either a version match (preserved) or mismatch (warning)
        version_related = [p for p in result["preserved"] if "trw-mcp package" in p] + [
            w for w in result["warnings"] if "trw-mcp" in w and "differs" in w
        ]
        assert len(version_related) > 0

    def test_warnings_key_always_present(self, initialized_repo: Path) -> None:
        """update_project result always includes 'warnings' key."""
        result = update_project(initialized_repo)
        assert "warnings" in result
        assert isinstance(result["warnings"], list)


class TestUpdateVerboseHintIsRunnable:
    """G3 (installer refinement 5.1.0): the printed '-v' hint must actually work.

    Root cause: the hint printed after ``update-project`` finishes reads (the
    natural, wrong way) as "append -v to what you just ran" — but ``-v`` is
    registered on the TOP-LEVEL argparse parser only
    (``_cli_argparse.py``), not on the ``update-project`` subcommand, so
    ``update-project . -v`` failed with ``unrecognized arguments: -v``
    (reproduced live). The fix corrects the hint text to put ``-v`` BEFORE
    the subcommand; this test runs that exact printed command through the
    real CLI entry point.
    """

    def test_the_printed_hint_command_runs_and_exits_zero(self, initialized_repo: Path) -> None:
        from trw_mcp.server._cli import main

        with (
            patch("sys.argv", ["trw-mcp", "-v", "update-project", str(initialized_repo)]),
            pytest.raises(SystemExit) as exc,
        ):
            main()
        assert exc.value.code == 0

    def test_the_old_hint_shape_still_fails_to_document_why_it_moved(self, initialized_repo: Path) -> None:
        """Negative control proving this is a real fix, not a no-op rewording:
        the OLD (pre-fix) hint shape — ``-v`` AFTER the subcommand — still
        does not parse, which is exactly why the hint text had to move ``-v``
        before ``update-project`` rather than merely rephrase the same
        command."""
        from trw_mcp.server._cli import main

        with (
            patch("sys.argv", ["trw-mcp", "update-project", str(initialized_repo), "-v"]),
            pytest.raises(SystemExit) as exc,
        ):
            main()
        assert exc.value.code != 0


class TestRootFrameworkMd:
    """Test that init/update deploy FRAMEWORK.md to the project root."""

    def test_init_creates_root_framework_md(self, fake_git_repo: Path) -> None:
        """init_project creates FRAMEWORK.md at the project root."""
        result = init_project(fake_git_repo)
        assert not result["errors"]

        root_fw = fake_git_repo / "FRAMEWORK.md"
        assert root_fw.is_file()
        content = root_fw.read_text(encoding="utf-8")
        assert TRWConfig().framework_version in content

    def test_init_root_matches_cached(self, fake_git_repo: Path) -> None:
        """Root FRAMEWORK.md matches .trw/frameworks/FRAMEWORK.md after init."""
        init_project(fake_git_repo)

        root_fw = fake_git_repo / "FRAMEWORK.md"
        cached_fw = fake_git_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert root_fw.read_text(encoding="utf-8") == cached_fw.read_text(encoding="utf-8")

    def test_update_overwrites_stale_root_framework_md(self, initialized_repo: Path) -> None:
        """update_project overwrites a stale root FRAMEWORK.md."""
        root_fw = initialized_repo / "FRAMEWORK.md"
        root_fw.write_text("old stale content v16.0", encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        content = root_fw.read_text(encoding="utf-8")
        assert content != "old stale content v16.0"
        assert TRWConfig().framework_version in content

    def test_update_root_matches_cached(self, initialized_repo: Path) -> None:
        """After update, root FRAMEWORK.md matches cached version."""
        update_project(initialized_repo)

        root_fw = initialized_repo / "FRAMEWORK.md"
        cached_fw = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        assert root_fw.read_text(encoding="utf-8") == cached_fw.read_text(encoding="utf-8")


@pytest.mark.unit
class TestUpdatePreservesUserEditedHooksAndSkills:
    """Codex HIGH round-2 audit: PRD-FIX-068-FR05 covers hooks + skills, not only agents.

    The manifest already records hook/skill content hashes (``_compute_content_hashes``),
    but the pre-fix ``_update_hooks`` / ``_update_skills`` raw-copied unconditionally,
    clobbering user-edited hooks/skills. The guard is now threaded through both.
    """

    def test_live_path_preserves_user_edited_hook(self, initialized_repo: Path) -> None:
        """A user-edited hook survives update_project() and is reported modified."""
        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        edited = hook.read_text(encoding="utf-8") + "\n# user custom line — keep me\n"
        hook.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert hook.read_text(encoding="utf-8") == edited
        assert any("session-start.sh" in m for m in result.get("modified", []))

    def test_live_path_preserves_user_edited_skill(self, initialized_repo: Path) -> None:
        """A user-edited skill SKILL.md survives update_project() and is reported."""
        skill = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
        edited = skill.read_text(encoding="utf-8") + "\n<!-- user note: keep me -->\n"
        skill.write_text(edited, encoding="utf-8")

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert skill.read_text(encoding="utf-8") == edited
        assert any("trw-deliver" in m and "SKILL.md" in m for m in result.get("modified", []))

    def test_unedited_hook_still_updates(self, initialized_repo: Path) -> None:
        """An un-edited hook (on-disk hash matches manifest) is still updated.

        Focused: the recorded manifest hash matches the (stale) on-disk content,
        so the guard reports NOT-modified and the newer bundled content overwrites.
        """
        import hashlib

        from trw_mcp.bootstrap._template_updater import _update_hooks
        from trw_mcp.bootstrap._utils import _DATA_DIR

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        stale = "#!/bin/bash\n# stale bundled version\n"
        hook.write_text(stale, encoding="utf-8")
        stale_hash = hashlib.sha256(stale.encode("utf-8")).hexdigest()

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(
            initialized_repo,
            _DATA_DIR,
            result,
            manifest_hashes={"session-start.sh": stale_hash},
        )

        assert hook.read_text(encoding="utf-8") != stale
        assert not any("session-start.sh" in m for m in result["modified"])

    def test_unedited_skill_still_updates(self, initialized_repo: Path) -> None:
        """An un-edited skill SKILL.md (hash matches manifest) is still updated."""
        import hashlib

        from trw_mcp.bootstrap._template_updater import _update_skills
        from trw_mcp.bootstrap._utils import _DATA_DIR

        skill = initialized_repo / ".claude" / "skills" / "trw-deliver" / "SKILL.md"
        stale = "# stale skill body\n"
        skill.write_text(stale, encoding="utf-8")
        stale_hash = hashlib.sha256(stale.encode("utf-8")).hexdigest()

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_skills(
            initialized_repo,
            _DATA_DIR,
            result,
            manifest_hashes={"trw-deliver/SKILL.md": stale_hash},
        )

        assert skill.read_text(encoding="utf-8") != stale
        assert not any("trw-deliver/SKILL.md" in m for m in result["modified"])


@pytest.mark.unit
class TestUpdatePreservesUserEditsWithoutManifest:
    """Round-3 audit: hooks/skills had NO framework baseline, so a missing/corrupt/
    pre-hash manifest (``manifest_hashes is None``) silently clobbered user edits.

    Only agents carried a framework-content baseline (``_framework_agent_hashes``).
    The fix derives a bundled-content baseline for hooks/skills too and threads it
    through ``_guarded_copy_update`` so preservation is decidable without a manifest
    and fails toward preservation on divergence.
    """

    def test_corrupt_manifest_refuses_before_touching_user_edited_hook(self, initialized_repo: Path) -> None:
        """PRD-INFRA-192-NFR02: a corrupt manifest refuses the whole update, so a
        user-edited hook is untouched not because the guard "preserved" it but
        because update_project() never wrote anything at all.

        Superseded behavior: this used to assert the corrupt manifest degraded
        ``_read_manifest`` to None and the live path still preserved the edit via
        the framework-content baseline. That fail-open degrade path is removed —
        a corrupt manifest now refuses up front (byte-identical tree), rather than
        proceeding with a best-effort guess at ownership.
        """
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE

        (initialized_repo / ".trw" / _MANIFEST_FILE).write_text("{ unclosed: [1, 2", encoding="utf-8")

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        edited = hook.read_text(encoding="utf-8") + "\n# user custom line — keep me\n"
        hook.write_text(edited, encoding="utf-8")

        before = {
            str(p.relative_to(initialized_repo)): p.read_bytes() for p in initialized_repo.rglob("*") if p.is_file()
        }

        result = update_project(initialized_repo)

        assert len(result["errors"]) == 1, result["errors"]
        assert "refusing to update" in result["errors"][0]
        assert "clean reinstall" in result["errors"][0]
        assert hook.read_text(encoding="utf-8") == edited

        after = {
            str(p.relative_to(initialized_repo)): p.read_bytes() for p in initialized_repo.rglob("*") if p.is_file()
        }
        assert after == before

    def test_no_manifest_preserves_user_edited_hook_unit(self, initialized_repo: Path) -> None:
        """Focused: ``_update_hooks`` with ``manifest_hashes=None`` preserves a diverged hook."""
        from trw_mcp.bootstrap._template_updater import _update_hooks
        from trw_mcp.bootstrap._utils import _DATA_DIR

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        edited = (_DATA_DIR / "hooks" / "session-start.sh").read_text(encoding="utf-8") + "\n# diverged\n"
        hook.write_text(edited, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes=None)

        assert hook.read_text(encoding="utf-8") == edited
        assert any("session-start.sh" in m for m in result["modified"])

    def test_no_manifest_pristine_hook_still_updates(self, initialized_repo: Path) -> None:
        """A hook matching the shipped bundle is framework-managed, so it still updates.

        Guards against the fix over-preserving: fail-toward-preservation must only
        trigger on genuine divergence, not on a pristine framework file.
        """
        from trw_mcp.bootstrap._template_updater import _update_hooks
        from trw_mcp.bootstrap._utils import _DATA_DIR

        hook = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        shipped = (_DATA_DIR / "hooks" / "session-start.sh").read_text(encoding="utf-8")
        hook.write_text(shipped, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": [], "modified": []}
        _update_hooks(initialized_repo, _DATA_DIR, result, manifest_hashes=None)

        assert not any("session-start.sh" in m for m in result["modified"])


@pytest.mark.unit
class TestReadManifestCorruptDegrades:
    """P2-3 round-2 audit: a malformed managed-artifacts.yaml must not crash update."""

    def test_read_manifest_malformed_yaml_returns_none(self, initialized_repo: Path) -> None:
        """_read_manifest degrades to None on StateError (malformed YAML), not raises."""
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE, _read_manifest

        manifest_path = initialized_repo / ".trw" / _MANIFEST_FILE
        # Unbalanced flow mapping — a hard YAML parse error → FileStateReader raises StateError.
        manifest_path.write_text("{ unclosed: [1, 2, 3", encoding="utf-8")

        assert _read_manifest(initialized_repo) is None

    def test_update_project_refuses_on_corrupt_manifest(self, initialized_repo: Path) -> None:
        """PRD-INFRA-192-NFR02: update_project() refuses (not "survives") a corrupt
        manifest. Superseded behavior: this used to assert the update completed
        with no errors despite the corrupt manifest; that fail-open path is removed
        — a corrupt manifest now produces exactly one refusal error naming the
        clean-reinstall remedy, with no artifacts modified."""
        from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE

        manifest_path = initialized_repo / ".trw" / _MANIFEST_FILE
        manifest_path.write_text("{ unclosed: [1, 2, 3", encoding="utf-8")

        before = {
            str(p.relative_to(initialized_repo)): p.read_bytes() for p in initialized_repo.rglob("*") if p.is_file()
        }

        result = update_project(initialized_repo)

        assert len(result["errors"]) == 1, result["errors"]
        assert "refusing to update" in result["errors"][0]
        assert "clean reinstall" in result["errors"][0]
        after = {
            str(p.relative_to(initialized_repo)): p.read_bytes() for p in initialized_repo.rglob("*") if p.is_file()
        }
        assert after == before


@pytest.mark.unit
class TestLivePathExistingAgentRelabel:
    """P2-4 round-2 audit: an existing agent on the live path is 'updated', not 'created'."""

    def test_existing_agent_reported_updated_not_created(self, initialized_repo: Path) -> None:
        """A pre-existing agent the update rewrites is reported ``updated``, never ``created``."""
        agent = initialized_repo / ".claude" / "agents" / "trw-implementer.md"
        from trw_mcp.bootstrap import _DATA_DIR

        # The raw bundled form is a framework rendering, so the update heals it.
        agent.write_bytes((_DATA_DIR / "agents" / "trw-implementer.md").read_bytes())

        result = update_project(initialized_repo)
        assert not result["errors"]

        assert ".claude/agents/trw-implementer.md" in result["updated"]
        assert ".claude/agents/trw-implementer.md" not in result["created"]


def test_only_file_scoped_marker_refusals_are_demoted_from_errors() -> None:
    from trw_mcp.state.claude_md._marker_layout import demote_file_scoped_refusals, refusal_message

    result = {
        "errors": [
            refusal_message("Refused to write /p/AGENTS.md (ambiguous_markers): left as found", "ambiguous_markers"),
            refusal_message(
                "AGENTS.md left untouched: its TRW markers are duplicated or unbalanced, so TRW cannot tell",
                "ambiguous_markers",
            ),
            refusal_message("Refused to write /p/CLAUDE.md (shrink_floor): would drop 40 lines", "shrink_floor"),
            "update-project failed: OSError: disk full",
        ],
        "warnings": [],
    }

    assert demote_file_scoped_refusals(result) == 2
    assert result["errors"] == [
        "Refused to write /p/CLAUDE.md (shrink_floor): would drop 40 lines",
        "update-project failed: OSError: disk full",
    ], "every other error still rolls the update back"
    assert len(result["warnings"]) == 2
