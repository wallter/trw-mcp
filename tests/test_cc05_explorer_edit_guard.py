"""``update-project`` must not overwrite a user-edited ``.claude/agents/trw-distill-explorer.md`` (HB-2).

CC05-EXPLORER-OVERWRITES-EDITS: ``install_cc05_subagent`` rewrote the file whenever its bytes differed
from the render, so a hand-tuned explorer agent was lost on every update. It now uses the shared
managed-artifact guard: absent -> write; TRW's own bytes (recorded hash or any framework render) ->
refresh; anything else -> keep untouched and warn.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes
from trw_mcp.bootstrap import init_project, update_project

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon", "entitled_to_distill")]

REL = ".claude/agents/trw-distill-explorer.md"
EDIT = b"---\nname: trw-distill-explorer\n---\n# my hand-tuned explorer\n"


@pytest.fixture
def entitled_to_distill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_kw: True)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="claude-code")
    assert not result["errors"], result["errors"]
    assert (tmp_path / REL).is_file()
    return tmp_path


def _manifest(project: Path) -> Path:
    return project / ".trw" / "managed-artifacts.yaml"


def _report(result: dict[str, list[str]]) -> str:
    return "\n".join(str(v) for v in result.values())


def test_edited_agent_survives_update_and_is_reported(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / REL).write_bytes(EDIT)
    (project / "notes.md").write_bytes(b"# my notes\n")
    # TRW-generated files (manifest, REVIEW.md) legitimately change on update; the user's bytes must not.
    mine = {hashlib.sha256(EDIT).hexdigest(), hashlib.sha256(b"# my notes\n").hexdigest()}
    before = {h: n for h, n in snapshot_user_bytes(project).items() if h in mine}
    assert len(before) == 2

    result = update_project(project, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (project / REL).read_bytes() == EDIT
    assert any(
        REL in w and "kept because it was edited" in w and "delete it and re-run update-project" in w
        for w in result.get("warnings", [])
    ), result
    assert_user_bytes_preserved(before, project)
    # A second update still keeps it: the edit must not be adopted into the manifest.
    update_project(project, ide="claude-code")
    assert (project / REL).read_bytes() == EDIT


def test_unedited_agent_is_refreshed_when_render_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    new_render = "---\nname: trw-distill-explorer\n---\n# new bundled render\n"
    monkeypatch.setattr(
        "trw_mcp.channels.claude_code._explorer_subagent.get_explorer_agent_content", lambda: new_render
    )

    result = update_project(project, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (project / REL).read_text(encoding="utf-8") == new_render
    assert not any(REL in w for w in result.get("warnings", []))


def test_absent_unrecorded_agent_is_written(tmp_path: Path) -> None:
    """Absent and never recorded (an install that predates the record) -> written.

    A file the manifest DID record and the user then deleted stays deleted: that is the deletion
    tombstone convention (PRD-INFRA-192), enforced for every managed artifact, not this guard's concern.
    """
    project = _project(tmp_path)
    (project / REL).unlink()
    manifest = yaml.safe_load(_manifest(project).read_text(encoding="utf-8"))
    del manifest["content_hashes"][REL]
    _manifest(project).write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = update_project(project, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (project / REL).is_file()


def test_old_install_without_recorded_hash_unedited_is_refreshed_and_recorded(tmp_path: Path) -> None:
    project = _project(tmp_path)
    original = (project / REL).read_bytes()
    manifest = yaml.safe_load(_manifest(project).read_text(encoding="utf-8"))
    del manifest["content_hashes"][REL]
    _manifest(project).write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = update_project(project, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (project / REL).read_bytes() == original
    recorded = yaml.safe_load(_manifest(project).read_text(encoding="utf-8"))["content_hashes"]
    assert recorded.get(REL) == hashlib.sha256(original).hexdigest()


def test_old_install_without_recorded_hash_edited_is_kept(tmp_path: Path) -> None:
    project = _project(tmp_path)
    manifest = yaml.safe_load(_manifest(project).read_text(encoding="utf-8"))
    del manifest["content_hashes"][REL]
    _manifest(project).write_text(yaml.safe_dump(manifest), encoding="utf-8")
    (project / REL).write_bytes(EDIT)

    result = update_project(project, ide="claude-code")

    assert (project / REL).read_bytes() == EDIT
    assert any(REL in w and "edited" in w for w in result.get("warnings", [])), result
    assert REL not in yaml.safe_load(_manifest(project).read_text(encoding="utf-8"))["content_hashes"]


def test_the_reported_recovery_advice_restores_the_fresh_render(tmp_path: Path) -> None:
    """The warning's advice must work as written: delete the kept file, re-run update-project -> fresh render.

    The kept edit is never adopted into the manifest, so its deletion is not tombstoned (PRD-INFRA-192)
    and a plain update writes the current render again.
    """
    project = _project(tmp_path)
    fresh = (project / REL).read_bytes()
    (project / REL).write_bytes(EDIT)
    update_project(project, ide="claude-code")
    assert (project / REL).read_bytes() == EDIT

    (project / REL).unlink()
    result = update_project(project, ide="claude-code")
    assert not result["errors"], result["errors"]
    assert (project / REL).read_bytes() == fresh
