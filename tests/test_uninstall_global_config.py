"""``uninstall`` and the user-global ``~/.gemini/config/mcp_config.json`` (W4 P1).

The file is shared by every project on the machine and its ``trw`` entry carries no project
identity, so a project uninstall must never edit it; only ``uninstall --global`` may, with the exact
match and format checks unchanged. The init/update writer must never replace an unparseable copy.
All I/O runs under the suite's autouse sandboxed ``$HOME`` (``tmp_path/.home``).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from tests._fs_hazards import (
    assert_user_bytes_preserved,
    atomic_replace,
    edit_in_place,
    fail_nth_read,
    race_after,
    snapshot_user_bytes,
)
from trw_mcp.bootstrap._antigravity_cli import _antigravity_global_mcp_config_path, _resolve_trw_mcp_command
from trw_mcp.server._subcommands_lifecycle import _run_uninstall

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

OTHER = {"command": "npx", "args": ["-y", "some-server"]}
PROJ_B = {"command": "/other/projB/.venv/bin/trw-mcp", "args": []}


def _generated() -> dict[str, object]:
    command, args = _resolve_trw_mcp_command()
    return {"command": command, "args": args}


def _canonical(servers: dict[str, object]) -> bytes:
    return (json.dumps({"mcpServers": servers}, indent=2) + "\n").encode()


def _multi() -> bytes:
    return _canonical({"context7": OTHER, "trw-projB": PROJ_B, "trw": _generated()})


@pytest.fixture
def home_file(tmp_path: Path) -> Path:
    path = _antigravity_global_mcp_config_path()
    assert path.is_relative_to(tmp_path.parent), (
        "the global path must resolve under the pytest tmp base, never the real home"
    )
    path.parent.mkdir(parents=True)
    return path


@pytest.fixture
def project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    (proj / ".trw").mkdir(parents=True)
    (proj / ".trw" / "config.yaml").write_text("debug: false\n")
    return proj


def _uninstall(project: Path, **kw: object) -> None:
    base: dict[str, object] = {
        "target_dir": str(project),
        "dry_run": False,
        "yes": True,
        "delete_memory": False,
        "keep_memory": False,
    }
    _run_uninstall(argparse.Namespace(**{**base, **kw}))


def _stat_key(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@pytest.mark.parametrize("ide", [None, "antigravity-cli", "claude-code"])
def test_default_uninstall_never_writes_the_global_file(
    tmp_path: Path, project: Path, home_file: Path, capsys: pytest.CaptureFixture[str], ide: str | None
) -> None:
    if ide:  # --ide needs an installed project (its manifest); seed the global file AFTER init writes it
        from trw_mcp.bootstrap import init_project

        init_project(project, ide=ide)
    home_file.parent.mkdir(parents=True, exist_ok=True)
    home_file.write_bytes(_multi())
    before, before_stat = home_file.read_bytes(), _stat_key(home_file)
    snap = snapshot_user_bytes(Path.home())

    _uninstall(project, ide=ide)

    assert home_file.read_bytes() == before
    assert _stat_key(home_file) == before_stat
    assert_user_bytes_preserved(snap, Path.home())
    out = capsys.readouterr().out
    if ide != "claude-code":  # a client with no global surface has nothing to report
        assert "left in place" in out
        assert "uninstall --global" in out
    assert not list(home_file.parent.glob("*.bak"))


def test_global_flag_removes_only_the_matching_trw_entry(
    tmp_path: Path, project: Path, home_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home_file.write_bytes(_multi())
    _uninstall(project, global_config=True)
    data = json.loads(home_file.read_text())
    assert data == {"mcpServers": {"context7": OTHER, "trw-projB": PROJ_B}}
    assert home_file.read_bytes() == _canonical({"context7": OTHER, "trw-projB": PROJ_B})
    assert "left in place" not in capsys.readouterr().out


def test_global_flag_on_a_file_holding_only_the_generated_entry_leaves_an_empty_object(
    project: Path, home_file: Path
) -> None:
    home_file.write_bytes(_canonical({"trw": _generated()}))
    _uninstall(project, global_config=True)
    assert home_file.read_bytes() == b"{}\n"


_DECOY = {"command": "trw-mcp", "args": [], "env": {"K": "placeholder"}}
_UNMATCHED: dict[str, bytes] = {
    "decoy_with_env": _canonical({"mine": OTHER, "trw": _DECOY}),
    "other_key_only": _canonical({"mine": OTHER, "trw-projB": PROJ_B}),
    "compact_formatting": (json.dumps({"mcpServers": {"mine": OTHER, "trw": _generated()}}) + "\n").encode(),
    "unparseable": b'{"mcpServers": {"mine": ',
    "non_object": b"[1, 2, 3]\n",
    "non_utf8": b"\xff\xfe{\x00junk",
}


@pytest.mark.parametrize("name", sorted(set(_UNMATCHED) - {"non_utf8"}))
def test_global_flag_leaves_unmatched_or_unparseable_files_byte_identical(
    tmp_path: Path, project: Path, home_file: Path, name: str
) -> None:
    home_file.write_bytes(_UNMATCHED[name])
    snap = snapshot_user_bytes(Path.home())
    _uninstall(project, global_config=True)
    assert home_file.read_bytes() == _UNMATCHED[name]
    assert_user_bytes_preserved(snap, Path.home())


def test_global_flag_refuses_a_non_utf8_file_and_prints_the_read_failure_not_symlink(
    project: Path, home_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home_file.write_bytes(_UNMATCHED["non_utf8"])
    with pytest.raises(SystemExit) as exc:
        _uninstall(project, global_config=True)
    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert f"Error updating {home_file}: could not read {home_file}" in out
    assert "symlink" not in out
    assert home_file.read_bytes() == _UNMATCHED["non_utf8"]


def test_global_flag_never_edits_through_a_symlinked_file(
    tmp_path: Path, project: Path, home_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "elsewhere.json"
    real.write_bytes(_multi())
    os.symlink(real, home_file)
    with pytest.raises(SystemExit):  # refused (symlink) is reported as an error
        _uninstall(project, global_config=True)
    assert f"Error updating {home_file}: refused (symlink)" in capsys.readouterr().out
    assert real.read_bytes() == _multi()
    assert home_file.is_symlink()


@pytest.mark.parametrize("link", [".gemini", ".gemini/config"])
def test_global_flag_never_edits_through_a_symlinked_parent_dir(
    tmp_path: Path, project: Path, capsys: pytest.CaptureFixture[str], link: str
) -> None:
    """Coverage of behaviour that was already correct (the path guard walks every parent under HOME)."""
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "config").mkdir(parents=True)
    (elsewhere / "config" / "mcp_config.json").write_bytes(_multi())
    home = Path.home()
    if link == ".gemini":
        os.symlink(elsewhere, home / ".gemini")
    else:
        (home / ".gemini").mkdir()
        os.symlink(elsewhere / "config", home / ".gemini" / "config")
    snap = snapshot_user_bytes(elsewhere)

    with pytest.raises(SystemExit):
        _uninstall(project, global_config=True)

    assert "refused" in capsys.readouterr().out
    assert (elsewhere / "config" / "mcp_config.json").read_bytes() == _multi()
    assert_user_bytes_preserved(snap, elsewhere)


_RACERS = {
    "atomic_replace": atomic_replace,
    "edit_in_place": edit_in_place,
}


@pytest.mark.parametrize("how", sorted(_RACERS))
def test_global_strip_aborts_when_another_writer_changes_the_file_between_read_and_write(
    project: Path,
    home_file: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    how: str,
) -> None:
    """The real run's 2nd read (the 1st is the preview's match check) is stale once a racer adds a server."""
    home_file.write_bytes(_multi())
    racer = _canonical({"context7": OTHER, "trw-projB": PROJ_B, "late": OTHER, "trw": _generated()})
    probe = race_after(
        monkeypatch, target=home_file, op="read_bytes", nth=2, interloper=lambda: _RACERS[how](home_file, racer)
    )

    with pytest.raises(SystemExit) as exc:
        _uninstall(project, global_config=True)

    assert probe.fired
    assert exc.value.code == 1
    assert home_file.read_bytes() == racer  # the other writer's bytes, untouched
    assert "changed while uninstall ran" in capsys.readouterr().out
    assert not list(home_file.parent.glob("*.tmp")) and not list(home_file.parent.glob("*.bak"))


@pytest.mark.parametrize("nth", [1, 2, 3])
def test_global_strip_survives_a_permission_error_on_each_read(
    project: Path, home_file: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, nth: int
) -> None:
    """Reads are: 1 the preview match check, 2 the strip, 3 the compare before the write."""
    home_file.write_bytes(_multi())
    counter = fail_nth_read(monkeypatch, home_file, nth=nth)
    stripped = _canonical({"context7": OTHER, "trw-projB": PROJ_B})

    if nth == 1:  # a failed preview only mislabels the listing; the real read then succeeds and strips
        _uninstall(project, global_config=True)
        assert home_file.read_bytes() == stripped
    else:
        with pytest.raises(SystemExit):
            _uninstall(project, global_config=True)
        assert home_file.read_bytes() == _multi()
    assert counter.raised == 1
    out = capsys.readouterr().out
    if nth >= 2:  # a failed strip or compare read prints the read failure; it is not another writer's edit
        assert "injected read failure" in out
        assert "changed while uninstall ran" not in out


def test_default_dry_run_reports_left_in_place(
    project: Path, home_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home_file.write_bytes(_multi())
    _uninstall(project, dry_run=True, yes=False)
    out = capsys.readouterr().out
    assert "would be left in place" in out
    assert "(TRW entries only)" not in out
    assert home_file.read_bytes() == _multi()


_NO_MATCH = "no entry matching TRW's generated value"
_CUSTOM_FORMAT = "TRW entry present but the file isn't in TRW's formatting; left untouched, remove it by hand"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (_multi(), "would be removed"),
        (_UNMATCHED["decoy_with_env"], "would be kept"),
        (_UNMATCHED["unparseable"], "would be kept"),
        (_UNMATCHED["compact_formatting"], f"({_CUSTOM_FORMAT})"),
    ],
)
def test_global_dry_run_reports_whether_the_entry_matches(
    project: Path, home_file: Path, capsys: pytest.CaptureFixture[str], content: bytes, expected: str
) -> None:
    home_file.write_bytes(content)
    _uninstall(project, dry_run=True, yes=False, global_config=True)
    out = capsys.readouterr().out
    assert expected in out
    assert "left in place" not in out
    assert home_file.read_bytes() == content


@pytest.mark.parametrize(
    ("content", "listed", "kept_line"),
    [
        (_multi(), "TRW entry matches; will be removed", None),
        (_UNMATCHED["decoy_with_env"], "no matching TRW entry; will be kept", _NO_MATCH),
        (_UNMATCHED["compact_formatting"], _CUSTOM_FORMAT, _CUSTOM_FORMAT),
    ],
)
def test_global_real_run_preview_and_outcome_are_truthful(
    project: Path,
    home_file: Path,
    capsys: pytest.CaptureFixture[str],
    content: bytes,
    listed: str,
    kept_line: str | None,
) -> None:
    home_file.write_bytes(content)
    _uninstall(project, global_config=True)
    out = capsys.readouterr().out
    assert f"entry {home_file} ({listed})" in out
    assert "(TRW entries only)" not in out
    assert ("Kept:" in out) is (kept_line is not None)
    if kept_line is not None:
        assert f"Kept: {home_file} ({kept_line})" in out
        assert home_file.read_bytes() == content


def test_global_dry_run_checks_each_file_once(project: Path, home_file: Path) -> None:
    home_file.write_bytes(_UNMATCHED["decoy_with_env"])
    with capture_logs() as logs:
        _uninstall(project, dry_run=True, yes=False, global_config=True)
    assert [e["event"] for e in logs].count("uninstall_entry_changed") == 1


def test_global_dry_run_lists_a_refused_file_once_in_the_refusals(
    project: Path, home_file: Path, tmp_path: Path
) -> None:
    real = tmp_path / "elsewhere.json"
    real.write_bytes(_multi())
    os.symlink(real, home_file)
    with capture_logs() as logs:
        _uninstall(project, dry_run=True, yes=False, global_config=True)
    assert [e["event"] for e in logs].count("uninstall_merged_config_refused") == 1


def test_custom_formatting_warning_names_the_actual_file(project: Path, home_file: Path) -> None:
    home_file.write_bytes(_UNMATCHED["compact_formatting"])
    with capture_logs() as logs:
        _uninstall(project, global_config=True)
    paths = [e["path"] for e in logs if e.get("event") == "uninstall_merged_config_custom_formatting"]
    assert paths == [".gemini/config/mcp_config.json"]


@pytest.mark.parametrize("name", ["unparseable", "non_object", "non_utf8"])
def test_init_refuses_to_replace_an_unparseable_global_file(tmp_path: Path, home_file: Path, name: str) -> None:
    from trw_mcp.bootstrap import init_project

    home_file.write_bytes(_UNMATCHED[name])
    snap = snapshot_user_bytes(Path.home())
    proj = tmp_path / "initproj"
    proj.mkdir()
    (proj / ".git").mkdir()

    result = init_project(proj, ide="antigravity-cli")

    assert home_file.read_bytes() == _UNMATCHED[name]
    assert not list(home_file.parent.glob("*.bak"))
    assert_user_bytes_preserved(snap, Path.home())
    assert any("mcp_config.json" in w and "left untouched" in w for w in result.get("warnings", []))


@pytest.mark.parametrize("name", ["unparseable", "non_utf8"])
def test_update_refuses_to_replace_an_unparseable_global_file(tmp_path: Path, home_file: Path, name: str) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    proj = tmp_path / "updproj"
    proj.mkdir()
    (proj / ".git").mkdir()
    init_project(proj, ide="antigravity-cli")  # writes a valid global file
    home_file.write_bytes(_UNMATCHED[name])
    snap = snapshot_user_bytes(Path.home())

    result = update_project(proj, ide="antigravity-cli")

    assert home_file.read_bytes() == _UNMATCHED[name]
    assert not list(home_file.parent.glob("*.bak"))
    assert_user_bytes_preserved(snap, Path.home())
    assert any("mcp_config.json" in w and "left untouched" in w for w in result.get("warnings", []))


_NON_OBJECT_SERVERS = {"list": [1, 2], "null": None, "string": "x"}


@pytest.mark.parametrize("kind", sorted(_NON_OBJECT_SERVERS))
def test_init_and_update_leave_a_non_object_mcp_servers_untouched(tmp_path: Path, home_file: Path, kind: str) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    content = (json.dumps({"theme": "dark", "mcpServers": _NON_OBJECT_SERVERS[kind]}, indent=2) + "\n").encode()
    home_file.write_bytes(content)
    snap = snapshot_user_bytes(Path.home())
    proj = tmp_path / "initproj"
    proj.mkdir()
    (proj / ".git").mkdir()

    for run in (init_project, update_project):
        result = run(proj, ide="antigravity-cli")
        assert home_file.read_bytes() == content
        assert not list(home_file.parent.glob("*.bak"))
        assert_user_bytes_preserved(snap, Path.home())
        assert any("mcp_config.json" in w and "left untouched" in w for w in result.get("warnings", []))
