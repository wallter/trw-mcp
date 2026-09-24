"""PRD-INFRA-190 FR02/FR03: ``update-project --dry-run`` reports exactly what the real run changes.

Every run here happens OUT OF PROCESS, through the same ``update_project`` entry
point the CLI calls. The in-process test harness reroutes ``resolve_trw_dir`` to
the fixture root, which would let a dry run's scratch writes land in the real
tree unseen; a subprocess has no harness, so "the dry run leaves the fixture
byte-identical" is measured against production path resolution.

Each scenario builds one committed project, copies it twice, runs the dry run on
one copy and the real run on the other, and compares three sets that must agree
path for path:

- the paths the dry run reported (``updated`` + ``created`` + ``cleaned``);
- the paths the real run reported;
- the paths whose bytes, mode or existence actually changed in the real run,
  measured by an independent whole-tree walk (the oracle).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from trw_mcp.bootstrap._update_transaction import _is_surface_path
from trw_mcp.bootstrap._utils import _DATA_DIR

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(600)]

_RUNNER = """
import json, sys
from pathlib import Path
from trw_mcp.bootstrap import init_project, update_project
target, mode, data_dir = Path(sys.argv[1]), sys.argv[2], sys.argv[3] or None
if mode == "init":
    result = init_project(target, ide="claude-code")
    copilot = init_project(target, ide="copilot")
    result["errors"] += copilot["errors"]
else:
    result = update_project(target, dry_run=mode == "dry", data_dir=Path(data_dir) if data_dir else None)
print(json.dumps(result))
"""

#: Runtime state outside the managed surface that a real update may touch (the
#: memory store, analytics, logs, the post-commit hook, the instruction-write
#: guard's pre-write backups). Anything else changing outside the surface is a
#: write the report cannot name.
_RUNTIME_PREFIXES = (
    ".trw/backups/",
    ".trw/memory/",
    ".trw/context/",
    ".trw/logs/",
    ".trw/hooks/",
    ".trw/security/",
    ".trw/frameworks/.rollback/",
)


def _run(target: Path, mode: str, home: Path, data_dir: Path | None = None) -> dict[str, list[str]]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_")}
    # Every run sees one user environment. XDG_DATA_HOME locates the memory
    # daemon the instruction render counts from, and the function-scoped
    # isolation fixtures point it at a new directory per test, after the
    # module-scoped fixture settled the project under another one.
    env.update(HOME=str(home), XDG_DATA_HOME=str(home / ".local" / "share"), TRW_EMBEDDINGS_ENABLED="false")
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, str(target), mode, str(data_dir or "")],
        capture_output=True,
        text=True,
        env=env,
        cwd=target,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    result: dict[str, list[str]] = json.loads(proc.stdout.strip().splitlines()[-1])
    assert not result["errors"], result["errors"]
    return result


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True
    )


def _tree(root: Path) -> dict[str, tuple[str, int]]:
    """``{path: (sha256, mode)}`` for every file outside ``.git`` — the oracle's own walk."""
    state: dict[str, tuple[str, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            rel = path.relative_to(root).as_posix()
            state[rel] = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o777)
    return state


def _changed(before: dict[str, tuple[str, int]], after: dict[str, tuple[str, int]]) -> set[str]:
    return {rel for rel in before.keys() | after.keys() if before.get(rel) != after.get(rel)}


def _reported(result: dict[str, list[str]]) -> set[str]:
    return {*result["updated"], *result["created"], *result["cleaned"]}


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("determinism")
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def committed_project(workspace: Path) -> Path:
    """An installed, settled, fully committed project (claude-code + copilot)."""
    home = workspace / "home"
    home.mkdir()
    project = workspace / "base"
    project.mkdir()
    _git(project, "init", "-q")
    _run(project, "init", home)
    # Two installs then updates converge in two runs (the first creates the
    # memory store the instruction render counts from and records the second
    # client); settle them so every scenario starts from a steady state.
    for _ in range(2):
        _run(project, "real", home)
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "installed", "--allow-empty")
    return project


def _dry_equals_real(
    workspace: Path, base: Path, name: str, data_dir: Path | None = None
) -> tuple[set[str], dict[str, list[str]]]:
    """Run both modes on identical copies; assert the three sets agree; return the set."""
    home = workspace / "home"
    dry_copy = workspace / f"{name}-dry"
    real_copy = workspace / f"{name}-real"
    shutil.copytree(base, dry_copy, symlinks=True)
    shutil.copytree(base, real_copy, symlinks=True)

    before = _tree(dry_copy)
    dry = _run(dry_copy, "dry", home, data_dir)
    assert _tree(dry_copy) == before, f"the dry run wrote to the target: {sorted(_changed(before, _tree(dry_copy)))}"

    real_before = _tree(real_copy)
    real = _run(real_copy, "real", home, data_dir)
    actual = _changed(real_before, _tree(real_copy))
    unreported = sorted(p for p in actual if not _is_surface_path(p) and not p.startswith(_RUNTIME_PREFIXES))
    assert unreported == [], f"the real run changed files outside the reported surface: {unreported}"

    assert _reported(dry) == _reported(real), (sorted(_reported(dry)), sorted(_reported(real)))
    assert _reported(real) == {p for p in actual if _is_surface_path(p)}
    return _reported(real), real


def _bundle_copy(workspace: Path, name: str) -> Path:
    bundle = workspace / f"{name}-bundle"
    shutil.copytree(_DATA_DIR, bundle)
    return bundle


def test_scenario_1_no_bundle_change_is_a_noop(workspace: Path, committed_project: Path) -> None:
    """Also the FR03 idempotence case: nothing changed, so nothing is written."""
    changed, _ = _dry_equals_real(workspace, committed_project, "noop")
    assert changed == set()


def test_scenario_2_changed_hook_and_agent_in_a_data_dir_override(workspace: Path, committed_project: Path) -> None:
    bundle = _bundle_copy(workspace, "changed")
    hook = bundle / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# bundle N+1\n", encoding="utf-8")
    agent = bundle / "agents" / "trw-reviewer.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "\nBundle N+1 guidance.\n", encoding="utf-8")

    changed, _ = _dry_equals_real(workspace, committed_project, "changed", bundle)

    assert {".claude/hooks/session-start.sh", ".claude/agents/trw-reviewer.md"} <= changed


def test_scenario_3_uncommitted_user_edit_survives_a_changed_bundle(workspace: Path, committed_project: Path) -> None:
    """FR04 inside the determinism contract: the preserved file is in neither report."""
    project = workspace / "edited-base"
    shutil.copytree(committed_project, project, symlinks=True)
    agent = project / ".claude" / "agents" / "trw-reviewer.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "\n<!-- my uncommitted note -->\n", encoding="utf-8")
    bundle = _bundle_copy(workspace, "edited")
    bundled = bundle / "agents" / "trw-reviewer.md"
    bundled.write_text(bundled.read_text(encoding="utf-8") + "\nBundle N+1 guidance.\n", encoding="utf-8")

    changed, real = _dry_equals_real(workspace, project, "edited", bundle)

    assert ".claude/agents/trw-reviewer.md" not in changed
    assert any(entry.startswith(".claude/agents/trw-reviewer.md") for entry in real["preserved"])


def test_scenario_4_client_hook_json_with_unsorted_keys(workspace: Path, committed_project: Path) -> None:
    project = workspace / "unsorted-base"
    shutil.copytree(committed_project, project, symlinks=True)
    hooks_json = project / ".github" / "hooks" / "hooks.json"
    assert hooks_json.is_file(), "fixture: copilot must install .github/hooks/hooks.json"
    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    reordered = dict(reversed(list(data.items())))
    hooks_json.write_text(json.dumps(reordered, indent=2) + "\n", encoding="utf-8")
    _git(project, "commit", "-qam", "reorder hooks.json keys")

    _dry_equals_real(workspace, project, "unsorted")


def test_second_real_run_changes_nothing(workspace: Path, committed_project: Path) -> None:
    """FR03: after a real run lands, the next run writes no file (bytes and modes)."""
    project = workspace / "second-run"
    shutil.copytree(committed_project, project, symlinks=True)
    bundle = _bundle_copy(workspace, "second")
    hook = bundle / "hooks" / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# bundle N+1\n", encoding="utf-8")
    home = workspace / "home"
    first = _run(project, "real", home, bundle)
    assert ".claude/hooks/session-start.sh" in first["updated"]
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "first update")

    before = _tree(project)
    second = _run(project, "real", home, bundle)

    assert _reported(second) == set()
    assert {p for p in _changed(before, _tree(project)) if _is_surface_path(p)} == set()


def test_scenario_5_symlinked_destinations_are_never_written_through(workspace: Path, committed_project: Path) -> None:
    """Codex P1: the dry run's scratch copy kept the link, so the settings merge wrote its real target.

    Both modes park every surface symlink: an external target and an in-project
    target keep their bytes, and both reports agree the link was preserved.
    """
    external = workspace / "outside-settings.json"
    external.write_text("{}\n", encoding="utf-8")
    project = workspace / "linked-base"
    shutil.copytree(committed_project, project, symlinks=True)
    settings = project / ".claude" / "settings.json"
    settings.unlink()
    settings.symlink_to(external)
    shared = project / "shared" / "copilot-instructions.md"
    shared.parent.mkdir()
    shared.write_text("user-owned\n", encoding="utf-8")
    instructions = project / ".github" / "copilot-instructions.md"
    instructions.unlink(missing_ok=True)
    instructions.symlink_to(Path("..") / "shared" / "copilot-instructions.md")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "symlinked destinations")

    changed, real = _dry_equals_real(workspace, project, "linked")

    assert external.read_bytes() == b"{}\n"
    assert (workspace / "linked-real" / "shared" / "copilot-instructions.md").read_bytes() == b"user-owned\n"
    assert (workspace / "linked-real" / ".claude" / "settings.json").is_symlink()
    assert {".claude/settings.json", ".github/copilot-instructions.md"}.isdisjoint(changed)
    assert ".claude/settings.json (symlink)" in real["preserved"]
