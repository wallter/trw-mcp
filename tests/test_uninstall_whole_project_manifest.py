"""PRD-INFRA-192 FR09 "C3": uninstall never deletes a user's file.

Extends the manifest-driven uninstall from a scoped ``--ide`` removal
(``tests/test_uninstall_ide_manifest.py``) to a WHOLE-PROJECT uninstall, and
proves the recorder-coverage gaps this change closes: ``.claude/loop.md``,
``.cursor/rules/trw-ceremony.mdc`` and ``.codex/hooks/trw_post_edit_telemetry.py``
are now recorded, so a scoped or whole-project uninstall can prove they are
TRW's own unedited write instead of guessing.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project
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


def _read_manifest(project: Path) -> dict[str, object]:
    return yaml.safe_load((project / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))


def _init(tmp_path: Path, ide: str) -> Path:
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide=ide)
    assert not result["errors"], result["errors"]
    return tmp_path


@pytest.mark.integration
class TestWholeProjectUserFilesSurviveClaudeCode:
    def test_user_files_in_skills_agents_hooks_survive_and_trw_files_are_gone(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
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

        _run_uninstall(_ns(project))

        for path, content in mine.items():
            assert path.read_bytes() == content, f"{path} is the user's and must survive byte-identical"
        assert [p for p in trw_files if p.exists()] == []
        assert not (project / ".trw").exists()


@pytest.mark.integration
class TestWholeProjectCursorRulesRecorderGap:
    def test_user_rule_survives_scoped_and_whole_project_ceremony_mdc_is_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "cursor-ide")
        rules_dir = project / ".cursor" / "rules"
        my_rule = rules_dir / "my-rule.mdc"
        my_rule.write_text("---\nalwaysApply: false\n---\nmy own rule\n", encoding="utf-8")
        ceremony = rules_dir / "trw-ceremony.mdc"
        assert ceremony.is_file(), "precondition: TRW wrote its ceremony rule"
        manifest = _read_manifest(project)
        assert ".cursor/rules/trw-ceremony.mdc" in manifest["content_hashes"], (
            "precondition: the recorder-coverage gap is closed"
        )

        _run_uninstall(_ns(project, ide="cursor-ide"))

        assert my_rule.read_text(encoding="utf-8") == "---\nalwaysApply: false\n---\nmy own rule\n"
        assert not ceremony.exists()

    def test_user_rule_survives_whole_project_ceremony_mdc_is_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "cursor-ide")
        rules_dir = project / ".cursor" / "rules"
        my_rule = rules_dir / "my-rule.mdc"
        my_rule.write_text("my own rule\n", encoding="utf-8")
        ceremony = rules_dir / "trw-ceremony.mdc"
        assert ceremony.is_file()

        _run_uninstall(_ns(project))

        assert my_rule.read_text(encoding="utf-8") == "my own rule\n"
        assert not ceremony.exists()


@pytest.mark.integration
class TestWholeProjectCodexHookRecorderGap:
    def test_user_hook_survives_trw_telemetry_hook_is_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "codex")
        hooks_dir = project / ".codex" / "hooks"
        mine = hooks_dir / "mine.py"
        mine.write_text("# my own hook\n", encoding="utf-8")
        telemetry = hooks_dir / "trw_post_edit_telemetry.py"
        assert telemetry.is_file(), "precondition: the codex distill telemetry hook is written by default"
        manifest = _read_manifest(project)
        assert ".codex/hooks/trw_post_edit_telemetry.py" in manifest["content_hashes"]

        _run_uninstall(_ns(project))

        assert mine.read_text(encoding="utf-8") == "# my own hook\n"
        assert not telemetry.exists()


@pytest.mark.integration
class TestWholeProjectGrokAgentsAlreadyCovered:
    def test_user_file_survives_trw_grok_agents_are_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "grok")
        agents_dir = project / ".grok" / "agents"
        mine = agents_dir / "mine.md"
        mine.write_text("# my own subagent\n", encoding="utf-8")
        trw_agents = [p for p in agents_dir.glob("*.md") if p.name != "mine.md"]
        assert trw_agents, "precondition: TRW installed grok agents"

        _run_uninstall(_ns(project))

        assert mine.read_text(encoding="utf-8") == "# my own subagent\n"
        assert [p for p in trw_agents if p.exists()] == []


@pytest.mark.integration
class TestWholeProjectLoopMdRecorderGap:
    def test_edited_loop_md_survives_unedited_one_is_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
        loop_md = project / ".claude" / "loop.md"
        assert loop_md.is_file(), "precondition: TRW writes .claude/loop.md"
        loop_md.write_text(loop_md.read_text(encoding="utf-8") + "\n<!-- my edit -->\n", encoding="utf-8")

        _run_uninstall(_ns(project, ide="claude-code"))

        assert loop_md.is_file(), "an edited TRW file must be preserved, not deleted"

    def test_unedited_loop_md_is_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
        loop_md = project / ".claude" / "loop.md"
        assert loop_md.is_file()

        _run_uninstall(_ns(project, ide="claude-code"))

        assert not loop_md.exists()


@pytest.mark.integration
class TestLegacyManifestWithNoContentHashesKeepsTheDirWhole:
    def test_stripped_content_hashes_keep_the_directory_and_note_it(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A manifest that once covered a surface but no longer does (e.g. an
        older schema, or every recorded key aged out) must not fall back to
        guessing wholesale deletion -- rule 3 keeps the directory whole."""
        project = _init(tmp_path, "claude-code")
        manifest_path = project / ".trw" / "managed-artifacts.yaml"
        manifest = _read_manifest(project)
        content_hashes = manifest["content_hashes"]
        assert isinstance(content_hashes, dict)
        stripped_keys = [k for k in content_hashes if k.endswith((".md", ".sh"))]
        assert stripped_keys, "precondition: claude-code recorded agent/hook keys"
        for key in stripped_keys:
            content_hashes.pop(key, None)
        manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

        _run_uninstall(_ns(project))

        assert (project / ".claude" / "agents").exists(), "no recorded key under it: kept, not guessed at"
        assert (project / ".claude" / "hooks").exists()
        out = capsys.readouterr().out
        assert "not recorded by TRW" in out


@pytest.mark.integration
class TestWholeProjectManifestRefusal:
    def test_corrupt_manifest_refuses_and_deletes_nothing(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
        manifest_path = project / ".trw" / "managed-artifacts.yaml"
        manifest_path.write_text("version: 2\ncontent_hashes: [oops\n", encoding="utf-8")
        before = manifest_path.read_bytes()

        with pytest.raises(SystemExit) as exc_info:
            _run_uninstall(_ns(project))
        assert exc_info.value.code == 1
        assert manifest_path.read_bytes() == before
        assert (project / ".claude" / "agents").exists()
        assert (project / ".trw").exists()

    def test_no_manifest_file_keeps_plain_dirs_but_still_removes_trw(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
        manifest_path = project / ".trw" / "managed-artifacts.yaml"
        manifest_path.unlink()

        _run_uninstall(_ns(project))

        assert (project / ".claude" / "agents").exists(), "no manifest to prove ownership: kept"
        assert (project / ".claude" / "skills").exists()
        assert (project / ".claude" / "hooks").exists()
        assert not (project / ".trw").exists(), ".trw itself is framework-core and always removed"


@pytest.mark.integration
class TestWholeProjectCoreRootFilesReviewFollowup:
    """PRD-INFRA-192 FR09 C3 review follow-up: the ``core_scaffold_relpaths()``
    exemption used to cover ``REVIEW.md``/``FRAMEWORK.md``/``AARE-F-FRAMEWORK.md``
    too, so these plain root FILES were still wholesale-removed with no manifest
    check at all -- a user's own pre-existing ``REVIEW.md`` (Claude Code's own
    code-review config) or a hand-edited copy of either canon doc was destroyed.
    Only ``.trw`` is exempt now; these three route through the same
    manifest-covered / ``plan_uncovered_surface`` path a client file does.
    """

    def test_user_authored_review_md_that_predates_init_survives(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        user_review = "# My own Claude Code review config\n"
        (tmp_path / "REVIEW.md").write_text(user_review, encoding="utf-8")
        from trw_mcp.bootstrap import init_project

        result = init_project(tmp_path, ide="claude-code")
        assert not result["errors"], result["errors"]
        assert (tmp_path / "REVIEW.md").read_text(encoding="utf-8") == user_review, (
            "precondition: init-project's _write_if_missing never touches a pre-existing REVIEW.md"
        )

        _run_uninstall(_ns(tmp_path))

        assert (tmp_path / "REVIEW.md").read_text(encoding="utf-8") == user_review

    def test_unedited_review_and_framework_docs_are_removed(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
        assert (project / "REVIEW.md").is_file()
        assert (project / "FRAMEWORK.md").is_file()
        assert (project / "AARE-F-FRAMEWORK.md").is_file()

        _run_uninstall(_ns(project))

        assert not (project / "REVIEW.md").exists()
        assert not (project / "FRAMEWORK.md").exists()
        assert not (project / "AARE-F-FRAMEWORK.md").exists()

    def test_edited_framework_md_is_kept(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        project = _init(tmp_path, "claude-code")
        framework_md = project / "FRAMEWORK.md"
        edited = framework_md.read_text(encoding="utf-8") + "\n<!-- my edit -->\n"
        framework_md.write_text(edited, encoding="utf-8")

        _run_uninstall(_ns(project))

        assert framework_md.read_text(encoding="utf-8") == edited
        assert "not recorded by TRW" in capsys.readouterr().out


@pytest.mark.integration
class TestWholeProjectDryRunChangesNothing:
    def test_dry_run_leaves_every_byte_untouched(self, tmp_path: Path) -> None:
        project = _init(tmp_path, "claude-code")
        (project / ".claude" / "agents" / "my-agent.md").write_text("mine\n", encoding="utf-8")
        before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}

        _run_uninstall(_ns(project, dry_run=True))

        after = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
        assert before == after
