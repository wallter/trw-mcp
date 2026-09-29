"""``uninstall`` names the user-placed files it will remove from ``.trw/``.

Behaviour is unchanged (``.trw`` is removed whole); the listing just says so.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._subcommands import _run_uninstall
from trw_mcp.server._uninstall_corpus import trw_created_names

_LINE = "item(s) in .trw/ were not created by TRW and will be removed"
_SENTENCE = "Everything under the project's .trw/ is removed (except the learning corpus with --keep-memory)."


def _project(tmp_path: Path) -> Path:
    trw = tmp_path / ".trw"
    for d in ("frameworks", "context", "runs", "learnings/entries", "runtime"):
        (trw / d).mkdir(parents=True)
    (trw / "config.yaml").write_text("x: 1\n")
    (trw / "memory.db").write_text("db")
    (trw / "learnings" / "entries" / "a.yaml").write_text("id: a\n")
    return tmp_path


def _user_files(tmp_path: Path) -> None:
    trw = tmp_path / ".trw"
    (trw / "notes.md").write_text("mine")
    (trw / "mine").mkdir()
    (trw / "mine" / "a.txt").write_text("1")
    (trw / "mine" / "b.txt").write_text("2")


def _run(tmp_path: Path, **kw: object) -> None:
    opts: dict[str, object] = {"dry_run": False, "yes": False, **kw}
    _run_uninstall(argparse.Namespace(target_dir=str(tmp_path), **opts))


@pytest.mark.usefixtures("no_memory_daemon")
@pytest.mark.unit
class TestUninstallUserFiles:
    def test_dry_run_names_user_entries_and_keeps_them(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _user_files(_project(tmp_path))
        _run(tmp_path, dry_run=True)
        out = capsys.readouterr().out
        assert f"2 {_LINE}: mine/ (2 files), notes.md" in out
        assert (tmp_path / ".trw" / "notes.md").exists()

    def test_yes_prints_line_and_still_removes_everything(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _user_files(_project(tmp_path))
        _run(tmp_path, yes=True)
        out = capsys.readouterr().out
        assert f"2 {_LINE}: mine/ (2 files), notes.md" in out
        assert not (tmp_path / ".trw").exists()

    def test_clean_scaffold_has_no_line(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _project(tmp_path)
        _run(tmp_path, dry_run=True)
        assert _LINE not in capsys.readouterr().out

    def test_keep_memory_does_not_list_corpus(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _user_files(_project(tmp_path))
        _run(tmp_path, dry_run=True, keep_memory=True)
        out = capsys.readouterr().out
        line = next(ln for ln in out.splitlines() if _LINE in ln)
        assert "notes.md" in line and "mine/" in line
        assert not re.search(r"memory|learnings", line)

    def test_help_contains_sentence(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            _build_arg_parser().parse_args(["uninstall", "--help"])
        assert _SENTENCE in " ".join(capsys.readouterr().out.split())

    def test_control_characters_in_names_are_escaped(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _project(tmp_path)
        (tmp_path / ".trw" / "\x1b[31mevil.md").write_text("x")
        _run(tmp_path, dry_run=True)
        out = capsys.readouterr().out
        assert "\x1b" not in out
        assert "\\x1b[31mevil.md" in out

    def test_vanishing_dir_does_not_abort_listing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _user_files(_project(tmp_path))
        (tmp_path / ".trw" / "gone").mkdir()
        real_rglob = Path.rglob

        def flaky(self: Path, pattern: str):  # type: ignore[no-untyped-def]
            if self.name == "gone":
                raise FileNotFoundError(str(self))
            return real_rglob(self, pattern)

        monkeypatch.setattr(Path, "rglob", flaky)
        _run(tmp_path, yes=True)
        out = capsys.readouterr().out
        assert "gone/ (? files)" in out and "mine/ (2 files)" in out and "notes.md" in out
        assert not (tmp_path / ".trw").exists()

    @pytest.mark.parametrize("keep", [True, False])
    def test_sqlite_sidecars_never_listed(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], keep: bool) -> None:
        _project(tmp_path)
        for n in ("memory.db-wal", "memory.db-shm"):
            (tmp_path / ".trw" / n).write_text("x")
        _run(tmp_path, dry_run=True, keep_memory=keep)
        assert _LINE not in capsys.readouterr().out


@pytest.mark.unit
def test_known_set_covers_source_literals() -> None:
    """Every ``trw_dir / "<name>"`` literal in the package is a TRW-created name (drift guard)."""
    import trw_mcp

    root = Path(trw_mcp.__file__).parent
    pat = re.compile(r"""(?:trw_dir|trw_root|_trw_dir|"\.trw") */ *"([A-Za-z0-9_.-]+)\"""")
    found = {m for f in root.rglob("*.py") for m in pat.findall(f.read_text(encoding="utf-8"))}
    found.discard(".trw")
    assert found <= trw_created_names(), sorted(found - trw_created_names())


@pytest.mark.parametrize("name", ["trash", "Trash"])
def test_trash_is_trw_created_and_never_listed_as_a_user_entry(tmp_path: Path, name: str) -> None:
    """``.trw/trash`` holds captured user bytes; its own uninstall line reports it, never this listing."""
    from trw_mcp.bootstrap._safe_remove import TRASH_DIR_NAME
    from trw_mcp.server._uninstall_corpus import untracked_trw_entries

    assert TRASH_DIR_NAME in trw_created_names()
    trw = tmp_path / ".trw"
    (trw / name).mkdir(parents=True)
    (trw / name / "captured.md").write_text("user bytes", encoding="utf-8")
    assert untracked_trw_entries(trw) == []
