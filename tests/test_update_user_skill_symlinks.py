"""A third-party skills installer's symlinks never block update-project (feedback sub_2K6zdxKjDwf6VBkr).

Such an installer keeps skills canonically in ``.agents/skills/<name>`` and symlinks them into each client's
skills directory. A non-TRW-named link under ANY client's skills dir is the user's, exactly as under
``.claude/skills``; a link at a TRW skill name, or a symlinked managed container, is still refused.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.bootstrap._managed_dirs import _TRANSACTION_DIRS, _is_user_skill_link, _managed_tables
from trw_mcp.bootstrap._update_transaction import _validate_transaction_surface

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

#: Every client skills directory a third-party skills installer links into.
_CLIENT_SKILL_DIRS = tuple(f"{root}/skills" for root in _TRANSACTION_DIRS if root.startswith(".") and "/" not in root)
_REPORTER_DIRS = (".claude/skills", ".grok/skills", ".codex/skills", ".github/skills")


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, capture_output=True)
    assert not init_project(root, ide="claude-code")["errors"]
    return root


def _canonical_skill(root: Path, name: str) -> Path:
    skill = root / ".agents" / "skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return skill


def _link(root: Path, skills_dir: str, name: str, target: Path) -> Path:
    link = root / skills_dir / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link


def _trw_skill_name() -> str:
    from trw_mcp.bootstrap._client_skills import canonical_skills_dir

    return sorted(d.name for d in canonical_skills_dir().iterdir() if d.is_dir() and d.name.startswith("trw-"))[0]


def test_the_reporters_layout_updates_cleanly(tmp_path: Path) -> None:
    """21 skills kept in .agents/skills, symlinked into the Claude, Grok, Codex and Copilot skills dirs."""
    root = _project(tmp_path)
    targets: dict[Path, tuple[str, bytes]] = {}
    for i in range(21):
        target = _canonical_skill(root, f"third-party-{i}")
        for skills_dir in _REPORTER_DIRS:
            link = _link(root, skills_dir, f"third-party-{i}", target)
            targets[link] = (os.readlink(link), (target / "SKILL.md").read_bytes())
    # an update must still do its work: the merged settings file, deleted here, is written again
    settings = root / ".claude" / "settings.json"
    settings.unlink()

    result = update_project(root, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert settings.is_file()  # the managed-file update happened (the project was not refused)
    assert len(targets) == 21 * len(_REPORTER_DIRS)
    for link, (
        target_text,
        content,
    ) in targets.items():  # EVERY link, not one: untouched, never followed, never rewritten
        assert link.is_symlink() and os.readlink(link) == target_text, link
        assert (link / "SKILL.md").read_bytes() == content, link


@pytest.mark.parametrize("skills_dir", _CLIENT_SKILL_DIRS)
def test_a_user_skill_link_is_exempt_under_every_clients_skills_dir(tmp_path: Path, skills_dir: str) -> None:
    root = _project(tmp_path)
    _link(root, skills_dir, "my-own-link", _canonical_skill(root, "my-own-skill"))

    assert _is_user_skill_link(root / skills_dir / "my-own-link", root)
    _validate_transaction_surface(root)  # does not raise


@pytest.mark.parametrize("skills_dir", _CLIENT_SKILL_DIRS)
def test_a_link_at_a_trw_skill_name_is_still_refused(tmp_path: Path, skills_dir: str) -> None:
    root = _project(tmp_path)
    name = _trw_skill_name()
    _link(root, skills_dir, name, _canonical_skill(root, "elsewhere"))

    with pytest.raises(OSError, match="symlinked directory") as caught:
        _validate_transaction_surface(root)

    assert f"{skills_dir}/{name} -> " in str(caught.value)
    assert "Fix:" in str(caught.value)


def test_a_symlinked_managed_skills_container_is_still_refused(tmp_path: Path) -> None:
    root = _project(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    (root / ".grok").mkdir(exist_ok=True)
    (root / ".grok" / "skills").symlink_to(real)

    with pytest.raises(OSError, match="symlinked directory") as caught:
        _validate_transaction_surface(root)

    assert ".grok/skills -> " in str(caught.value)


def test_the_skills_containers_are_derived_not_hand_listed() -> None:
    tables = _managed_tables()
    assert {
        (root, "skills") for root in (".claude", ".grok", ".codex", ".cursor", ".github", ".opencode", ".agents")
    } <= tables.skill_roots
    assert (".trw", "skills") not in tables.skill_roots


def test_the_refusal_names_every_client_whose_surface_holds_a_blocking_path(tmp_path: Path) -> None:
    """Feedback sub_2K6zdxKjDwf6VBkr: the message named only the client whose call failed."""
    root = _project(tmp_path)
    name = _trw_skill_name()
    target = _canonical_skill(root, "elsewhere")
    _link(root, ".grok/skills", name, target)
    _link(root, ".claude/skills", name, target)

    with pytest.raises(OSError) as caught:
        _validate_transaction_surface(root)

    message = str(caught.value)
    assert "Clients affected:" in message
    affected = next(ln for ln in message.splitlines() if ln.startswith("Clients affected:"))
    assert "Grok Build CLI" in affected and "Claude Code" in affected
    assert message.index("Clients affected:") < message.index("Fix:")  # the Fix line stays last


def test_a_blocking_path_under_no_client_adds_no_clients_line(tmp_path: Path) -> None:
    root = _project(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    runtime = root / ".trw" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(root / ".trw" / "runtime" / "hook-env.d", ignore_errors=True)
    (root / ".trw" / "runtime" / "hook-env.d").symlink_to(real, target_is_directory=True)

    with pytest.raises(OSError) as caught:
        _validate_transaction_surface(root)

    assert "Clients affected:" not in str(caught.value)


def _affected(root: Path) -> set[str]:
    with pytest.raises(OSError) as caught:
        _validate_transaction_surface(root)
    line = next(ln for ln in str(caught.value).splitlines() if ln.startswith("Clients affected:"))
    return {part.rsplit(" (", 1)[0].strip() for part in line.removeprefix("Clients affected:").split("), ")}


def _name(client: str) -> str:
    from trw_mcp.models.config._profiles import resolve_client_profile

    return resolve_client_profile(client).display_name


@pytest.mark.parametrize(
    ("skills_dir", "expected"),
    [
        (".claude/skills", {"claude-code"}),  # Codex and Copilot also register something under .claude, not this
        (".agents/skills", {"codex"}),  # Antigravity registers .agents/agents, not .agents/skills
        (".github/skills", {"copilot"}),
        (".grok/skills", {"grok"}),  # no registered surface: the clients in the same top-level dir
    ],
)
def test_a_refused_skill_names_exactly_the_clients_that_own_its_surface(
    tmp_path: Path, skills_dir: str, expected: set[str]
) -> None:
    root = _project(tmp_path)
    _link(root, skills_dir, _trw_skill_name(), _canonical_skill(root, "elsewhere"))

    assert _affected(root) == {_name(c) for c in expected}


def test_a_symlinked_ancestor_names_every_client_with_a_surface_below_it(tmp_path: Path) -> None:
    root = _project(tmp_path)
    real = tmp_path / "elsewhere"
    real.mkdir()
    shutil.rmtree(root / ".claude")
    (root / ".claude").symlink_to(real, target_is_directory=True)

    assert _affected(root) == {_name(c) for c in ("claude-code", "codex", "copilot")}
