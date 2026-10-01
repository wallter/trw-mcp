"""INC-117 (swarm-e2e S13-A1): uninstall leaves no empty or stripped config shell TRW created itself.

``uninstall(init(profile))`` on a clean project used to leave ``.cursor/mcp.json = {}``, the copilot
``.github/mcp.json`` / ``.vscode/mcp.json``, ``.claude/settings.json`` and ``.mcp.json`` as ``{}``,
``.grok/config.toml`` as an empty ``[mcp_servers]`` header, and ``opencode.json`` still holding the keys a
fresh install seeds. TRW's own entries were gone; the file was only a husk. A file that holds anything the
user owns keeps it, and only TRW's entries leave (HB-2 for the product).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
from pathlib import Path

import pytest

from tests._fs_hazards import atomic_replace, race_after
from trw_mcp.bootstrap import init_project, update_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_SHELL_PROFILES = ("antigravity-cli", "claude-code", "codex", "copilot", "cursor-cli", "cursor-ide", "grok")
_ALL_PROFILES = (*_SHELL_PROFILES, "opencode")  # opencode keeps the keys its fresh install seeds (see its own tests)


@pytest.fixture
def distill_entitled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the licence gate so init also writes the distill-dependent files (the harsher uninstall case)."""
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)


def _ns(project: Path, *, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=dry_run, yes=True, user_tier=False, keep_memory=False, ide=None
    )


def _project(tmp_path: Path, ide: str) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert not init_project(tmp_path, ide=ide)["errors"]
    return tmp_path


def _left(project: Path) -> list[str]:
    """Every file outside ``.git`` and ``.trw`` (TRW's own state, handled by its own removal step)."""
    return sorted(
        str(p.relative_to(project))
        for p in project.rglob("*")
        if p.is_file() and not {".git", ".trw"} & set(p.relative_to(project).parts)
    )


@pytest.mark.parametrize("ide", _SHELL_PROFILES)
@pytest.mark.usefixtures("distill_entitled")
def test_uninstall_after_a_clean_init_leaves_no_file_behind(tmp_path: Path, ide: str) -> None:
    project = _project(tmp_path, ide)

    _run_uninstall(_ns(project))

    assert _left(project) == []


def _rewrite(path: Path, edit: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)  # type: ignore[operator]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("ide", "rel", "container"),
    [
        ("cursor-ide", ".cursor/mcp.json", "mcpServers"),
        ("copilot", ".vscode/mcp.json", "servers"),
        ("copilot", ".github/mcp.json", "mcpServers"),
        ("claude-code", ".mcp.json", "mcpServers"),
        ("opencode", "opencode.json", "mcp"),
    ],
)
def test_a_user_server_beside_trws_keeps_the_file_and_only_trws_leaves(
    tmp_path: Path, ide: str, rel: str, container: str
) -> None:
    project = _project(tmp_path, ide)
    mine = {"command": "my-mcp", "args": ["--x"]}
    _rewrite(project / rel, lambda data: data[container].update({"mine": mine}))

    _run_uninstall(_ns(project))

    assert json.loads((project / rel).read_text(encoding="utf-8"))[container] == {"mine": mine}


def test_opencode_json_the_user_added_a_key_to_keeps_that_key(tmp_path: Path) -> None:
    project = _project(tmp_path, "opencode")
    _rewrite(project / "opencode.json", lambda data: data.update({"model": "my/model"}))

    _run_uninstall(_ns(project))

    kept = json.loads((project / "opencode.json").read_text(encoding="utf-8"))
    assert kept["model"] == "my/model"
    assert "mcp" not in kept
    assert ".opencode/INSTRUCTIONS.md" not in kept.get("instructions", [])


_OPENCODE_SEED = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {"bash": "ask", "write": "ask", "edit": "ask"},
    "tools": {"trw*": True},
}


def test_opencode_json_keeps_the_keys_a_fresh_install_seeds_because_their_origin_is_unprovable(
    tmp_path: Path,
) -> None:
    """Only TRW's own ``mcp`` server and ``instructions`` entry leave; ``$schema``/``permission``/``tools`` stay."""
    project = _project(tmp_path, "opencode")

    _run_uninstall(_ns(project))

    assert json.loads((project / "opencode.json").read_text(encoding="utf-8")) == _OPENCODE_SEED


def test_a_preexisting_opencode_json_shaped_like_the_seed_survives_init_and_uninstall(tmp_path: Path) -> None:
    """Review round 1 (P0, HB-2): a user's own file with exactly the seed keys must never be deleted as "TRW's shell"."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "opencode.json").write_text(json.dumps(_OPENCODE_SEED, indent=2) + "\n", encoding="utf-8")
    assert not init_project(tmp_path, ide="opencode")["errors"]

    _run_uninstall(_ns(tmp_path))

    assert json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8")) == _OPENCODE_SEED


def test_opencode_json_with_a_changed_permission_is_the_users_and_is_kept(tmp_path: Path) -> None:
    project = _project(tmp_path, "opencode")
    _rewrite(project / "opencode.json", lambda data: data["permission"].update({"bash": "allow"}))

    _run_uninstall(_ns(project))

    assert json.loads((project / "opencode.json").read_text(encoding="utf-8"))["permission"]["bash"] == "allow"


def test_a_user_table_in_grok_config_toml_keeps_the_file(tmp_path: Path) -> None:
    project = _project(tmp_path, "grok")
    config = project / ".grok" / "config.toml"
    config.write_text(config.read_text(encoding="utf-8") + '\n[model]\nname = "mine"\n', encoding="utf-8")

    _run_uninstall(_ns(project))

    text = config.read_text(encoding="utf-8")
    assert 'name = "mine"' in text
    assert "mcp_servers.trw" not in text


def test_claude_settings_with_a_user_permission_keeps_it(tmp_path: Path) -> None:
    project = _project(tmp_path, "claude-code")
    _rewrite(project / ".claude" / "settings.json", lambda data: data.update({"permissions": {"allow": ["Bash(ls)"]}}))

    _run_uninstall(_ns(project))

    kept = json.loads((project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert kept == {"permissions": {"allow": ["Bash(ls)"]}}


def test_a_dry_run_changes_no_shell(tmp_path: Path) -> None:
    project = _project(tmp_path, "cursor-ide")
    before = (project / ".cursor" / "mcp.json").read_bytes()

    _run_uninstall(_ns(project, dry_run=True))

    assert (project / ".cursor" / "mcp.json").read_bytes() == before


def test_an_edit_saved_after_uninstall_read_the_shell_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The act-time rule: the delete re-proves the bytes it judged, so a user's save in between is kept."""
    project = _project(tmp_path, "cursor-ide")
    mcp = project / ".cursor" / "mcp.json"
    mine = json.dumps({"mcpServers": {"mine": {"command": "my-mcp"}}}, indent=2).encode() + b"\n"
    probe = race_after(
        monkeypatch, target=mcp, op="read_bytes", when="after", interloper=lambda: atomic_replace(mcp, mine)
    )

    with contextlib.suppress(SystemExit):  # an edit seen at the act may report partial; the bytes are the assertion
        _run_uninstall(_ns(project))

    assert probe.fired
    assert mcp.read_bytes() == mine


_EXPLORER = Path(".agents") / "agents" / "trw-distill-explorer.md"


@pytest.mark.usefixtures("distill_entitled")
def test_the_antigravity_explorer_trw_rendered_is_removed_with_its_install(tmp_path: Path) -> None:
    """The explorer is rendered from the distill sidecar, so only the channel state can prove its bytes are TRW's."""
    project = _project(tmp_path, "antigravity-cli")
    assert (project / _EXPLORER).is_file(), "precondition: init wrote the explorer"

    _run_uninstall(_ns(project))

    assert not (project / _EXPLORER).exists()


@pytest.mark.usefixtures("distill_entitled")
def test_an_antigravity_explorer_the_user_edited_is_kept(tmp_path: Path) -> None:
    project = _project(tmp_path, "antigravity-cli")
    explorer = project / _EXPLORER
    explorer.write_text(explorer.read_text(encoding="utf-8") + "\nMy own instruction.\n", encoding="utf-8")

    _run_uninstall(_ns(project))

    assert "My own instruction." in explorer.read_text(encoding="utf-8")


@pytest.mark.usefixtures("distill_entitled")
def test_the_antigravity_explorer_is_still_proven_after_an_update_project(tmp_path: Path) -> None:
    project = _project(tmp_path, "antigravity-cli")
    assert not update_project(project, ide="antigravity-cli")["errors"]
    assert (project / _EXPLORER).is_file(), "precondition: the explorer survived the update"
    assert not (project / ".trw" / "trash").exists(), "an update must not remove and rewrite the recorded explorer"

    _run_uninstall(_ns(project))

    assert not (project / _EXPLORER).exists()


@pytest.mark.parametrize("ide", _ALL_PROFILES)
def test_no_file_init_wrote_under_dot_trw_is_called_not_created_by_trw(tmp_path: Path, ide: str) -> None:
    """INC-117(f): opencode's ``.trw/client-profile.env`` was listed as a file TRW did not create."""
    from trw_mcp.server._uninstall_corpus import untracked_trw_entries

    project = _project(tmp_path, ide)

    assert untracked_trw_entries(project / ".trw") == []


def test_a_scoped_removal_leaves_none_of_that_clients_empty_directories_and_none_of_the_shared_scaffold(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "cursor-ide")
    (project / "docs").mkdir(exist_ok=True)

    _run_uninstall(
        argparse.Namespace(
            target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide="cursor-ide"
        )
    )

    assert not (project / ".cursor").exists(), "an emptied client directory was left behind"
    assert (project / "docs").is_dir(), "a scoped removal must not prune the shared scaffold"


@pytest.mark.skipif(not hasattr(__import__("os"), "mkfifo"), reason="no FIFOs on this platform (codex r2 KI)")
def test_a_fifo_at_the_explorer_state_path_neither_stalls_nor_records(tmp_path: Path) -> None:
    """codex r1 KI: the channel state is read only when it is a regular file; a FIFO would block the install."""
    import os
    import threading

    from trw_mcp.bootstrap._managed_client_artifacts import _antigravity_explorer_manifest_hash
    from trw_mcp.channels._state import state_path_for
    from trw_mcp.channels.antigravity._explorer_subagent import AG02_CHANNEL_ID

    explorer = tmp_path / _EXPLORER
    explorer.parent.mkdir(parents=True)
    explorer.write_text("rendered\n", encoding="utf-8")
    state = state_path_for(AG02_CHANNEL_ID, tmp_path / ".trw" / "channels")
    state.parent.mkdir(parents=True, exist_ok=True)
    os.mkfifo(state)
    got: list[dict[str, str]] = []
    worker = threading.Thread(
        target=lambda: got.append(_antigravity_explorer_manifest_hash(tmp_path, None)), daemon=True
    )
    worker.start()
    worker.join(10)
    assert not worker.is_alive() and got == [{}]
