"""A hook registration follows its hook file: kept file, kept registration (field report sub_gXdMU8Zw7f)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

SETTINGS = ".claude/settings.json"
HINT = ".claude/hooks/pre-tool-distill-hint.sh"
HINT_CMD = 'sh "$CLAUDE_PROJECT_DIR/.claude/hooks/pre-tool-distill-hint.sh"'


def _git(root: Path, *args: str) -> None:
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "HOME": str(root),
    }
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, timeout=60, env=env)


def _hint_entries(root: Path) -> list[dict[str, object]]:
    hooks = json.loads((root / SETTINGS).read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    return [h for e in hooks for h in e["hooks"] if h.get("command") == HINT_CMD]


def installed_project(tmp_path: Path) -> Path:
    """A committed git project with the CC-03 hint hook enabled, installed and registered."""
    from trw_mcp.bootstrap import init_project, update_project

    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code").get("errors")
    config = root / ".trw/config.yaml"
    config.write_text(config.read_text(encoding="utf-8") + "cc03_hook_enabled: true\n", encoding="utf-8")
    assert not update_project(root).get("errors")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "installed")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return installed_project(tmp_path)


def _outdate(root: Path, *, edit_hook: bool, user_key: bool) -> None:
    """An installed project whose hint registration is stale (timeout 60), optionally a user-edited hook file."""
    path = root / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            if hook.get("command") == HINT_CMD:
                hook["timeout"] = 60
    if user_key:
        data["permissions"] = {"allow": ["Bash(ls)"]}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if edit_hook:
        hook = root / HINT
        hook.write_text(hook.read_text(encoding="utf-8") + "\n# my edit\n", encoding="utf-8")


def test_setup_has_a_hint_registration(project: Path) -> None:
    assert _hint_entries(project), "init registers the CC-03 hint hook"


def test_a_kept_hook_file_keeps_its_registration(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _outdate(project, edit_hook=True, user_key=True)
    result = update_project(project)
    assert not result.get("errors")
    assert "# my edit" in (project / HINT).read_text(encoding="utf-8")
    assert _hint_entries(project)[0]["timeout"] == 60


def _settings_warnings(result: dict[str, list[str]]) -> list[str]:
    return [w for w in result["warnings"] if "left the registration" in w]


def test_the_output_says_the_registration_was_left_and_the_remedy(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _outdate(project, edit_hook=True, user_key=True)
    result = update_project(project)
    assert _settings_warnings(result) == [
        ".claude/settings.json: left the registration of .claude/hooks/pre-tool-distill-hint.sh as it was because "
        "the hook file was kept (for the fresh copy and its new registration, delete the file and run "
        "update-project again)"
    ]
    assert json.loads((project / SETTINGS).read_text(encoding="utf-8"))["permissions"] == {"allow": ["Bash(ls)"]}


def test_the_remedy_works(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _outdate(project, edit_hook=True, user_key=False)
    update_project(project)
    (project / HINT).unlink()
    result = update_project(project)
    assert not _settings_warnings(result)
    assert _hint_entries(project)[0]["timeout"] == 5
    assert "# my edit" not in (project / HINT).read_text(encoding="utf-8")


@pytest.mark.parametrize("committed", [True, False])
def test_an_unedited_hook_file_and_its_registration_both_follow(project: Path, committed: bool) -> None:
    from trw_mcp.bootstrap import update_project

    _outdate(project, edit_hook=False, user_key=True)
    if committed:
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "stale registration")
    result = update_project(project)
    assert not _settings_warnings(result)
    assert _hint_entries(project)[0]["timeout"] == 5


def test_a_clean_tree_keeps_the_edited_hook_registration(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _outdate(project, edit_hook=True, user_key=True)
    _git(project, "add", "-A")  # nothing is dirty: the writers' own decision stands, not the dirty-file restore
    _git(project, "commit", "-qm", "edited hook, stale registration")
    result = update_project(project)
    assert len(_settings_warnings(result)) == 1
    assert _hint_entries(project)[0]["timeout"] == 60


def test_a_settings_file_with_no_trw_registration_still_gets_one(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    path = project / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"] = [
        e for e in data["hooks"]["PreToolUse"] if all(h.get("command") != HINT_CMD for h in e["hooks"])
    ]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    hook = project / HINT
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# my edit\n", encoding="utf-8")
    result = update_project(project)
    assert not _settings_warnings(result)  # nothing existed to leave as it was
    assert _hint_entries(project)[0]["timeout"] == 5


def _session_end_timeout(root: Path) -> object:
    hooks = json.loads((root / SETTINGS).read_text(encoding="utf-8"))["hooks"]["SessionEnd"]
    return next(h["timeout"] for e in hooks for h in e["hooks"] if "session-end.sh" in h["command"])


@pytest.mark.parametrize("edited", [True, False])
def test_a_legacy_millisecond_timeout_migrates_only_with_its_unedited_hook(project: Path, edited: bool) -> None:
    from trw_mcp.bootstrap import update_project

    path = project / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    for e in data["hooks"]["SessionEnd"]:
        for h in e["hooks"]:
            if "session-end.sh" in h["command"]:
                h["timeout"] = 5000
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if edited:
        hook = project / ".claude/hooks/session-end.sh"
        hook.write_text(hook.read_text(encoding="utf-8") + "\n# my edit\n", encoding="utf-8")
    _git(project, "add", "-A")  # a git-dirty settings.json is put back whole, migration included
    _git(project, "commit", "-qm", "legacy timeout")
    result = update_project(project)
    assert (_session_end_timeout(project), len(_settings_warnings(result))) == ((5000, 1) if edited else (60, 0))


def test_the_dry_run_says_what_the_real_run_does(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    _outdate(project, edit_hook=True, user_key=True)
    before = (project / SETTINGS).read_bytes()
    dry = update_project(project, dry_run=True)
    assert (project / SETTINGS).read_bytes() == before
    real = update_project(project)
    assert _settings_warnings(dry) == _settings_warnings(real) != []


# --- the other clients and the other registration writers (review round) ---------------------------------------


def _edit(root: Path, rel: str, *, crlf: bool = False) -> None:
    path = root / rel
    if crlf:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    else:
        path.write_text(path.read_text(encoding="utf-8") + "\n# my edit\n", encoding="utf-8")


def test_a_crlf_copy_is_kept_as_the_writer_keeps_it(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    path = project / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    for e in data["hooks"]["SessionEnd"]:
        for h in e["hooks"]:
            h["timeout"] = 5000
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _edit(project, ".claude/hooks/session-end.sh", crlf=True)
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "crlf checkout")
    result = update_project(project)
    assert (_session_end_timeout(project), len(_settings_warnings(result))) == (5000, 1)


def test_every_registration_of_a_kept_hint_script_is_left_whatever_the_order(project: Path) -> None:
    from trw_mcp.bootstrap import update_project
    from trw_mcp.bootstrap._claude_code_distill_channels import _CC03_ENTRY

    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": HINT_CMD, "timeout": 77}]}
    for first, second in ((_CC03_ENTRY, other), (other, _CC03_ENTRY)):
        path = project / SETTINGS
        data = json.loads(path.read_text(encoding="utf-8"))
        data["hooks"]["PreToolUse"] = [
            e for e in data["hooks"]["PreToolUse"] if all(h.get("command") != HINT_CMD for h in e["hooks"])
        ] + [first, second]
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        _edit(project, HINT) if "# my edit" not in (project / HINT).read_text(encoding="utf-8") else None
        result = update_project(project)
        after = json.loads(path.read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
        kept = [e for e in after if any(h.get("command") == HINT_CMD for h in e["hooks"])]
        assert kept == [first, second]
        assert len(_settings_warnings(result)) == 1  # one per hook file, not per entry


def test_an_edited_statusline_script_keeps_its_registered_flags(project: Path) -> None:
    from trw_mcp.bootstrap import update_project

    path = project / SETTINGS
    data = json.loads(path.read_text(encoding="utf-8"))
    bundled_cmd = data.get("statusLine", {}).get("command")
    assert bundled_cmd, "the bundled settings register a statusLine; without one this test proves nothing"
    data["statusLine"]["command"] = bundled_cmd + " -x"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _edit(project, ".claude/hooks/statusline.sh")
    _git(project, "add", "-A")
    _git(project, "commit", "-qm", "edited statusline")
    result = update_project(project)
    assert json.loads(path.read_text(encoding="utf-8"))["statusLine"]["command"].endswith(" -x")
    assert any("statusline.sh as it was" in w for w in result["warnings"])


def _codex_hook(root: Path, script: str) -> list[dict[str, object]]:
    data = json.loads((root / ".codex/hooks.json").read_text(encoding="utf-8"))
    return [
        h
        for groups in data["hooks"].values()
        for g in groups
        for h in g["hooks"]
        if f"/{script}" in h.get("command", "")
    ]


def _stale(root: Path, rel: str, script: str, key: str = "timeout") -> None:
    path = root / rel
    data = json.loads(path.read_text(encoding="utf-8"))
    for groups in data["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                if f"/{script}" in hook.get("command", ""):
                    hook[key] = 999
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


@pytest.mark.parametrize("edited", [True, False])
def test_codex_general_merge_follows_its_hook_file(project: Path, edited: bool) -> None:
    from trw_mcp.bootstrap import generate_codex_hooks

    script = "pre-tool-deliver-gate.sh"
    assert not generate_codex_hooks(project)["errors"]
    _stale(project, ".codex/hooks.json", script)
    if edited:
        _edit(project, f".claude/hooks/{script}")
    result = generate_codex_hooks(project)
    assert not result["errors"]
    assert _codex_hook(project, script)[0].get("timeout") == (999 if edited else None)
    expected = [f".codex/hooks.json: left the registration of .claude/hooks/{script} as it was because"]
    assert [w[: len(expected[0])] for w in result.get("warnings", [])] == (expected if edited else [])


def test_codex_pre_edit_hint_group_is_kept_by_both_writers(project: Path) -> None:
    from trw_mcp.bootstrap import generate_codex_hooks
    from trw_mcp.bootstrap._codex_distill_channels import set_pre_edit_hint_registration

    script = "pre-tool-distill-hint.sh"
    assert not generate_codex_hooks(project)["errors"]
    assert set_pre_edit_hint_registration(project, present=True)
    _stale(project, ".codex/hooks.json", script)
    _edit(project, f".claude/hooks/{script}")
    result = generate_codex_hooks(project)  # the general merge runs first and used to drop the group outright
    assert _codex_hook(project, script)[0]["timeout"] == 999
    result2: dict[str, list[str]] = {}
    assert not set_pre_edit_hint_registration(project, present=True, result=result2)
    assert _codex_hook(project, script)[0]["timeout"] == 999
    assert len(result2["warnings"]) == 1 and not result.get("errors")


@pytest.mark.parametrize("edited", [True, False])
def test_copilot_merge_follows_its_hook_file(project: Path, edited: bool) -> None:
    from trw_mcp.bootstrap import generate_copilot_hooks

    script = "session-start.sh"
    assert not generate_copilot_hooks(project)["errors"]
    path = project / ".github/hooks/hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for groups in data["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                if f"/{script}" in hook["command"]:
                    hook["timeoutSec"] = 999
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if edited:
        _edit(project, f".claude/hooks/{script}")
    result = generate_copilot_hooks(project)
    after = json.loads(path.read_text(encoding="utf-8"))
    stale = [h for g in after["hooks"].values() for grp in g for h in grp["hooks"] if f"/{script}" in h["command"]]
    assert [h.get("timeoutSec") for h in stale] == ([999] if edited else [None])
    assert len(result.get("warnings", [])) == (1 if edited else 0)
    if edited:
        assert result["warnings"][0].startswith(
            f".github/hooks/hooks.json: left the registration of .claude/hooks/{script}"
        )


def test_a_new_project_still_receives_every_registration(tmp_path: Path) -> None:
    """No hooks, no settings: the reorder changes nothing for a project with nothing kept."""
    from trw_mcp.bootstrap import init_project
    from trw_mcp.bootstrap._utils import _DATA_DIR

    wanted = {
        h["command"]
        for groups in json.loads((Path(_DATA_DIR) / "settings.json").read_text(encoding="utf-8"))["hooks"].values()
        for g in groups
        for h in g["hooks"]
    }
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    assert not init_project(root, ide="claude-code").get("errors")
    assert wanted <= {h for g in _all_entries(root) for h in g}
    # The update's own merge on a project with no settings and no hooks yet copies the whole template.
    from trw_mcp.bootstrap._settings_merge import _merge_settings_json

    bare = tmp_path / "bare"
    (bare / ".claude").mkdir(parents=True)
    result: dict[str, list[str]] = {"errors": [], "updated": [], "created": [], "preserved": [], "warnings": []}
    _merge_settings_json(Path(_DATA_DIR) / "settings.json", bare / SETTINGS, result)
    assert not result["errors"]
    assert wanted <= {h for g in _all_entries(bare) for h in g}


def _all_entries(root: Path) -> list[list[str]]:
    data = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
    return [[h["command"] for h in g["hooks"]] for groups in data["hooks"].values() for g in groups]
