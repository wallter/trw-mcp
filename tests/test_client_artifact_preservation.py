"""User-edit preservation for the copilot / cursor / antigravity-cli mirrors.

CONSTITUTION HB-2: TRW never destroys uncommitted user work without explicit
authorization. Three of the seven supported client profiles used to overwrite
hand-edited files on every ``update-project``:

- ``.github/skills/**`` — both branches of ``if existed and not force`` issued
  the same ``shutil.copy2``;
- ``.cursor/skills/**`` — unconditional ``copytree(..., dirs_exist_ok=True)``;
- ``.cursor/agents``/``.cursor/commands`` — documented as "always written".

and the other three generators had the mirror-image bug: ``existed and not
force`` froze TRW-owned files at their first-installed content forever.

Every test here therefore asserts in BOTH directions — a user edit is preserved
AND an untouched TRW-owned file still refreshes — because a fix that merely
freezes files passes the first and silently breaks the second.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ._bootstrap_test_support import patch_update_project_internals


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# The shared predicate
# ---------------------------------------------------------------------------


class TestArtifactUserEdited:
    """``artifact_user_edited`` — the one guard all three profiles now share."""

    def test_absent_file_is_not_an_edit(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited

        assert artifact_user_edited(tmp_path / "nope.md", "nope.md", b"bundled", None) is False

    def test_matches_incoming_bundle_is_not_an_edit(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited

        dest = tmp_path / "a.md"
        dest.write_text("bundled", encoding="utf-8")
        assert artifact_user_edited(dest, "a.md", b"bundled", None) is False

    def test_matches_recorded_manifest_hash_is_not_an_edit(self, tmp_path: Path) -> None:
        """Stale-but-TRW-written content stays refreshable — the update direction."""
        from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited

        dest = tmp_path / "a.md"
        dest.write_text("old bundled", encoding="utf-8")
        hashes = {"a.md": _sha("old bundled")}
        assert artifact_user_edited(dest, "a.md", b"new bundled", hashes) is False

    def test_diverged_with_no_manifest_record_is_an_edit(self, tmp_path: Path) -> None:
        """The permissive-fallback fix: "cannot verify" must not mean "overwrite".

        A first-run, corrupt, or pre-content-hash ``managed-artifacts.yaml``
        yields ``manifest_hashes=None``. The bundled bytes are still a usable
        baseline, so divergence is decidable and the file is preserved.
        """
        from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited

        dest = tmp_path / "a.md"
        dest.write_text("my edit", encoding="utf-8")
        assert artifact_user_edited(dest, "a.md", b"bundled", None) is True

    def test_diverged_from_manifest_record_is_an_edit(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._managed_client_artifacts import artifact_user_edited

        dest = tmp_path / "a.md"
        dest.write_text("my edit", encoding="utf-8")
        hashes = {"a.md": _sha("old bundled")}
        assert artifact_user_edited(dest, "a.md", b"new bundled", hashes) is True


# ---------------------------------------------------------------------------
# Per-generator, both directions
# ---------------------------------------------------------------------------


class TestCursorIdeAgentsAndCommands:
    """``.cursor/agents`` and ``.cursor/commands`` were unconditional writes."""

    def test_agents_preserve_user_edit(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_subagents

        generate_cursor_ide_subagents(tmp_path)
        edited = tmp_path / ".cursor" / "agents" / "trw-implementer.md"
        assert edited.is_file()
        edited.write_text("# my agent\n", encoding="utf-8")

        result = generate_cursor_ide_subagents(tmp_path)

        assert edited.read_text(encoding="utf-8") == "# my agent\n"
        assert ".cursor/agents/trw-implementer.md" in result["preserved"]

    def test_agents_refresh_untouched_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_ide import cursor_ide_agent_contents, generate_cursor_ide_subagents

        rel = ".cursor/agents/trw-implementer.md"
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True)
        dest.write_text("# older bundled agent\n", encoding="utf-8")

        result = generate_cursor_ide_subagents(tmp_path, manifest_hashes={rel: _sha("# older bundled agent\n")})

        assert dest.read_bytes() == cursor_ide_agent_contents()[rel]
        assert rel in result["updated"]

    def test_commands_preserve_user_edit(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_ide import _TRW_COMMANDS, generate_cursor_ide_commands

        generate_cursor_ide_commands(tmp_path)
        name = _TRW_COMMANDS[0][0]
        edited = tmp_path / ".cursor" / "commands" / f"{name}.md"
        edited.write_text("# my command\n", encoding="utf-8")

        result = generate_cursor_ide_commands(tmp_path)

        assert edited.read_text(encoding="utf-8") == "# my command\n"
        assert f".cursor/commands/{name}.md" in result["preserved"]

    def test_commands_refresh_untouched_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_ide import (
            _TRW_COMMANDS,
            cursor_ide_command_contents,
            generate_cursor_ide_commands,
        )

        name = _TRW_COMMANDS[0][0]
        rel = f".cursor/commands/{name}.md"
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True)
        dest.write_text("# older bundled command\n", encoding="utf-8")

        result = generate_cursor_ide_commands(tmp_path, manifest_hashes={rel: _sha("# older bundled command\n")})

        assert dest.read_bytes() == cursor_ide_command_contents()[rel]
        assert rel in result["updated"]


class TestAntigravityAgents:
    """``.antigravitycli/agents`` was frozen, not destructive — the other direction."""

    def test_preserve_user_edit(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._antigravity_cli import generate_antigravity_agents

        generate_antigravity_agents(tmp_path)
        edited = tmp_path / ".antigravitycli" / "agents" / "trw-explorer.md"
        edited.write_text("# my agent\n", encoding="utf-8")

        result = generate_antigravity_agents(tmp_path)

        assert edited.read_text(encoding="utf-8") == "# my agent\n"
        assert ".antigravitycli/agents/trw-explorer.md" in result["preserved"]

    def test_refresh_untouched_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._antigravity_cli import (
            antigravity_agent_contents,
            generate_antigravity_agents,
        )

        rel = ".antigravitycli/agents/trw-explorer.md"
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True)
        dest.write_text("# older bundled agent\n", encoding="utf-8")

        result = generate_antigravity_agents(tmp_path, manifest_hashes={rel: _sha("# older bundled agent\n")})

        assert dest.read_bytes() == antigravity_agent_contents()[rel]
        assert rel in result["updated"]


class TestCopilotPathInstructions:
    def test_refresh_untouched_file(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._copilot import (
            _PATH_SCOPED_TEMPLATES,
            copilot_path_instruction_contents,
            generate_copilot_path_instructions,
        )

        name = next(iter(_PATH_SCOPED_TEMPLATES))
        rel = f".github/instructions/{name}"
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True)
        dest.write_text("# older bundled instructions\n", encoding="utf-8")

        result = generate_copilot_path_instructions(
            tmp_path, manifest_hashes={rel: _sha("# older bundled instructions\n")}
        )

        assert dest.read_bytes() == copilot_path_instruction_contents()[rel]
        assert rel in result["updated"]


# ---------------------------------------------------------------------------
# The manifest baseline — the property that makes preservation DURABLE
# ---------------------------------------------------------------------------


class TestManagedClientManifestHashes:
    def test_records_hash_for_a_trw_owned_artifact(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._antigravity_cli import (
            antigravity_agent_contents,
            generate_antigravity_agents,
        )
        from trw_mcp.bootstrap._managed_client_artifacts import managed_client_manifest_hashes

        generate_antigravity_agents(tmp_path)
        rel = ".antigravitycli/agents/trw-explorer.md"

        hashes = managed_client_manifest_hashes(tmp_path, None)

        assert hashes[rel] == hashlib.sha256(antigravity_agent_contents()[rel]).hexdigest()

    def test_declines_to_record_a_user_edited_artifact(self, tmp_path: Path) -> None:
        """Non-laundering: recording the user's hash would make TRW claim it.

        The recorded hash IS the "TRW wrote this" baseline, so a laundered entry
        makes the guard answer "not modified" on the NEXT update and overwrite —
        i.e. preservation that only survives one run.
        """
        from trw_mcp.bootstrap._antigravity_cli import generate_antigravity_agents
        from trw_mcp.bootstrap._managed_client_artifacts import managed_client_manifest_hashes

        generate_antigravity_agents(tmp_path)
        rel = ".antigravitycli/agents/trw-explorer.md"
        (tmp_path / rel).write_text("# my agent\n", encoding="utf-8")

        hashes = managed_client_manifest_hashes(tmp_path, None)

        assert rel not in hashes

    def test_absent_artifact_is_not_recorded(self, tmp_path: Path) -> None:
        """A project that never installed a client contributes no keys."""
        from trw_mcp.bootstrap._managed_client_artifacts import managed_client_manifest_hashes

        assert managed_client_manifest_hashes(tmp_path, None) == {}

    def test_every_source_key_is_repo_relative(self) -> None:
        """Guard keys and manifest keys must be the same namespace.

        A drift here reintroduces P9 ("matcher that cannot see its own
        producer"): the sweep would record paths the generators never ask about,
        so every guard lookup would miss and report the reassuring answer.
        """
        from trw_mcp.bootstrap._managed_client_artifacts import MANAGED_CLIENT_ARTIFACT_SOURCES

        assert MANAGED_CLIENT_ARTIFACT_SOURCES
        for source in MANAGED_CLIENT_ARTIFACT_SOURCES:
            contents = source.contents()
            assert contents, f"{source.surface} produced no bundled content"
            for key in contents:
                assert key.startswith(f"{source.surface}/"), (source.surface, key)
                assert not Path(key).is_absolute()


# ---------------------------------------------------------------------------
# End to end: init-project -> hand edit -> update-project (twice)
# ---------------------------------------------------------------------------


_E2E_EDITS: tuple[tuple[str, str], ...] = (
    ("copilot", ".github/skills/trw-deliver/SKILL.md"),
    ("copilot", ".github/agents/trw-implementer.agent.md"),
    ("cursor-ide", ".cursor/agents/trw-implementer.md"),
    ("cursor-ide", ".cursor/skills/trw-deliver/SKILL.md"),
    ("antigravity-cli", ".antigravitycli/agents/trw-explorer.md"),
)


@pytest.fixture()
def multi_client_project(tmp_path: Path) -> Path:
    """A git repo initialized for copilot + cursor-ide + antigravity-cli."""
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    for marker in (".github", ".cursor", ".antigravitycli"):
        (tmp_path / marker).mkdir()

    result = init_project(tmp_path, ide="all")
    assert not result["errors"], result["errors"]
    return tmp_path


@pytest.mark.integration
def test_update_project_preserves_hand_edits_across_two_updates(multi_client_project: Path) -> None:
    """The reported defect, end to end, on the real update path.

    Two updates, not one: the first proves the guard fires, the second proves the
    manifest written by the first did not launder the user's content into TRW's
    baseline (which would destroy the edit on the following run).
    """
    from trw_mcp.bootstrap import update_project

    edited: dict[str, str] = {}
    for client, rel in _E2E_EDITS:
        path = multi_client_project / rel
        assert path.is_file(), f"{client}: {rel} was not installed by init-project"
        edited[rel] = f"# hand-edited by the user: {rel}\n"
        path.write_text(edited[rel], encoding="utf-8")

    for run in (1, 2):
        with patch_update_project_internals():
            result = update_project(multi_client_project, ide="all")
        assert not result["errors"], result["errors"]
        for rel, content in edited.items():
            assert (multi_client_project / rel).read_text(encoding="utf-8") == content, (
                f"{rel} was destroyed on update #{run}"
            )


@pytest.mark.integration
def test_update_project_still_refreshes_untouched_artifacts(
    multi_client_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction on the real update path.

    A guard that simply froze every mirrored file would pass the preservation
    test above and silently stop delivering upstream fixes. Here the bundled
    template changes between updates and the untouched on-disk artifact must
    pick the new content up.
    """
    from trw_mcp.bootstrap import _antigravity_cli, update_project

    rel = ".antigravitycli/agents/trw-explorer.md"
    dest = multi_client_project / rel
    assert dest.is_file()
    installed = dest.read_text(encoding="utf-8")

    # Seed the manifest baseline with the currently-installed content.
    with patch_update_project_internals():
        update_project(multi_client_project, ide="all")
    assert dest.read_text(encoding="utf-8") == installed

    new_template = "---\nname: trw-explorer\n---\n\n# upstream fix\n"
    patched = dict(_antigravity_cli._ANTIGRAVITY_AGENT_TEMPLATES)
    patched["trw-explorer.md"] = new_template
    monkeypatch.setattr(_antigravity_cli, "_ANTIGRAVITY_AGENT_TEMPLATES", patched)

    with patch_update_project_internals():
        result = update_project(multi_client_project, ide="all")

    assert not result["errors"], result["errors"]
    assert dest.read_text(encoding="utf-8") == new_template
