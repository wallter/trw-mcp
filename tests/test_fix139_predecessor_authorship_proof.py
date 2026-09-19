"""PRD-FIX-139 — a retired bundle name is deleted only with proof TRW wrote it.

``trw-release-verify`` was retired from the bundle on 2026-07-19 as an internal
dev-only skill. The monorepo that develops TRW keeps exactly that skill under
``.claude/skills/`` (it is the release gate), so ``update-project`` on a fresh
checkout deleted it from three client mirrors — reproduced 2026-09-16. The
deletion-only predecessor sweep had no authorship check at all, unlike the
file-granular sweep in ``_version_migration_clients`` which already refuses to
delete anything whose bytes do not hash to the manifest record.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.bootstrap._version_migration_predecessors import _migrate_prefix_predecessors

pytestmark = pytest.mark.unit

RETIRED = "trw-release-verify"


def _project_with_retired_skill(tmp_path: Path, body: str = "# release gate\n") -> Path:
    skill = tmp_path / ".claude" / "skills" / RETIRED / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(body, encoding="utf-8")
    return skill


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(tmp_path: Path, manifest_hashes: dict[str, str] | None) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"updated": [], "errors": []}
    _migrate_prefix_predecessors(tmp_path, result, manifest_hashes=manifest_hashes)
    return result


class TestRetiredNameNeedsAuthorshipProof:
    def test_recorded_and_unchanged_is_removed(self, tmp_path: Path) -> None:
        """FR01: TRW wrote it and nobody edited it -> the retirement applies."""
        skill = _project_with_retired_skill(tmp_path)
        result = _run(tmp_path, {f"{RETIRED}/SKILL.md": _sha(skill)})
        assert not skill.parent.exists()
        assert not result.get("preserved")

    def test_unrecorded_is_preserved_and_reported(self, tmp_path: Path) -> None:
        """FR01: the monorepo case — a manifest with hashes but none for this name."""
        skill = _project_with_retired_skill(tmp_path)
        result = _run(tmp_path, {"trw-audit/SKILL.md": "0" * 64})
        assert skill.exists()
        assert result["updated"] == []
        assert result["preserved"] == [f".claude/skills/{RETIRED} (not_installer_owned)"]

    def test_drifted_content_is_preserved(self, tmp_path: Path) -> None:
        """FR01: TRW wrote it once, the user edited it since -> user work, keep it."""
        skill = _project_with_retired_skill(tmp_path, body="# edited by the project\n")
        result = _run(tmp_path, {f"{RETIRED}/SKILL.md": "f" * 64})
        assert skill.exists()
        assert result["preserved"]

    def test_extra_unrecorded_file_inside_the_dir_blocks_removal(self, tmp_path: Path) -> None:
        """FR01: every regular file under the artifact must be proven, not just SKILL.md."""
        skill = _project_with_retired_skill(tmp_path)
        (skill.parent / "notes.md").write_text("mine\n", encoding="utf-8")
        result = _run(tmp_path, {f"{RETIRED}/SKILL.md": _sha(skill)})
        assert skill.exists()
        assert result["preserved"]

    @pytest.mark.parametrize("legacy", [None, {}])
    def test_no_hash_record_means_no_proof_so_the_artifact_is_kept(self, tmp_path: Path, legacy) -> None:
        """PRD-INFRA-190-FR06 supersedes FIX-139-FR02: a first run or a v1 manifest proves
        nothing, and a sweep deletes only what TRW can prove it wrote."""
        skill = _project_with_retired_skill(tmp_path)
        result = _run(tmp_path, legacy)
        assert skill.exists()
        assert result["preserved"] == [f".claude/skills/{RETIRED} (not_installer_owned)"]

    def test_client_mirror_keys_match_by_path_suffix(self, tmp_path: Path) -> None:
        """FR01: opencode records ``.opencode/skills/<name>/SKILL.md``; the proof must find it."""
        mirror = tmp_path / ".opencode" / "skills" / RETIRED / "SKILL.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text("# mirror\n", encoding="utf-8")
        result = _run(tmp_path, {f".opencode/skills/{RETIRED}/SKILL.md": _sha(mirror)})
        assert not mirror.parent.exists()
        assert not result.get("preserved")

    def test_rename_migrations_need_the_same_proof(self, tmp_path: Path) -> None:
        """PRD-INFRA-190-FR06: a present successor proves nothing about who wrote the predecessor."""
        from trw_mcp.bootstrap._version_migration import PREDECESSOR_MAP

        old, new = next((o, n) for o, n in PREDECESSOR_MAP["skills"].items() if n is not None)
        skills = tmp_path / ".claude" / "skills"
        (skills / old).mkdir(parents=True)
        (skills / old / "SKILL.md").write_text("old", encoding="utf-8")
        (skills / new).mkdir(parents=True)
        (skills / new / "SKILL.md").write_text("new", encoding="utf-8")
        result = _run(tmp_path, {"unrelated/SKILL.md": "0" * 64})
        assert (skills / old).exists()
        assert result["preserved"] == [f".claude/skills/{old} (not_installer_owned)"]
        recorded = _run(tmp_path, {f"{old}/SKILL.md": _sha(skills / old / "SKILL.md")})
        assert not (skills / old).exists()
        assert not recorded.get("preserved")


class TestClientMirrorsFollowTheirSource:
    def test_mirror_of_a_preserved_source_skill_is_kept(self, tmp_path: Path) -> None:
        """FR03: the codex mirror TRW rendered from a preserved .claude skill stays with it."""
        source = _project_with_retired_skill(tmp_path)
        mirror = tmp_path / ".agents" / "skills" / RETIRED / "SKILL.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text("# mirror\n", encoding="utf-8")
        # The mirror IS recorded (TRW wrote it), the source is NOT (the project's own).
        result = _run(tmp_path, {f".agents/skills/{RETIRED}/SKILL.md": _sha(mirror)})
        assert source.exists()
        assert mirror.exists()
        assert result["updated"] == []

    def test_mirror_of_a_removed_source_skill_is_still_swept(self, tmp_path: Path) -> None:
        """Non-vacuity: once the source is gone, a proven mirror is retired as before."""
        mirror = tmp_path / ".agents" / "skills" / RETIRED / "SKILL.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text("# mirror\n", encoding="utf-8")
        result = _run(tmp_path, {f".agents/skills/{RETIRED}/SKILL.md": _sha(mirror)})
        assert not mirror.parent.exists()
        assert not result.get("preserved")


class TestStaleClientSurfaceSweepFollowsTheSource:
    """The dir-level codex/cursor/copilot sweep had no authorship check at all."""

    def _mirror(self, tmp_path: Path, client_dir: str) -> Path:
        mirror = tmp_path / client_dir / RETIRED / "SKILL.md"
        mirror.parent.mkdir(parents=True)
        mirror.write_text("# mirror\n", encoding="utf-8")
        return mirror

    def test_mirror_kept_while_the_claude_source_exists(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

        _project_with_retired_skill(tmp_path)
        mirror = self._mirror(tmp_path, ".agents/skills")
        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _remove_stale_client_artifacts(tmp_path, result)
        assert mirror.exists()
        assert any(str(mirror.parent) in line for line in result["preserved"])

    def test_mirror_removed_once_the_claude_source_is_gone(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

        mirror = self._mirror(tmp_path, ".agents/skills")
        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _remove_stale_client_artifacts(
            tmp_path, result, manifest_hashes={f".agents/skills/{RETIRED}/SKILL.md": _sha(mirror)}
        )
        assert not mirror.parent.exists()
        assert not result.get("preserved")
