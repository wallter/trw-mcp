"""PRD-CORE-337 FR06 -- the bootstrap generators refuse a planted symlink instead of writing through it.

Every case plants a symlink inside a fresh project, pointing OUTSIDE it, and then runs one generator the way
``init-project``/``update`` runs it. A *leaf* case links the generator's target file to a sentinel file; a
*parent* case links a directory above the target to an empty outside directory. Before PRD-CORE-337 each
generator wrote through the link (the sentinel's bytes changed, or a file appeared outside the project).
Now the write is refused: the sentinel is untouched, the planted link is still a link, and the refusal
surfaces through the generator's existing error contract -- its reported ``errors``/``warnings``, or the
typed ``UnsafeWriteError`` for a generator that never caught write failures.

Scope: this proves each migrated FR06 site refuses; it does not prove output bytes are unchanged for a
clean project -- ``test_checkout_write.py`` pins the adapter's bytes and mode, and the generators' own
suites pin their content.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from trw_memory.safe_fs import UnsafeWriteError

from trw_mcp.bootstrap._claude_code_distill_channels import _install_hook
from trw_mcp.bootstrap._client_skills import skill_files, skill_names
from trw_mcp.bootstrap._codex import generate_codex_config, install_codex_skills
from trw_mcp.bootstrap._codex_distill_channels import merge_distill_hook_into_hooks_json
from trw_mcp.bootstrap._codex_hooks import generate_codex_hooks
from trw_mcp.bootstrap._copilot import generate_copilot_hooks
from trw_mcp.bootstrap._copilot_artifacts import (
    copilot_path_instruction_contents,
    copilot_skill_contents,
    generate_copilot_path_instructions,
    install_copilot_skills,
)
from trw_mcp.bootstrap._copilot_distill_channels import _C5_HOOK_NAMES, _install_c5_hook
from trw_mcp.bootstrap._cursor import (
    generate_cursor_mcp_config,
    generate_cursor_rules_mdc,
    generate_cursor_skills_mirror,
)
from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config
from trw_mcp.bootstrap._cursor_ide import (
    _IDE_HOOK_SCRIPTS,
    cursor_ide_command_contents,
    generate_cursor_ide_commands,
    generate_cursor_ide_hooks,
)
from trw_mcp.bootstrap._file_ops import read_settings_for_merge
from trw_mcp.bootstrap._gitignore_merge import _ensure_credentials_gitignored
from trw_mcp.bootstrap._grok import generate_grok_config
from trw_mcp.bootstrap._ide_targets_finalize import _update_config_target_platforms
from trw_mcp.bootstrap._init_project import _write_ceremony_state_skeleton, _write_initial_config
from trw_mcp.bootstrap._mcp_json import _merge_mcp_json
from trw_mcp.bootstrap._opencode import generate_opencode_config, install_opencode_commands
from trw_mcp.bootstrap._opencode_distill_channels import install_opencode_distill_channels
from trw_mcp.bootstrap._update_project import _generate_behavioral_protocol_md
from trw_mcp.bootstrap._user_file_edit import atomic_write_text

#: Valid TOML and invalid JSON, so a TOML merge proceeds to its write and a JSON merge falls back to a fresh one.
_SENTINEL = b"x = 1\n"


def _result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "skipped": [], "errors": [], "warnings": []}


def _run(call: Callable[[Path], object], project: Path) -> str:
    """The generator's outcome as text: its result, or the refusal it raised."""
    try:
        return repr(call(project))
    except UnsafeWriteError as refused:
        return f"raised {refused.reason}: {refused}"


def _codex_skill_file() -> str:
    name = skill_names("codex")[0]
    filename = next(iter(skill_files("codex", name)))[0]
    return f".agents/skills/{name}/{filename}"


def _settings_backup(project: Path) -> object:
    (project / "settings.json").write_text("{not json", encoding="utf-8")
    result = _result()
    read_settings_for_merge(project / "settings.json", rel_path="settings.json", result=result)
    return result


def _with_result(call: Callable[[Path, dict[str, list[str]]], object]) -> Callable[[Path], object]:
    def run(project: Path) -> object:
        result = _result()
        call(project, result)
        return result

    return run


_LEAF_CASES = [
    pytest.param(".cursor/mcp.json", generate_cursor_mcp_config, "symlink_leaf", id="cursor-mcp"),
    pytest.param(
        ".cursor/rules/trw-ceremony.mdc",
        lambda p: generate_cursor_rules_mdc(p, "body"),
        "symlink_leaf",
        id="cursor-rules",
    ),
    pytest.param(".codex/config.toml", generate_codex_config, "symlink_leaf", id="codex-config"),
    pytest.param(_codex_skill_file(), lambda p: install_codex_skills(p, force=True), "symlink_leaf", id="codex-skills"),
    pytest.param(".codex/hooks.json", lambda p: generate_codex_hooks(p, force=True), "symlink_leaf", id="codex-hooks"),
    pytest.param(
        ".github/hooks/hooks.json", lambda p: generate_copilot_hooks(p, force=True), "symlink_leaf", id="copilot-hooks"
    ),
    pytest.param(".grok/config.toml", generate_grok_config, "symlink_leaf", id="grok"),
    pytest.param(".mcp.json", _with_result(_merge_mcp_json), "symlink_leaf", id="mcp-json"),
    pytest.param("opencode.json", lambda p: generate_opencode_config(p, force=True), "symlink_leaf", id="opencode"),
    pytest.param(
        ".opencode/commands/trw-deliver.md",
        lambda p: install_opencode_commands(p, force=True),
        "symlink_leaf",
        id="opencode-rendered",
    ),
    pytest.param(
        ".trw/learnings/index.yaml",
        _with_result(lambda p, r: _write_initial_config(p, True, r)),
        "symlink_leaf",
        id="write-if-missing",
    ),
    pytest.param("settings.json.bak", _settings_backup, "backup to settings.json.bak failed", id="settings-backup"),
    pytest.param(
        ".claude/hooks/pre-tool-distill-hint.sh",
        _with_result(lambda p, r: _install_hook(p, "pre-tool-distill-hint.sh", r)),
        "symlink_leaf",
        id="claude-code-distill-hook",
    ),
    pytest.param(
        f".github/hooks/{_C5_HOOK_NAMES[0]}",
        _with_result(lambda p, r: _install_c5_hook(p, _C5_HOOK_NAMES[0], r)),
        "symlink_leaf",
        id="copilot-c5-hook",
    ),
    pytest.param(".codex/hooks.json", merge_distill_hook_into_hooks_json, "symlink_leaf", id="codex-distill-hooks"),
    pytest.param(
        ".trw/client-profile.env", install_opencode_distill_channels, "symlink_leaf", id="opencode-client-profile"
    ),
    # The LIVE .cursor/hooks.json writer (CURSOR-HOOKS-RAW-WRITE) and the other writers the census had
    # grandfathered as unscheduled-checkout-write.
    pytest.param(".cursor/hooks.json", generate_cursor_ide_hooks, "symlink_leaf", id="cursor-ide-hooks"),
    pytest.param(
        next(iter(cursor_ide_command_contents())),
        lambda p: generate_cursor_ide_commands(p, force=True),
        "symlink_leaf",
        id="cursor-ide-commands",
    ),
    pytest.param(".cursor/cli.json", generate_cursor_cli_config, "could not be written", id="cursor-cli-json"),
    pytest.param(
        next(iter(copilot_path_instruction_contents())),
        lambda p: generate_copilot_path_instructions(p, force=True),
        "symlink_leaf",
        id="copilot-path-instructions",
    ),
    pytest.param(
        next(iter(copilot_skill_contents())),
        lambda p: install_copilot_skills(p, force=True),
        "symlink_leaf",
        id="copilot-skills",
    ),
    pytest.param(".trw/.gitignore", _with_result(_ensure_credentials_gitignored), "symlink_leaf", id="trw-gitignore"),
    pytest.param(
        ".trw/context/behavioral_protocol.md",
        _with_result(_generate_behavioral_protocol_md),
        "symlink_leaf",
        id="behavioral-protocol",
    ),
    pytest.param(
        "edited.json", lambda p: atomic_write_text(p / "edited.json", "{}\n"), "symlink_leaf", id="user-file-edit"
    ),
]


@pytest.mark.parametrize(("rel", "generator", "surfaced"), _LEAF_CASES)
def test_a_planted_leaf_symlink_is_refused_not_followed(
    tmp_path: Path, rel: str, generator: Callable[[Path], object], surfaced: str
) -> None:
    project = tmp_path / "project"
    sentinel = tmp_path / "outside" / "sentinel"
    sentinel.parent.mkdir()
    sentinel.write_bytes(_SENTINEL)
    (project / rel).parent.mkdir(parents=True)
    (project / rel).symlink_to(sentinel)

    outcome = _run(generator, project)

    assert sentinel.read_bytes() == _SENTINEL, f"{rel}: the generator wrote through the planted link"
    assert (project / rel).is_symlink()
    assert surfaced in outcome


def test_a_planted_symlink_at_hooks_json_is_refused_not_followed(tmp_path: Path) -> None:
    """The PRD's named FR06 acceptance case (AK-P06): ``.cursor/hooks.json`` linked out of the project."""
    project = tmp_path / "project"
    (project / ".cursor").mkdir(parents=True)
    sentinel = tmp_path / "sentinel.json"
    sentinel.write_bytes(_SENTINEL)
    (project / ".cursor" / "hooks.json").symlink_to(sentinel)

    with pytest.raises(UnsafeWriteError) as refused:
        generate_cursor_ide_hooks(project)

    assert refused.value.reason == "symlink_leaf"
    assert sentinel.read_bytes() == _SENTINEL


_PARENT_CASES = [
    pytest.param(".codex", generate_codex_config, "symlink_component", id="codex-dir"),
    pytest.param(".cursor", generate_cursor_ide_hooks, "symlink_component", id="cursor-ide-dir"),
    pytest.param(
        ".trw/context", _with_result(_write_ceremony_state_skeleton), "symlink_component", id="trw-context-dir"
    ),
    pytest.param(
        ".cursor/skills/trw-deliver",
        lambda p: generate_cursor_skills_mirror(p, ["trw-deliver"]),
        "preserved",
        id="cursor-skill-dir",
    ),
]


@pytest.mark.parametrize(("rel", "generator", "surfaced"), _PARENT_CASES)
def test_a_planted_parent_symlink_is_refused_not_followed(
    tmp_path: Path, rel: str, generator: Callable[[Path], object], surfaced: str
) -> None:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / rel).parent.mkdir(parents=True)
    (project / rel).symlink_to(outside, target_is_directory=True)

    outcome = _run(generator, project)

    assert [p for p in outside.rglob("*") if p.is_file()] == [], f"{rel}: a file was written outside the project"
    assert (project / rel).is_symlink()
    assert surfaced in outcome


def test_a_symlinked_trw_config_yaml_is_refused_not_followed(tmp_path: Path) -> None:
    """``target_platforms`` rewrite (``_update_config_target_platforms``); a YAML sentinel so it parses and merges."""
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    sentinel = tmp_path / "sentinel.yaml"
    sentinel.write_bytes(b"x: 1\n")
    (project / ".trw" / "config.yaml").symlink_to(sentinel)
    result = _result()

    _update_config_target_platforms(project, ["cursor-ide"], result)

    assert sentinel.read_bytes() == b"x: 1\n"
    assert "symlink_leaf" in " ".join(result["warnings"])


def test_a_symlinked_cursor_hook_script_is_refused_not_followed(tmp_path: Path) -> None:
    """The hook scripts were a ``shutil.copy2``, which writes through a symlinked destination."""
    project = tmp_path / "project"
    (project / ".cursor" / "hooks").mkdir(parents=True)
    sentinel = tmp_path / "sentinel.sh"
    sentinel.write_bytes(_SENTINEL)
    first = sorted(_IDE_HOOK_SCRIPTS)[0]
    (project / ".cursor" / "hooks" / first).symlink_to(sentinel)

    outcome = _run(lambda p: generate_cursor_ide_hooks(p, force=True), project)

    assert sentinel.read_bytes() == _SENTINEL
    assert "symlink_leaf" in outcome
