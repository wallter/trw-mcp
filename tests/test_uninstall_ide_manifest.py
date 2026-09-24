"""PRD-INFRA-192 FR09: manifest-driven per-client uninstall.

``uninstall --ide X`` used to delete every plain catalog directory X declared
wholesale with ``shutil.rmtree`` -- including a user's own custom file dropped
into that directory, or a TRW file the user had hand-edited. These tests prove
the manifest-driven replacement: a recorded file is deleted only when no
remaining recorded client still owns it and its bytes are unedited; everything
else survives.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._subcommands import _run_uninstall


def _ns(tmp_path: Path, **overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "target_dir": str(tmp_path),
        "dry_run": False,
        "yes": True,
        "user_tier": False,
        "keep_memory": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _claude_only_project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"], result["errors"]
    return tmp_path


def _read_manifest(project: Path) -> dict[str, object]:
    return yaml.safe_load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))


@pytest.mark.integration
class TestManifestDrivenRemovalPreservesUnowned:
    def test_users_own_skill_survives_recorded_ones_are_removed(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        mine = project / ".claude" / "skills" / "my-skill"
        mine.mkdir(parents=True)
        (mine / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")
        recorded_skill_dirs = [
            d for d in (project / ".claude" / "skills").iterdir() if d.is_dir() and d.name != "my-skill"
        ]
        assert recorded_skill_dirs, "precondition: at least one TRW-recorded skill installed"

        _run_uninstall(_ns(project, ide="claude-code"))

        assert (mine / "SKILL.md").is_file(), "a user's own custom skill must never be touched"
        for d in recorded_skill_dirs:
            assert not d.exists(), f"{d} is TRW's recorded skill and must be removed"

    def test_user_files_in_skills_agents_and_hooks_survive_byte_identical(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        claude = project / ".claude"
        mine = {
            claude / "skills" / "my-skill" / "SKILL.md": b"# my own skill\n",
            claude / "agents" / "my-agent.md": b"# my own agent\n",
            claude / "hooks" / "my-hook.sh": b"#!/bin/sh\necho mine\n",
        }
        trw_files = [p for sub in ("skills", "agents", "hooks") for p in (claude / sub).rglob("*") if p.is_file()]
        assert trw_files, "precondition: TRW installed files under skills/, agents/ and hooks/"
        for path, content in mine.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        _run_uninstall(_ns(project, ide="claude-code"))

        for path, content in mine.items():
            assert path.read_bytes() == content, f"{path} is the user's and must survive byte-identical"
        assert [p for p in trw_files if p.exists()] == []

    def test_a_users_file_inside_a_trw_skill_survives(self, tmp_path: Path) -> None:
        """Only ``SKILL.md`` is hashed per skill, so the directory is not rmtree'd:
        a file the user added next to it is not TRW's bundled content and stays."""
        project = _claude_only_project(tmp_path)
        skill = next(d for d in sorted((project / ".claude" / "skills").iterdir()) if d.is_dir())
        (skill / "my-notes.md").write_text("mine\n", encoding="utf-8")

        _run_uninstall(_ns(project, ide="claude-code"))

        assert (skill / "my-notes.md").read_text(encoding="utf-8") == "mine\n"
        assert not (skill / "SKILL.md").exists()

    def test_user_edited_skill_survives_and_is_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project = _claude_only_project(tmp_path)
        skill_dir = next(d for d in (project / ".claude" / "skills").iterdir() if d.is_dir())
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(skill_md.read_text(encoding="utf-8") + "\n<!-- my edit -->\n", encoding="utf-8")

        _run_uninstall(_ns(project, ide="claude-code"))

        assert skill_md.is_file(), "an edited TRW file must be preserved, not deleted"
        assert "Preserved (edited)" in capsys.readouterr().out

    def test_user_edited_agent_survives(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        agent = project / ".claude" / "agents" / "trw-implementer.md"
        agent.write_text(agent.read_text(encoding="utf-8") + "\n<!-- my note -->\n", encoding="utf-8")

        _run_uninstall(_ns(project, ide="claude-code"))

        assert agent.is_file()

    def test_every_agent_edited_keeps_the_whole_directory(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR09 C3 review follow-up: when EVERY file under a
        recorded directory has been hand-edited, the recorder omits all of
        them (``artifact_user_edited`` declines each one) so
        ``content_hashes`` has NO key left under ``.claude/agents`` at all --
        the exact condition ``manifest_covers_surface`` reads as "uncovered".
        Rule 3 must still keep the directory whole rather than falling back to
        the pre-C3 wholesale ``rmtree``."""
        project = _claude_only_project(tmp_path)
        agents_dir = project / ".claude" / "agents"
        agent_files = sorted(agents_dir.glob("*.md"))
        assert agent_files, "precondition: claude-code installed agent files"
        edited = {p: p.read_text(encoding="utf-8") + "\n<!-- my edit -->\n" for p in agent_files}
        for p, content in edited.items():
            p.write_text(content, encoding="utf-8")

        from trw_mcp.bootstrap import update_project

        assert not update_project(project, ide="claude-code")["errors"]
        manifest = _read_manifest(project)
        edited_names = {p.name for p in edited}
        assert not (edited_names & set(manifest["content_hashes"])), (
            "precondition: the recorder dropped every edited agent's key"
        )

        _run_uninstall(_ns(project, ide="claude-code"))

        for p, content in edited.items():
            assert p.read_text(encoding="utf-8") == content, f"{p} was hand-edited and must survive byte-identical"
        assert agents_dir.is_dir()


@pytest.mark.integration
class TestManifestDrivenSharedOwnership:
    def _two_client_project(self, tmp_path: Path) -> Path:
        (tmp_path / ".git").mkdir()
        assert not init_project(tmp_path, ide="claude-code")["errors"]
        assert not update_project(tmp_path, ide="codex")["errors"]
        return tmp_path

    def test_removing_codex_keeps_every_claude_hooks_file_and_updates_owners(self, tmp_path: Path) -> None:
        project = self._two_client_project(tmp_path)
        hooks_dir = project / ".claude" / "hooks"
        hook_files_before = sorted(p.name for p in hooks_dir.glob("*.sh"))
        assert hook_files_before, "precondition: hooks installed"
        manifest_before = _read_manifest(project)
        hook_key = next(k for k in manifest_before["owners"] if (hooks_dir / k).is_file())
        assert sorted(manifest_before["owners"][hook_key]) == ["claude-code", "codex"]

        _run_uninstall(_ns(project, ide="codex"))

        assert sorted(p.name for p in hooks_dir.glob("*.sh")) == hook_files_before, (
            "every .claude/hooks file must survive: claude-code still owns it"
        )
        manifest_after = _read_manifest(project)
        assert manifest_after["owners"][hook_key] == ["claude-code"], (
            "codex must be dropped from the owners list even though the file was kept"
        )


@pytest.mark.integration
class TestManifestDrivenSafety:
    def test_traversal_key_is_rejected_not_deleted(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-outside-traversal.txt"
        outside.write_text("do not delete me", encoding="utf-8")
        try:
            manifest = _read_manifest(project)
            digest = hashlib.sha256(outside.read_bytes()).hexdigest()
            manifest["content_hashes"][".claude/skills/../../%s" % outside.name] = digest
            manifest["owners"][".claude/skills/../../%s" % outside.name] = ["claude-code"]
            (project / ".trw" / "managed-artifacts.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

            with pytest.raises(SystemExit) as exc_info:
                _run_uninstall(_ns(project, ide="claude-code"))
            assert exc_info.value.code == 1
            assert outside.is_file(), "a path that escapes the project must never be deleted"
        finally:
            outside.unlink(missing_ok=True)

    def test_symlink_escape_is_rejected_not_deleted(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        outside = tmp_path.parent / f"{tmp_path.name}-outside-symlink.txt"
        outside.write_text("do not delete me either", encoding="utf-8")
        try:
            link = project / ".claude" / "hooks" / "evil-link.sh"
            link.symlink_to(outside)
            manifest = _read_manifest(project)
            digest = hashlib.sha256(outside.read_bytes()).hexdigest()
            manifest["content_hashes"]["evil-link.sh"] = digest
            manifest["owners"]["evil-link.sh"] = ["claude-code"]
            (project / ".trw" / "managed-artifacts.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

            with pytest.raises(SystemExit) as exc_info:
                _run_uninstall(_ns(project, ide="claude-code"))
            assert exc_info.value.code == 1
            assert outside.is_file(), "the symlink target outside the project must never be deleted"
        finally:
            outside.unlink(missing_ok=True)


@pytest.mark.integration
class TestManifestDrivenDryRunAndIdempotence:
    def test_dry_run_lists_exact_dispositions_and_changes_nothing(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}

        capsys_check = _run_uninstall(_ns(project, ide="claude-code", dry_run=True))
        assert capsys_check is None

        after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
        assert before == after, "dry-run must not change a single byte on disk"

    def test_dry_run_output_names_a_recorded_agent_for_removal(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        project = _claude_only_project(tmp_path)

        _run_uninstall(_ns(project, ide="claude-code", dry_run=True))

        out = capsys.readouterr().out
        assert "remove" in out
        assert ".claude/agents/trw-implementer.md" in out

    def test_second_removal_is_idempotent(self, tmp_path: Path) -> None:
        project = _claude_only_project(tmp_path)
        _run_uninstall(_ns(project, ide="claude-code"))
        assert not (project / ".claude" / "agents").exists()

        _run_uninstall(_ns(project, ide="claude-code"))  # must not raise, nothing left to do

        assert not (project / ".claude" / "agents").exists()


@pytest.mark.integration
class TestManifestDrivenWriteFailureRetry:
    def test_unlink_failure_reports_error_and_a_retry_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project = _claude_only_project(tmp_path)
        agent = project / ".claude" / "agents" / "trw-implementer.md"
        assert agent.is_file()

        original_unlink = Path.unlink
        state = {"raised": False}

        def flaky_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self.name == "trw-implementer.md" and not state["raised"]:
                state["raised"] = True
                raise PermissionError("denied")
            original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        with pytest.raises(SystemExit) as exc_info:
            _run_uninstall(_ns(project, ide="claude-code"))
        assert exc_info.value.code == 1
        assert agent.is_file(), "a failed unlink must leave the file in place"
        manifest = _read_manifest(project)
        assert "trw-implementer.md" in manifest["content_hashes"], (
            "the manifest must still list the key whose deletion failed"
        )

        monkeypatch.setattr(Path, "unlink", original_unlink)
        _run_uninstall(_ns(project, ide="claude-code"))  # retry succeeds

        assert not agent.is_file()
        manifest_after = _read_manifest(project)
        assert "trw-implementer.md" not in manifest_after["content_hashes"]


@pytest.mark.integration
class TestCorruptManifestRefusal:
    def test_scoped_and_whole_project_both_refuse_on_a_corrupt_manifest(self, tmp_path: Path) -> None:
        """PRD-INFRA-192 FR09 C3: whole-project uninstall is manifest-driven too,
        so a corrupt manifest refuses it exactly like a scoped ``--ide`` removal --
        deleting nothing while ownership cannot be read is the whole point of the
        refusal, and that reasoning does not become safer just because every
        client is being removed at once."""
        project = _claude_only_project(tmp_path)
        manifest_path = project / ".trw" / "managed-artifacts.yaml"
        manifest_path.write_text("version: 2\ncontent_hashes: [oops\n", encoding="utf-8")
        before = manifest_path.read_bytes()

        with pytest.raises(SystemExit) as exc_info:
            _run_uninstall(_ns(project, ide="claude-code"))
        assert exc_info.value.code == 1
        assert manifest_path.read_bytes() == before, "a refusal must write nothing"
        assert (project / ".claude" / "agents").exists(), "a refusal must delete nothing"

        with pytest.raises(SystemExit) as exc_info:
            _run_uninstall(_ns(project))
        assert exc_info.value.code == 1
        assert manifest_path.read_bytes() == before, "a whole-project refusal must write nothing either"
        assert (project / ".claude" / "agents").exists(), "a whole-project refusal must delete nothing"
        assert (project / ".trw").exists()

    def test_whole_project_proceeds_with_no_manifest_file_at_all(self, tmp_path: Path) -> None:
        """A project with NO manifest (never installed through this mechanism, or
        the manifest file was removed by hand) is not "corrupt" -- whole-project
        uninstall proceeds, keeping every plain client surface (rule 3) while
        still removing ``.trw`` itself."""
        project = _claude_only_project(tmp_path)
        manifest_path = project / ".trw" / "managed-artifacts.yaml"
        manifest_path.unlink()

        _run_uninstall(_ns(project))

        assert (project / ".claude" / "agents").exists(), "no manifest means no proof of ownership -- kept"
        assert not (project / ".trw").exists(), ".trw itself is framework-core and always removed"


@pytest.mark.integration
def test_bare_init_records_claude_code_as_owner_of_the_claude_scaffold(tmp_path: Path) -> None:
    """A bare init writes the Claude Code surfaces and records claude-code, so the
    owners map must name it even when detection resolved some other client."""
    (tmp_path / ".git").mkdir()
    (tmp_path / ".opencode").mkdir()
    assert not init_project(tmp_path)["errors"]

    owners = _read_manifest(tmp_path)["owners"]
    assert isinstance(owners, dict)
    skill_keys = [k for k in owners if k.endswith("/SKILL.md") and not k.startswith(".")]
    assert skill_keys, "precondition: bare init installed .claude skills"
    assert all("claude-code" in owners[k] for k in skill_keys)
