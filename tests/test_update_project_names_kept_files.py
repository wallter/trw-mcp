"""FB-INSTALL-03 (feedback sub_Q9TAfFqYL8lXkm53, sub_i7UMmxUbTbsdW0eD): an update names every file it left alone or moved.

On an 8.1.2 upgrade over an existing project the installer printed nothing about a kept, edited ``lib-trw.sh``
(and the hooks that depend on it), about a git-dirty ``AGENTS.md`` it did not refresh, or about the skill files it
deleted or kept. Three producers recorded those only as a count (``preserved``), in a key nothing reads
(``modified``), or under a path the spinner-mode installer discarded (it re-surfaces only ``WARNING:`` lines).

The producer is ``server/_subcommands.py``; the consumer is ``install-trw.py``'s ``run_with_progress``; the two
meet on the ``WARNING: <text>`` contract (``test_update_project_warning_surfacing``).
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module
from trw_mcp.bootstrap._version_migration import _cleanup_stale_artifacts

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


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
    base: dict[str, list[str]] = {
        "updated": [],
        "created": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
        "cleaned": [],
    }
    base.update(overrides)  # type: ignore[arg-type]
    return base


def _run_update(args: argparse.Namespace, result: dict[str, list[str]]) -> int:
    from trw_mcp.server._subcommands import _run_update_project

    with patch("trw_mcp.bootstrap.update_project", return_value=result), pytest.raises(SystemExit) as exc:
        _run_update_project(args)
    return int(exc.value.code or 0)


# ── Producer ─────────────────────────────────────────────────────────────


class TestTheCliNamesWhatItKept:
    def test_an_edited_hook_that_was_kept_is_named_with_the_reason(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        kept = tmp_path / ".claude" / "hooks" / "lib-trw.sh"
        _run_update(_args(tmp_path), _result(modified=[str(kept)]))

        out = capsys.readouterr().out
        assert "WARNING: kept .claude/hooks/lib-trw.sh: you edited it since TRW last wrote it" in out

    def test_a_file_outside_the_project_is_named_by_its_absolute_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_update(_args(tmp_path), _result(modified=["/elsewhere/x.sh"]))

        assert "WARNING: kept /elsewhere/x.sh:" in capsys.readouterr().out

    def test_a_refresh_reverted_for_uncommitted_changes_is_named(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_update(_args(tmp_path), _result(preserved=["AGENTS.md (uncommitted_changes)"]))

        assert "WARNING: kept ./AGENTS.md: it has uncommitted changes in git" in capsys.readouterr().out

    def test_a_retired_artifact_TRW_could_not_prove_it_wrote_is_named(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _run_update(_args(tmp_path), _result(preserved=[".agents/skills/trw-x (not_installer_owned)"]))

        assert "WARNING: kept .agents/skills/trw-x: TRW cannot show it wrote it" in capsys.readouterr().out

    def test_an_ordinary_preserved_entry_is_not_promoted_to_a_warning(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Negative control: ``.trw/config.yaml`` is preserved on every run by design."""
        _run_update(_args(tmp_path), _result(preserved=[str(tmp_path / ".trw" / "config.yaml"), "AGENTS.md"]))

        assert "kept" not in capsys.readouterr().out.lower().replace("preserved", "")

    def test_the_same_path_is_named_once(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        kept = tmp_path / ".claude" / "agents" / "trw-lead.md"
        _run_update(_args(tmp_path), _result(modified=[str(kept), str(kept)]))

        assert capsys.readouterr().out.count("kept .claude/agents/trw-lead.md") == 1

    def test_kept_lines_are_printed_even_when_the_update_also_errored(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = _run_update(
            _args(tmp_path), _result(modified=[str(tmp_path / ".claude/hooks/x.sh")], errors=["something broke"])
        )

        assert code == 1
        assert "WARNING: kept .claude/hooks/x.sh:" in capsys.readouterr().out

    def test_quiet_suppresses_them_and_verbose_logs_instead_of_printing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import structlog

        result = _result(modified=[str(tmp_path / ".claude/hooks/x.sh")])
        _run_update(_args(tmp_path, quiet=True), result)
        assert capsys.readouterr().out == ""

        with structlog.testing.capture_logs() as logs:
            _run_update(_args(tmp_path, verbose=1), result)
        assert "kept" not in capsys.readouterr().out
        assert any(entry.get("event") == "update_project_kept" for entry in logs)


class TestAnEditedLibraryIsReportedByARealUpdate:
    @pytest.mark.usefixtures("no_memory_daemon")
    def test_update_project_backs_up_the_edited_lib_and_the_cli_names_the_backup(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """FB-INSTALL-01 replaced "keep an edited lib-trw.sh" (its refreshed hooks then called functions the kept
        lib lacked): the user's uncommitted copy goes to .trw/trash byte for byte, the bundled lib is installed, and the run
        names the backup. Naming it is still this file's contract."""
        import subprocess

        from trw_mcp.bootstrap import init_project
        from trw_mcp.server._subcommands import _run_update_project

        root = tmp_path / "proj"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        assert not init_project(root, ide="claude-code")["errors"]
        lib = root / ".claude" / "hooks" / "lib-trw.sh"
        lib.write_text(lib.read_text(encoding="utf-8") + "\n# my local tweak\n", encoding="utf-8")
        edited = lib.read_bytes()

        with pytest.raises(SystemExit):
            _run_update_project(_args(root))

        out = capsys.readouterr().out
        assert ".claude/hooks/lib-trw.sh: your edited copy was moved to" in out
        assert b"# my local tweak" not in lib.read_bytes()
        saved = [p for p in (root / ".trw" / "trash").rglob("data") if p.read_bytes() == edited]
        assert saved, "the user's edited bytes must survive in .trw/trash"


# ── Retired artifacts are reported as trashed ────────────────────────────

_RETIRED = "trw-release-verify"


def test_a_retired_skill_is_deleted_in_place_and_reported_as_retired(tmp_path: Path) -> None:
    """The uncommitted-changes guard skips only paths in ``retired``, and the CLI names only those: a deleted
    retired skill left both blind (the nine silent skill files in sub_i7UMmxUbTbsdW0eD)."""
    skill = tmp_path / ".claude" / "skills" / _RETIRED / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# release gate\n", encoding="utf-8")
    digest = hashlib.sha256(skill.read_bytes()).hexdigest()
    hashes = {f"{_RETIRED}/SKILL.md": digest}
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(
        f"version: 2\ncontent_hashes:\n  {_RETIRED}/SKILL.md: {digest}\n", encoding="utf-8"
    )
    result: dict[str, list[str]] = {"updated": [], "errors": []}

    _cleanup_stale_artifacts(tmp_path, result, None, manifest_hashes=hashes)

    assert not skill.parent.exists()
    assert result["retired"] == [f".claude/skills/{_RETIRED}/SKILL.md"]
    assert not (tmp_path / ".trw" / "trash").exists()


def test_the_cli_names_each_retired_file_unless_a_warning_already_describes_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from trw_mcp.server._update_report import print_retired

    print_retired(
        [".claude/hooks/old.sh", "a.md", "b.md"],
        ["b.md: removed; your version differs from TRW's but is committed in git (restore: git restore -- b.md)"],
    )
    assert capsys.readouterr().out.splitlines() == [
        "Removed retired TRW file: .claude/hooks/old.sh",
        "Removed retired TRW file: a.md",
    ]


def test_the_cli_names_every_edit_to_the_users_claude_md(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """CLAUDE.md is the user's file, so an edit to it is never only a count in the summary (9.0.1 regression)."""
    edit = "removed TRW's block (it already imports AGENTS.md, which imports .trw/INSTRUCTIONS.md)"
    _run_update(_args(tmp_path), _result(updated=["CLAUDE.md"], claude_md=[edit]))

    assert f"CLAUDE.md: {edit}" in capsys.readouterr().out.splitlines()
