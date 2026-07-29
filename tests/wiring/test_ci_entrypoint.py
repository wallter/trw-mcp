"""FR07 — enforcing by default, end to end through the process boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from trw_mcp.wiring.cli import EXIT_FINDINGS, EXIT_INPUT_ERROR, EXIT_OK, main
from trw_mcp.wiring.selfcheck import _recipe_of


def _synthetic_unwired_repo(repo_root: Path, tmp_path: Path) -> Path:
    """A minimal repo carrying one introduced unwired feature.

    The manifest is copied verbatim, so the introduced defect is the *only*
    difference from a real channel declaration.
    """
    channels = tmp_path / ".trw/channels"
    channels.mkdir(parents=True)
    (channels / "manifest.yaml").write_text(
        (repo_root / ".trw/channels/manifest.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "CLAUDE.md").write_text("# no rendered segment here\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text(
        "check: wiring-gate\n\nwiring-gate:\n\t@python -m trw_mcp.wiring.cli --repo-root .\n", encoding="utf-8"
    )
    return tmp_path


def test_exits_nonzero_on_finding(repo_root: Path, tmp_path: Path) -> None:
    """A synthetic unwired feature makes the entry point fail."""
    root = _synthetic_unwired_repo(repo_root, tmp_path)
    assert main(["--repo-root", str(root)]) == EXIT_FINDINGS


def test_default_is_enforcing_no_flag_required(repo_root: Path, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """FR07: no flag, no config, no env var makes this advisory."""
    root = _synthetic_unwired_repo(repo_root, tmp_path)
    exit_code = main(["--repo-root", str(root)])
    captured = capsys.readouterr()
    assert exit_code == EXIT_FINDINGS
    assert "FAIL" in captured.out


def test_advisory_mode_requires_explicit_optin(repo_root: Path, tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """The escape hatch exists, is loud, and must be typed by a human."""
    root = _synthetic_unwired_repo(repo_root, tmp_path)
    exit_code = main(["--repo-root", str(root), "--advisory"])
    captured = capsys.readouterr()
    assert exit_code == EXIT_OK
    assert "ADVISORY MODE" in captured.err


def test_missing_input_exits_with_its_own_code(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    """'could not run' and 'found nothing' must never share an exit code."""
    exit_code = main(["--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == EXIT_INPUT_ERROR
    assert "INPUT ERROR" in captured.err


def test_live_repo_exits_zero_through_a_real_subprocess(repo_root: Path) -> None:
    """The exact command ``make check`` runs, through the process boundary."""
    completed = subprocess.run(
        [sys.executable, "-m", "trw_mcp.wiring.cli", "--repo-root", str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
        cwd=repo_root,
    )
    assert completed.returncode == EXIT_OK, completed.stdout + completed.stderr
    assert "no NEW findings" in completed.stdout


def test_makefile_check_target_depends_on_the_gate(repo_root: Path) -> None:
    makefile = (repo_root / "Makefile").read_text(encoding="utf-8")
    check_line = next(line for line in makefile.splitlines() if line.startswith("check:"))
    assert "wiring-gate" in check_line, "the gate is not a prerequisite of `make check`"
    # Extract the recipe with the SAME helper FR08's self-check uses, so the
    # test and the gate can never disagree about what "the recipe" means.
    recipe = _recipe_of(makefile, "wiring-gate")
    assert "trw_mcp.wiring.cli" in recipe, "the wiring-gate recipe does not run the detector"
    assert "--advisory" not in recipe, "CI must never run the detector in advisory mode"
