"""sub_GOYE8ssHqS item 3: a bare ``update-project`` says which client it newly adopted, why, and how to undo it.

Adoption on file-by-file proof is by design (``_client_adoption``) and unchanged. What was missing is the line:
a blocked client was named ("<client> not adopted into the install record: ..."), an adopted one left no trace
but ``config.yaml`` among the updated files.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from trw_mcp.bootstrap import _optional_skills, init_project

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

CURSOR = "cursor-ide"
_ADOPTED = f"Note: {CURSOR} adopted into the install record:"
_BLOCKED = f"WARNING: {CURSOR} not adopted into the install record:"


@pytest.fixture
def orphaned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with TRW's cursor files on disk, byte for byte, that the install record does not name."""
    home = tmp_path / "home"
    (home / ".Trash").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for var in ("CURSOR_TRACE_ID", "CURSOR_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "cursor"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin")
    monkeypatch.setattr(_optional_skills, "skill_enabled", lambda *_a, **_k: True)
    root = tmp_path / "proj"
    root.mkdir()
    git = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert not init_project(root)["errors"]
    assert CURSOR in _record(root), "precondition: detection installed and recorded cursor-ide"
    config = root / ".trw" / "config.yaml"
    data = yaml.safe_load(config.read_text())
    data["target_platforms"] = ["claude-code"]  # what an older init left behind
    config.write_text(yaml.safe_dump(data, sort_keys=False))
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "--no-verify", "-m", "install"], check=True)
    return root


def _record(root: Path) -> list[str]:
    return list(yaml.safe_load((root / ".trw" / "config.yaml").read_text())["target_platforms"])


def _cli_update(root: Path, capsys: pytest.CaptureFixture[str], **overrides: object) -> str:
    from trw_mcp.server._subcommands import _run_update_project

    args: dict[str, object] = {
        "target_dir": str(root),
        "pip_install": False,
        "dry_run": False,
        "ide": None,
        "log_json": False,
        "debug": False,
        "verbose": 0,
        "quiet": False,
        "reprovision": None,
        "rerender": None,
    }
    args.update(overrides)
    capsys.readouterr()
    with pytest.raises(SystemExit):
        _run_update_project(argparse.Namespace(**args))
    captured = capsys.readouterr()
    return captured.out + captured.err


def _lines(out: str, prefix: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(prefix)]


def test_a_bare_update_names_the_client_it_adopted_why_and_how_to_remove_it(
    orphaned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _cli_update(orphaned, capsys)

    assert CURSOR in _record(orphaned), "precondition: the adoption itself is unchanged"
    adopted = _lines(out, _ADOPTED)
    assert len(adopted) == 1, out
    assert "byte-identical to what TRW writes or recorded writing" in adopted[0]
    assert f"trw-mcp uninstall --ide {CURSOR}" in adopted[0]
    assert not _lines(out, _BLOCKED)


def test_the_second_run_says_nothing_about_adoption(orphaned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _lines(_cli_update(orphaned, capsys), _ADOPTED), "precondition: the first run adopted"

    out = _cli_update(orphaned, capsys)

    assert "adopted" not in out, out
    assert CURSOR in _record(orphaned)


def test_a_blocked_client_prints_what_it_printed_before_and_no_adopted_line(
    orphaned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edited = orphaned / ".cursor" / "skills" / "trw-audit" / "SKILL.md"
    edited.write_bytes(edited.read_bytes() + b"\nmine\n")
    mine = edited.read_bytes()

    out = _cli_update(orphaned, capsys)

    blocked = _lines(out, _BLOCKED)
    assert blocked == [
        f"{_BLOCKED} .cursor/skills/trw-audit/SKILL.md differs from the bundled render and any recorded hash"
    ], out
    assert not _lines(out, _ADOPTED)
    assert CURSOR not in _record(orphaned) and edited.read_bytes() == mine


def test_a_dry_run_proposes_the_adoption_and_records_nothing(
    orphaned: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _cli_update(orphaned, capsys, dry_run=True)

    assert len(_lines(out, f"Note: {CURSOR} would be adopted into the install record:")) == 1, out
    assert not _lines(out, _ADOPTED)
    assert _record(orphaned) == ["claude-code"]


def test_a_rolled_back_update_does_not_claim_an_adoption(
    orphaned: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap import _update_project

    def fail_after_the_writers(*_a: object, **_k: object) -> None:
        raise RuntimeError("injected failure after every writer ran")

    monkeypatch.setattr(_update_project, "_verify_installation", fail_after_the_writers)

    out = _cli_update(orphaned, capsys)

    assert "injected failure" in out, "precondition: the run failed and was rolled back"
    assert _record(orphaned) == ["claude-code"]
    assert "adopted into the install record" not in out, out


def test_an_explicit_ide_run_is_not_an_adoption(orphaned: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``--ide`` names the client, so nothing was adopted on TRW's own judgement and no line is owed."""
    out = _cli_update(orphaned, capsys, ide=CURSOR)

    assert CURSOR in _record(orphaned)
    assert "adopted" not in out, out


def test_a_record_that_could_not_be_written_is_not_reported_as_an_adoption(
    orphaned: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The config writer turns a failed write into a warning; the line is printed only for a client the record
    holds when the run ends."""
    from trw_mcp.bootstrap import _ide_targets_finalize

    real = _ide_targets_finalize.write_checkout_file
    refused: list[Path] = []

    def refuse_the_record(root: Path, path: Path, *args: object, **kwargs: object) -> None:
        if path.name == "config.yaml":
            refused.append(path)
            raise OSError(30, "Read-only file system", str(path))
        real(root, path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_ide_targets_finalize, "write_checkout_file", refuse_the_record)

    out = _cli_update(orphaned, capsys)

    assert refused, "precondition: the record write was attempted and failed"
    assert CURSOR not in _record(orphaned)
    assert "adopted into the install record" not in out, out
