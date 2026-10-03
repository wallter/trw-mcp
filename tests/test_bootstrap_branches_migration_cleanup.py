"""Split bootstrap branch coverage for migration and stale-cleanup edges."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import (
    _get_bundled_names,
    _remove_stale_artifacts,
    update_project,
)
from trw_mcp.bootstrap._version_migration import _cleanup_stale_artifacts

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("no_memory_daemon")


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run(tmp_path: Path, hashes: dict[str, str]) -> dict[str, list[str]]:
    """The update's cleanup with *hashes* as the pre-run manifest's ``content_hashes``."""
    (tmp_path / ".trw").mkdir(exist_ok=True)
    (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(
        yaml.safe_dump({"version": 2, "content_hashes": hashes}), encoding="utf-8"
    )
    result: dict[str, list[str]] = {"updated": [], "errors": []}
    _cleanup_stale_artifacts(tmp_path, result, None, manifest_hashes=hashes)
    return result


def _skill(tmp_path: Path, name: str, body: str = "old") -> Path:
    path = tmp_path / ".claude" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.unit
class TestRetirementPredicate:
    """REMOVE-S8a: a ``trw-*`` skill or agent TRW no longer ships on that surface is retired, with proof."""

    def test_a_recorded_trw_name_missing_from_the_bundle_is_retired(self, tmp_path: Path) -> None:
        """No name list: any recorded, unchanged ``trw-*`` skill or agent outside the bundle goes."""
        skill = _skill(tmp_path, "trw-gone")
        agent = tmp_path / ".claude" / "agents" / "trw-gone.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("old", encoding="utf-8")

        result = _run(tmp_path, {"trw-gone/SKILL.md": _sha("old"), "trw-gone.md": _sha("old")})

        assert not skill.parent.exists()
        assert not agent.exists()
        assert not result.get("preserved")

    def test_a_users_own_unrecorded_trw_skill_is_kept_and_reported(self, tmp_path: Path) -> None:
        skill = _skill(tmp_path, "trw-mine", "my own skill")

        result = _run(tmp_path, {"trw-audit/SKILL.md": "0" * 64})

        assert skill.read_text(encoding="utf-8") == "my own skill"
        assert result["preserved"] == [".claude/skills/trw-mine (not_installer_owned)"]

    def test_without_an_opencode_inventory_nothing_in_opencode_skills_is_swept(self, tmp_path: Path) -> None:
        """codex S8a r1 KI1 + lead ruling (b): no inventory means opencode's list is unknown, not empty.

        Before, an absent inventory read as an empty curated list, so every recorded, unchanged skill in
        ``.opencode/skills`` (still-bundled ones too) was trashed. Now that surface is skipped; .claude still sweeps.
        """
        from trw_mcp.bootstrap._utils import _DATA_DIR

        data = tmp_path / "data"
        for name in ("skills", "agents", "hooks"):
            (data / name).mkdir(parents=True)
        for skill in (_DATA_DIR / "skills").iterdir():
            if skill.is_dir():
                (data / "skills" / skill.name).mkdir()
        assert not (data / "opencode" / "skills_inventory.yaml").exists()
        project = tmp_path / "project"
        for root in (".opencode/skills", ".claude/skills"):
            path = project / root / "trw-gone" / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("old", encoding="utf-8")
        audit = project / ".opencode" / "skills" / "trw-audit" / "SKILL.md"
        audit.parent.mkdir(parents=True)
        audit.write_text("old", encoding="utf-8")
        hashes = {
            ".opencode/skills/trw-gone/SKILL.md": _sha("old"),
            ".opencode/skills/trw-audit/SKILL.md": _sha("old"),
            "trw-gone/SKILL.md": _sha("old"),
        }
        (project / ".trw").mkdir()
        (project / ".trw" / "managed-artifacts.yaml").write_text(
            yaml.safe_dump({"version": 2, "content_hashes": hashes}), encoding="utf-8"
        )
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        _cleanup_stale_artifacts(project, result, data, manifest_hashes=hashes)

        assert (project / ".opencode" / "skills" / "trw-gone" / "SKILL.md").read_text(encoding="utf-8") == "old"
        assert audit.read_text(encoding="utf-8") == "old"
        assert result.get("retired") == [".claude/skills/trw-gone/SKILL.md"], "non-vacuous: .claude still sweeps"

    def test_an_inventory_that_appears_mid_update_never_reads_as_an_empty_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """codex S8a r3: membership and the skip decision came from two reads of the inventory.

        Absent when the bundle was listed (so an empty OpenCode list) and present at the separate existence check,
        the sweep ran with that empty list and trashed skills OpenCode still ships. One read now decides both.
        """
        import shutil

        from trw_mcp.bootstrap import _template_updater
        from trw_mcp.bootstrap._utils import _DATA_DIR

        name = sorted(_get_bundled_names()["opencode_skills"])[0]
        data = tmp_path / "data"
        for sub in ("skills", "agents", "hooks", "opencode"):
            (data / sub).mkdir(parents=True)
        for skill in (_DATA_DIR / "skills").iterdir():
            if skill.is_dir():
                (data / "skills" / skill.name).mkdir()
        listed = _template_updater._get_bundled_names

        def list_then_inventory_appears(data_dir: Path | None = None) -> dict[str, list[str]]:
            names = listed(data_dir)
            shutil.copy(_DATA_DIR / "opencode" / "skills_inventory.yaml", data / "opencode" / "skills_inventory.yaml")
            return names

        monkeypatch.setattr(_template_updater, "_get_bundled_names", list_then_inventory_appears)
        project = tmp_path / "project"
        copy = project / ".opencode" / "skills" / name / "SKILL.md"
        copy.parent.mkdir(parents=True)
        copy.write_text("old", encoding="utf-8")
        hashes = {f".opencode/skills/{name}/SKILL.md": _sha("old")}
        (project / ".trw").mkdir()
        (project / ".trw" / "managed-artifacts.yaml").write_text(
            yaml.safe_dump({"version": 2, "content_hashes": hashes}), encoding="utf-8"
        )

        _cleanup_stale_artifacts(project, {"updated": [], "errors": []}, data, manifest_hashes=hashes)

        assert copy.read_text(encoding="utf-8") == "old"

    def test_a_flag_gated_skill_in_a_client_mirror_survives_the_stale_sweep(self, tmp_path: Path) -> None:
        """Lead ruling (b) condition 1: retire_disabled_skills owns flag-gated skills; Codex's list never retires one."""
        from trw_mcp.bootstrap._client_skills import skill_names
        from trw_mcp.bootstrap._optional_skills import CONDITIONAL_SKILLS
        from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

        name = "trw-assess"
        assert name in CONDITIONAL_SKILLS
        assert name not in set(skill_names("codex")), "precondition: outside Codex's list, so only the guard keeps it"
        copy = tmp_path / ".agents" / "skills" / name / "SKILL.md"
        copy.parent.mkdir(parents=True)
        copy.write_text("old", encoding="utf-8")
        gone = tmp_path / ".agents" / "skills" / "trw-gone" / "SKILL.md"
        gone.parent.mkdir(parents=True)
        gone.write_text("old", encoding="utf-8")
        hashes = {f".agents/skills/{name}/SKILL.md": _sha("old"), ".agents/skills/trw-gone/SKILL.md": _sha("old")}
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)

        assert copy.read_text(encoding="utf-8") == "old"
        assert not gone.exists(), "non-vacuous: a recorded trw-gone in the same mirror is retired"

    def test_a_skill_one_client_dropped_is_retired_there_though_its_canonical_copy_lives(self, tmp_path: Path) -> None:
        """codex S8a r3: the mirror-follows-source guard (PRD-FIX-139-FR03) is for names TRW does not ship.

        A skill still in the full bundle but outside Copilot's curated list always has a live ``.claude/skills``
        copy, so that guard kept the Copilot copy forever and the per-client rule never fired. A local skill (not
        shipped) with a live canonical copy still keeps its mirror.
        """
        from trw_mcp.bootstrap._client_skills import skill_names
        from trw_mcp.bootstrap._optional_skills import CONDITIONAL_SKILLS
        from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

        dropped = sorted(set(_get_bundled_names()["skills"]) - set(skill_names("copilot")) - set(CONDITIONAL_SKILLS))
        assert dropped, "precondition: a bundled skill outside Copilot's list"
        name = dropped[0]
        hashes: dict[str, str] = {}
        for skill in (name, "trw-mine"):
            _skill(tmp_path, skill)
            mirror = tmp_path / ".github" / "skills" / skill / "SKILL.md"
            mirror.parent.mkdir(parents=True)
            mirror.write_text("old", encoding="utf-8")
            hashes[f".github/skills/{skill}/SKILL.md"] = _sha("old")
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        _remove_stale_client_artifacts(tmp_path, result, manifest_hashes=hashes)

        assert not (tmp_path / ".github" / "skills" / name).exists()
        assert (tmp_path / ".claude" / "skills" / name / "SKILL.md").is_file()
        assert (tmp_path / ".github" / "skills" / "trw-mine" / "SKILL.md").read_text(encoding="utf-8") == "old"

    def test_a_flag_gated_bundled_skill_is_never_swept(self, tmp_path: Path) -> None:
        """trw-assess is conditional (assess_enabled) but bundled: recorded and unchanged, it still stays."""
        from trw_mcp.bootstrap._optional_skills import CONDITIONAL_SKILLS

        assert CONDITIONAL_SKILLS, "precondition: there is a flag-gated skill to test"
        for name in CONDITIONAL_SKILLS:
            _skill(tmp_path, name)

        _run(tmp_path, {f"{name}/SKILL.md": _sha("old") for name in CONDITIONAL_SKILLS})

        assert all((tmp_path / ".claude" / "skills" / name).is_dir() for name in CONDITIONAL_SKILLS)

    @pytest.mark.parametrize(
        "relpath", [".claude/agents/trw-distill-explorer.md", ".opencode/agents/trw-distill-explorer.md"]
    )
    def test_a_channel_explorer_is_never_this_sweeps_to_retire(self, tmp_path: Path, relpath: str) -> None:
        """Its channel installs and withdraws it (distill entitlement): recorded or not, kept and not reported."""
        explorer = tmp_path / relpath
        explorer.parent.mkdir(parents=True)
        explorer.write_text("explorer", encoding="utf-8")

        result = _run(tmp_path, {relpath: _sha("explorer"), Path(relpath).name: _sha("explorer")})

        assert explorer.exists()
        assert not result.get("preserved")

    def test_a_bare_pre_prefix_name_is_the_projects_own(self, tmp_path: Path) -> None:
        """Outside the ``trw-`` namespace nothing is retired: a recorded ``review-pr`` stays."""
        skill = _skill(tmp_path, "review-pr")

        _run(tmp_path, {"review-pr/SKILL.md": _sha("old")})

        assert skill.exists()

    def test_dry_run_reports_the_retirement_without_deleting(self, initialized_repo: Path) -> None:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        skill = _skill(initialized_repo, "trw-review-pr")
        manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
        manifest = FileStateReader().read_yaml(manifest_path)
        manifest["content_hashes"]["trw-review-pr/SKILL.md"] = _sha("old")
        FileStateWriter().write_yaml(manifest_path, manifest)

        result = update_project(initialized_repo, dry_run=True)

        assert skill.exists()
        assert ".claude/skills/trw-review-pr/SKILL.md" in result["cleaned"]

    @pytest.mark.parametrize("missing", ["skills", "agents"])
    def test_a_missing_dir_is_not_an_error(self, tmp_path: Path, missing: str) -> None:
        present = "agents" if missing == "skills" else "skills"
        (tmp_path / ".claude" / present).mkdir(parents=True)

        result = _run(tmp_path, {})

        assert not result["errors"]


@pytest.mark.unit
class TestRemoveStaleArtifactsCustomPreservation:
    """Custom artifacts listed in prev_custom_* must not be removed during stale cleanup."""

    def _setup_manifest_with_custom(
        self,
        target_dir: Path,
        extra_skills: list[str] | None = None,
        extra_agents: list[str] | None = None,
        extra_hooks: list[str] | None = None,
        custom_skills: list[str] | None = None,
        custom_agents: list[str] | None = None,
        custom_hooks: list[str] | None = None,
    ) -> None:
        from trw_mcp.state.persistence import FileStateWriter

        bundled = _get_bundled_names()
        manifest = {
            "version": 1,
            "skills": bundled["skills"] + (extra_skills or []),
            "agents": bundled["agents"] + (extra_agents or []),
            "hooks": bundled["hooks"] + (extra_hooks or []),
            "custom_skills": custom_skills or [],
            "custom_agents": custom_agents or [],
            "custom_hooks": custom_hooks or [],
        }
        manifest_path = target_dir / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        FileStateWriter().write_yaml(manifest_path, manifest)

    def test_custom_skill_not_removed(self, initialized_repo: Path) -> None:
        """A skill listed in prev_custom_skills is NOT removed even if it's stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_skills=["trw-my-custom"],
            custom_skills=["trw-my-custom"],
        )
        custom_skill = initialized_repo / ".claude" / "skills" / "trw-my-custom"
        custom_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_skill.exists()
        assert not any("trw-my-custom" in u for u in result["updated"])

    def test_custom_agent_not_removed(self, initialized_repo: Path) -> None:
        """A trw- agent in prev_custom_agents is NOT removed even if it's stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_agents=["trw-my-agent.md"],
            custom_agents=["trw-my-agent.md"],
        )
        custom_agent = initialized_repo / ".claude" / "agents" / "trw-my-agent.md"
        custom_agent.write_text("custom agent", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_agent.exists()
        assert not any("trw-my-agent" in u for u in result["updated"])

    def test_custom_hook_not_removed(self, initialized_repo: Path) -> None:
        """A hook listed in prev_custom_hooks is NOT removed even if stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_hooks=["my-custom-hook.sh"],
            custom_hooks=["my-custom-hook.sh"],
        )
        custom_hook = initialized_repo / ".claude" / "hooks" / "my-custom-hook.sh"
        custom_hook.write_text("#!/bin/sh", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_hook.exists()
        assert not any("my-custom-hook" in u for u in result["updated"])

    def test_non_trw_prefixed_stale_skill_not_removed(self, initialized_repo: Path) -> None:
        """Stale skills without trw- prefix are skipped (defense-in-depth guard)."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_skills=["stale-no-prefix"],
        )
        stale_skill = initialized_repo / ".claude" / "skills" / "stale-no-prefix"
        stale_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert stale_skill.exists()
        assert not any("stale-no-prefix" in u for u in result["updated"])

    def test_non_trw_prefixed_stale_agent_not_removed(self, initialized_repo: Path) -> None:
        """Stale agents without trw- prefix are skipped (defense-in-depth guard)."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_agents=["my-old-agent.md"],
        )
        stale_agent = initialized_repo / ".claude" / "agents" / "my-old-agent.md"
        stale_agent.write_text("old", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert stale_agent.exists()
        assert not any("my-old-agent" in u for u in result["updated"])


@pytest.mark.unit
class TestStaleCleanupOwnership:
    """A stale bundled artifact is removed only with manifest proof (PRD-INFRA-190-FR06).

    Removals are reported from the update's surface diff (``cleaned``), in both
    the dry run and the real run.
    """

    @staticmethod
    def _write_manifest_with_ghost(target_dir: Path, *, recorded: bool) -> Path:
        from trw_mcp.state.persistence import FileStateWriter

        bundled = _get_bundled_names()
        manifest = {
            "version": 2,
            "skills": bundled["skills"] + ["trw-ghost"],
            "agents": bundled["agents"],
            "hooks": bundled["hooks"],
            "custom_skills": [],
            "custom_agents": [],
            "custom_hooks": [],
            "content_hashes": {"trw-ghost/SKILL.md": _sha("stale")} if recorded else {},
        }
        manifest_path = target_dir / ".trw" / "managed-artifacts.yaml"
        FileStateWriter().write_yaml(manifest_path, manifest)
        ghost = target_dir / ".claude" / "skills" / "trw-ghost"
        ghost.mkdir(parents=True, exist_ok=True)
        (ghost / "SKILL.md").write_text("stale", encoding="utf-8")
        return ghost

    def test_dry_run_reports_the_removal_and_keeps_the_file(self, initialized_repo: Path) -> None:
        ghost = self._write_manifest_with_ghost(initialized_repo, recorded=True)

        result = update_project(initialized_repo, dry_run=True)

        assert ghost.exists()
        assert ".claude/skills/trw-ghost/SKILL.md" in result["cleaned"]

    def test_real_run_deletes_a_recorded_stale_artifact(self, initialized_repo: Path) -> None:
        ghost = self._write_manifest_with_ghost(initialized_repo, recorded=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert not ghost.exists()

    def test_unrecorded_stale_artifact_is_kept(self, initialized_repo: Path) -> None:
        ghost = self._write_manifest_with_ghost(initialized_repo, recorded=False)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert ghost.exists()
        assert result["preserved"] == [".claude/skills/trw-ghost (not_installer_owned)"]


def test_a_dry_run_leaves_the_retired_file_in_place(initialized_repo: Path) -> None:
    """A dry run sweeps a scratch copy: the preview names the file it would retire and the target keeps it."""
    skill = initialized_repo / ".claude" / "skills" / "trw-gone" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("old", encoding="utf-8")
    manifest_path = initialized_repo / ".trw" / "managed-artifacts.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("content_hashes", {})["trw-gone/SKILL.md"] = _sha("old")
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = update_project(initialized_repo, dry_run=True)

    assert ".claude/skills/trw-gone/SKILL.md" in result.get("retired", []), "precondition: the preview sweeps it"
    assert skill.read_text(encoding="utf-8") == "old"
