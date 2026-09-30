"""E2E-UNINSTALL-CODEX (INC-012): init --ide codex, then uninstall, leaves no reference to a removed file.

Observed on trunk: "Removed 42 items", yet ``.codex/config.toml`` kept ``model_instructions_file`` naming the
deleted ``INSTRUCTIONS.md``, ``[skills]`` paths into the deleted ``.agents/skills``, and ``.codex/hooks.json``
kept hooks running deleted scripts. Every path either file still names must exist after uninstall.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pytest
import tomllib

from trw_mcp.bootstrap import init_project
from trw_mcp.server._subcommands import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


def _ns(project: Path) -> argparse.Namespace:
    return argparse.Namespace(
        target_dir=str(project), dry_run=False, yes=True, user_tier=False, keep_memory=False, ide="codex"
    )


def _codex_project(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="codex")
    assert not result["errors"], result["errors"]
    assert (tmp_path / ".codex" / "config.toml").is_file(), "precondition: codex config written"
    return tmp_path


def _toml_paths(config: dict[str, object]) -> list[str]:
    """Every project-relative file path the codex config names (instructions file and skill paths)."""
    found: list[str] = []
    instructions = config.get("model_instructions_file")
    if isinstance(instructions, str):
        found.append(instructions)
    skills = config.get("skills")
    if isinstance(skills, dict):
        for entry in skills.get("config", []) if isinstance(skills.get("config"), list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                found.append(entry["path"])
    return found


def _hook_scripts(hooks: object) -> list[str]:
    """Every ``.codex/…`` / ``.claude/…`` / ``.trw/…`` script path a hook command names."""
    text = json.dumps(hooks)
    return re.findall(r"(?:\$CODEX_PROJECT_DIR/|\./)?(\.(?:codex|claude|trw|agents)/[\w./-]+)", text)


def test_uninstall_leaves_no_codex_reference_to_a_removed_file(tmp_path: Path) -> None:
    project = _codex_project(tmp_path)

    _run_uninstall(_ns(project))

    dangling: list[str] = []
    config_path = project / ".codex" / "config.toml"
    if config_path.is_file():
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        servers = config.get("mcp_servers")
        if isinstance(servers, dict) and "trw" in servers:
            dangling.append("config.toml: mcp_servers.trw")
        for rel in _toml_paths(config):
            path = Path(rel) if Path(rel).is_absolute() else project / rel
            if not path.exists():
                dangling.append(f"config.toml -> {rel}")
    hooks_path = project / ".codex" / "hooks.json"
    if hooks_path.is_file():
        for rel in _hook_scripts(json.loads(hooks_path.read_text(encoding="utf-8"))):
            if not (project / rel).exists():
                dangling.append(f"hooks.json -> {rel}")
    assert not dangling, dangling


def test_uninstall_keeps_the_users_own_codex_settings(tmp_path: Path) -> None:
    """The inverse merge withdraws only TRW's values: the user's server, skill, key and features stay."""
    (tmp_path / ".git").mkdir()
    codex = tmp_path / ".codex"
    codex.mkdir()
    (codex / "config.toml").write_text(
        'model = "o3"\n\n[features]\nweb_search = true\n\n[mcp_servers.mine]\ncommand = "my-server"\n\n'
        '[[skills.config]]\npath = ".agents/skills/mine"\nenabled = true\n',
        encoding="utf-8",
    )
    assert not init_project(tmp_path, ide="codex")["errors"]

    _run_uninstall(_ns(tmp_path))

    config = tomllib.loads((codex / "config.toml").read_text(encoding="utf-8"))
    assert config.get("model") == "o3"
    assert config.get("features") == {"web_search": True}
    assert config.get("mcp_servers") == {"mine": {"command": "my-server"}}
    assert config.get("skills") == {"config": [{"path": ".agents/skills/mine", "enabled": True}]}
    assert "model_instructions_file" not in config


def _append_to_user_region(config: Path, text: str) -> None:
    from trw_mcp.bootstrap._codex_toml import USER_REGION_END

    raw = config.read_text(encoding="utf-8")
    config.write_text(raw.replace(USER_REGION_END, text.strip("\n") + "\n" + USER_REGION_END, 1), encoding="utf-8")


def test_a_managed_table_the_user_added_below_the_marker_survives_once(tmp_path: Path) -> None:
    """Lead review: a user-region ``[mcp_servers.mine]`` must not be re-emitted, or codex cannot parse the file."""
    project = _codex_project(tmp_path)
    config = project / ".codex" / "config.toml"
    _append_to_user_region(config, '[mcp_servers.mine]\ncommand = "my-server"\n')

    _run_uninstall(_ns(project))

    text = config.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)  # a duplicated table raises here
    assert parsed["mcp_servers"] == {"mine": {"command": "my-server"}}
    assert text.count("[mcp_servers.mine]") == 1


def test_a_bare_managed_key_the_user_set_below_the_marker_is_theirs(tmp_path: Path) -> None:
    """The user's own ``model_instructions_file`` in their region stays; TRW's value in the managed block goes."""
    project = _codex_project(tmp_path)
    config = project / ".codex" / "config.toml"
    _append_to_user_region(config, 'model_instructions_file = "MY-RULES.md"\n')

    _run_uninstall(_ns(project))

    text = config.read_text(encoding="utf-8")
    assert tomllib.loads(text)["model_instructions_file"] == "MY-RULES.md"
    assert text.count("model_instructions_file") == 1
