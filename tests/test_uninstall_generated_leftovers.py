"""Uninstall leaves no TRW-generated file behind: the distill explorer agent and cursor-cli hooks.

canary-eng found four files a plain uninstall of an untouched project never
removed or mentioned: ``.claude/agents/trw-distill-explorer.md`` and
``.cursor/hooks/trw-{after-mcp,after-shell,before-shell}.sh``. None had a
managed source, so install recorded no hash and uninstall could not prove them
TRW's own. Cursor-cli-only hooks and the rendered explorer agent are now
registered sources; ``update-project`` re-records projects installed before.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest
import yaml

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon", "entitled_to_distill")]

LEFTOVERS = (
    ".claude/agents/trw-distill-explorer.md",
    ".cursor/hooks/trw-after-mcp.sh",
    ".cursor/hooks/trw-after-shell.sh",
    ".cursor/hooks/trw-before-shell.sh",
)
IDES = "all"


@pytest.fixture
def entitled_to_distill(monkeypatch: pytest.MonkeyPatch) -> None:
    """The explorer agent is licence-gated; canary-eng's install was entitled."""
    monkeypatch.setattr("trw_mcp.bootstrap._distill_entitlement.distill_artifacts_entitled", lambda **_kw: True)


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide=IDES)
    assert not result["errors"], result["errors"]
    for rel in LEFTOVERS:
        assert (tmp_path / rel).is_file(), f"precondition: install wrote {rel}"
    return tmp_path


def _manifest_path(project: Path) -> Path:
    return project / ".trw" / "managed-artifacts.yaml"


def _hashes(project: Path) -> dict[str, str]:
    return yaml.safe_load(_manifest_path(project).read_text(encoding="utf-8"))["content_hashes"]


def test_install_records_all_four(tmp_path: Path) -> None:
    project = _project(tmp_path)
    recorded = _hashes(project)
    for rel in LEFTOVERS:
        assert recorded.get(rel) == hashlib.sha256((project / rel).read_bytes()).hexdigest(), rel


def test_update_then_uninstall_leaves_none_of_the_four(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert not update_project(project, ide=IDES)["errors"]

    _run_uninstall(_ns(project))

    assert [rel for rel in LEFTOVERS if (project / rel).exists()] == []
    assert not (project / ".trw").exists()


def test_project_installed_before_the_fix_is_re_recorded_by_update(tmp_path: Path) -> None:
    project = _project(tmp_path)
    manifest = yaml.safe_load(_manifest_path(project).read_text(encoding="utf-8"))
    for rel in LEFTOVERS:
        del manifest["content_hashes"][rel]
    _manifest_path(project).write_text(yaml.safe_dump(manifest), encoding="utf-8")
    assert not set(LEFTOVERS) & set(_hashes(project)), "precondition: old-install manifest lacks the four"

    assert not update_project(project, ide=IDES)["errors"]
    assert set(LEFTOVERS) <= set(_hashes(project)), "update-project must re-record bytes matching the bundle"

    _run_uninstall(_ns(project))
    assert [rel for rel in LEFTOVERS if (project / rel).exists()] == []


@pytest.mark.parametrize("edited", LEFTOVERS)
def test_user_edited_copy_is_kept_and_reported(tmp_path: Path, edited: str, capsys: pytest.CaptureFixture[str]) -> None:
    project = _project(tmp_path)
    mine = project / edited
    mine.write_bytes(mine.read_bytes() + b"\n# my own edit\n")
    edited_bytes = mine.read_bytes()

    _run_uninstall(_ns(project))

    assert mine.read_bytes() == edited_bytes, "a user-edited file must survive byte-identical"
    assert [rel for rel in LEFTOVERS if rel != edited and (project / rel).exists()] == []
    out = capsys.readouterr()
    assert edited in out.out + out.err, "the kept file must be reported"


def test_user_files_beside_them_survive(tmp_path: Path) -> None:
    project = _project(tmp_path)
    mine = {
        project / ".claude" / "agents" / "my-agent.md": b"# my own agent\n",
        project / ".cursor" / "hooks" / "my-hook.sh": b"#!/bin/sh\necho mine\n",
    }
    for path, content in mine.items():
        path.write_bytes(content)
    before = snapshot_user_bytes(project)

    _run_uninstall(_ns(project))

    for path, content in mine.items():
        assert path.read_bytes() == content
    assert_user_bytes_preserved(
        {h: n for h, n in before.items() if h in {hashlib.sha256(c).hexdigest() for c in mine.values()}}, project
    )
