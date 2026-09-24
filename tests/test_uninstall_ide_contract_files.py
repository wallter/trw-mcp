"""``uninstall --ide`` removes trw-prd-ready's ``<phase>-contract.md`` files it wrote (codex, opencode).

init-project folds the readiness phases into ``trw-prd-ready`` as supporting
files. They used to be written outside ``skill_files()``, so the manifest never
recorded them and a scoped uninstall left them behind while printing that the
skill directory was removed. The writer and the recorder now read one list.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._client_skills import PRD_READY_CONTRACTS
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = pytest.mark.integration

_SKILLS_ROOT = {"codex": ".agents/skills", "opencode": ".opencode/skills"}


def _ns(project: Path, client: str) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide=client
    )


def _installed(project: Path, client: str) -> Path:
    (project / ".git").mkdir()
    result = init_project(project, ide=client)
    assert not result["errors"], result["errors"]
    return project / _SKILLS_ROOT[client]


@pytest.mark.parametrize("client", ["codex", "opencode"])
def test_init_records_every_contract_file_it_writes(tmp_path: Path, client: str) -> None:
    skills = _installed(tmp_path, client)

    written = sorted(p.name for p in (skills / "trw-prd-ready").glob("*-contract.md"))
    recorded = yaml.safe_load((tmp_path / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8"))

    assert written == sorted(f"{phase}-contract.md" for phase in PRD_READY_CONTRACTS[client])
    for name in written:
        path = skills / "trw-prd-ready" / name
        assert (
            recorded["content_hashes"][f"{_SKILLS_ROOT[client]}/trw-prd-ready/{name}"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        ), name


@pytest.mark.parametrize("client", ["codex", "opencode"])
@pytest.mark.parametrize("updated", [False, True], ids=["init", "init+update"])
def test_scoped_uninstall_leaves_no_trw_skill_file(tmp_path: Path, client: str, updated: bool) -> None:
    skills = _installed(tmp_path, client)
    if updated:
        result = update_project(tmp_path, ide=client)
        assert not result["errors"], result["errors"]
    assert list((skills / "trw-prd-ready").glob("*-contract.md")), "precondition: the contracts were written"

    _run_uninstall(_ns(tmp_path, client))

    left = sorted(str(p.relative_to(tmp_path)) for p in skills.rglob("*") if p.is_file()) if skills.exists() else []
    assert left == [], left


@pytest.mark.parametrize("client", ["codex", "opencode"])
def test_scoped_uninstall_keeps_an_edited_contract_and_a_users_own_file(tmp_path: Path, client: str) -> None:
    skills = _installed(tmp_path, client)
    ready = skills / "trw-prd-ready"
    edited = next(iter(sorted(ready.glob("*-contract.md"))))
    edited.write_text(edited.read_text(encoding="utf-8") + "\nmy own note\n", encoding="utf-8")
    mine = ready / "my-notes.md"
    mine.write_text("mine\n", encoding="utf-8")
    kept = {edited: edited.read_bytes(), mine: mine.read_bytes()}

    _run_uninstall(_ns(tmp_path, client))

    assert {path: path.read_bytes() for path in kept if path.exists()} == kept
    assert sorted(p.name for p in ready.iterdir()) == sorted([edited.name, mine.name])


def test_codex_ships_no_directory_for_a_folded_phase(tmp_path: Path) -> None:
    skills = _installed(tmp_path, "codex")

    for phase in PRD_READY_CONTRACTS["codex"]:
        assert not (skills / phase).exists(), phase
