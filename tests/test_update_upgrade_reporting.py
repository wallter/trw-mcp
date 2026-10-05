"""The 8.1.7 -> 9.0.1 upgrade report: what update-project deleted, kept and refreshed must be named, and named right.

* every file the update deleted is named, whichever writer deleted it (``cleaned`` is the transaction diff),
  under ``-v`` too, where nothing named a deletion;
* a kept file at the project root is shown as ``./NAME`` (``kept FRAMEWORK.md`` read as a path with no directory);
* ``.trw/installer-meta.yaml`` left uncommitted by an earlier update is TRW's own stamp, refreshed rather than kept
  as "uncommitted changes", while any byte of someone else's in it still keeps it.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap._canon_ownership import is_trw_install_record
from trw_mcp.bootstrap._utils import INSTALLER_META_KEYS, _write_installer_metadata

_META = ".trw/installer-meta.yaml"


def _args(target: Path, **overrides: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "target_dir": str(target),
        "pip_install": False,
        "dry_run": False,
        "ide": "claude-code",
        "log_json": False,
        "debug": False,
        "verbose": 0,
        "quiet": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _result(**overrides: object) -> dict[str, list[str]]:
    base: dict[str, list[str]] = {"updated": [], "created": [], "preserved": [], "errors": [], "warnings": []}
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _run_update(args: argparse.Namespace, result: dict[str, list[str]]) -> None:
    from trw_mcp.server._subcommands import _run_update_project

    with patch("trw_mcp.bootstrap.update_project", return_value=result), pytest.raises(SystemExit):
        _run_update_project(args)


# ── Every deletion is named ──────────────────────────────────────────────


class TestEveryDeletionIsNamed:
    def test_a_deletion_only_the_transaction_diff_saw_is_named(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No sweep recorded it in ``retired``; the diff of the managed surface did, and that is enough."""
        gone = [".agents/skills/trw-prd-new/SKILL.md", ".github/skills/trw-prd-new/SKILL.md"]
        _run_update(_args(tmp_path), _result(cleaned=gone))

        lines = capsys.readouterr().out.splitlines()
        assert [line for line in lines if line.startswith("Removed")] == [
            f"Removed retired TRW file: {path}" for path in gone
        ]

    def test_a_file_in_both_lists_is_named_once(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        path = ".claude/skills/trw-prd-new/SKILL.md"
        _run_update(_args(tmp_path), _result(retired=[path], cleaned=[path]))

        assert capsys.readouterr().out.count(f"Removed retired TRW file: {path}") == 1

    def test_verbose_logs_each_deletion_and_quiet_prints_none(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import structlog

        result = _result(cleaned=[".claude/skills/trw-prd-new/SKILL.md"])
        with structlog.testing.capture_logs() as logs:
            _run_update(_args(tmp_path, verbose=1), result)
        removed = [e for e in logs if e.get("event") == "update_project_removed"]
        assert [e["path"] for e in removed] == [".claude/skills/trw-prd-new/SKILL.md"]

        capsys.readouterr()
        _run_update(_args(tmp_path, quiet=True), result)
        assert capsys.readouterr().out == ""


# ── Kept lines carry a repo-relative path a reader can place ─────────────


class TestKeptPathsArePlaceable:
    def test_a_root_level_file_is_shown_from_the_project_root(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_update(_args(tmp_path), _result(preserved=["FRAMEWORK.md (uncommitted_changes)"]))

        assert "WARNING: kept ./FRAMEWORK.md: it has uncommitted changes in git" in capsys.readouterr().out

    def test_a_nested_file_keeps_its_directories(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        _run_update(_args(tmp_path), _result(modified=[str(tmp_path / ".trw" / "frameworks" / "FRAMEWORK.md")]))

        assert "WARNING: kept .trw/frameworks/FRAMEWORK.md:" in capsys.readouterr().out

    def test_an_absolute_path_through_a_symlinked_project_root_is_still_relative(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        # The CLI resolves the target; a producer may have recorded the path under the unresolved spelling.
        _run_update(_args(link), _result(modified=[str(real / "AGENTS.md")]))

        assert "WARNING: kept ./AGENTS.md:" in capsys.readouterr().out


# ── installer-meta.yaml: TRW's own stamp is refreshed, an edited one kept ─


def _write_meta(root: Path) -> Path:
    (root / ".trw").mkdir(parents=True, exist_ok=True)
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _write_installer_metadata(root, "update-project", result)
    assert not result["errors"]
    return root / _META


class TestInstallRecordProof:
    def test_the_writer_writes_exactly_the_recorded_keys(self, tmp_path: Path) -> None:
        from ruamel.yaml import YAML

        loaded = YAML(typ="safe", pure=True).load(_write_meta(tmp_path).read_text(encoding="utf-8"))
        assert tuple(loaded) == INSTALLER_META_KEYS

    def test_a_record_TRW_wrote_is_proven(self, tmp_path: Path) -> None:
        _write_meta(tmp_path)

        assert is_trw_install_record(tmp_path, _META)

    @pytest.mark.parametrize(
        "edit",
        [
            lambda text: text + "# my note\n",
            lambda text: text + "my_key: 1\n",
            lambda text: text.replace("installed_by: trw-mcp update-project", "installed_by: me"),
            lambda text: text.replace("hooks_count: ", "hooks_count:  "),
        ],
        ids=["comment", "foreign-key", "not-trw-mcp", "hand-formatting"],
    )
    def test_any_byte_of_someone_elses_is_not_proven(self, tmp_path: Path, edit: object) -> None:
        meta = _write_meta(tmp_path)
        meta.write_text(edit(meta.read_text(encoding="utf-8")), encoding="utf-8")  # type: ignore[operator]

        assert not is_trw_install_record(tmp_path, _META)

    def test_only_the_install_record_is_judged(self, tmp_path: Path) -> None:
        meta = _write_meta(tmp_path)
        other = tmp_path / ".trw" / "config.yaml"
        other.write_bytes(meta.read_bytes())

        assert not is_trw_install_record(tmp_path, ".trw/config.yaml")
        assert not is_trw_install_record(tmp_path, ".trw/absent.yaml")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.mark.usefixtures("no_memory_daemon")
class TestAnUpgradeOverAnEarlierUncommittedUpdate:
    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        from trw_mcp.bootstrap import init_project

        root = tmp_path / "proj"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        _git(root, "config", "user.email", "t@example.com")
        _git(root, "config", "user.name", "t")
        assert not init_project(root, ide="claude-code")["errors"]
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "base")
        _write_meta(root)  # an earlier update's stamp, never committed: git lists it as modified
        return root

    def test_TRWs_own_stamp_is_refreshed_not_kept(self, project: Path) -> None:
        from trw_mcp.bootstrap import update_project

        before = (project / _META).read_bytes()
        result = update_project(project, ide="claude-code")

        assert not result["errors"]
        assert not [e for e in result.get("preserved", []) if str(e).startswith(_META)]
        assert (project / _META).read_bytes() != before

    def test_an_edited_stamp_is_still_kept(self, project: Path) -> None:
        from trw_mcp.bootstrap import update_project

        meta = project / _META
        meta.write_text(meta.read_text(encoding="utf-8") + "# pinned by ops\n", encoding="utf-8")
        edited = meta.read_bytes()
        result = update_project(project, ide="claude-code")

        assert f"{_META} (uncommitted_changes)" in result["preserved"]
        assert meta.read_bytes() == edited
