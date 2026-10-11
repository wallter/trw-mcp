"""sub_GOYE8ssHqS: a kept file TRW has no record of is not reported as "you edited it".

The manifest recorder records ``<skill>/SKILL.md`` only, while the update guards every file of a ``.claude``
skill. A support file that differs from the bundle and has no record is kept, and the report used to say the
user had edited it; the reporter's ``trw-handoff/RECEIVE.md`` and ``REFERENCE.md`` were git-clean copies an
earlier TRW wrote. The file is still kept (nothing proves it is TRW's); the report now says "no record" apart
from "edited", and both lines name ``update-project --rerender``, whose record lasts until the next plain update.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests._fs_hazards import assert_user_bytes_preserved, snapshot_user_bytes

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]

_REL = ".claude/skills/trw-handoff/RECEIVE.md"
_KEY = "trw-handoff/RECEIVE.md"
_SKILL_MD = ".claude/skills/trw-handoff/SKILL.md"
_NO_RECORD = "TRW has no record of writing it"
_EDITED = "you edited it since TRW last wrote it"
_OLDER = b"# Receiving a handoff\n\nThe text an earlier TRW release shipped here.\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _commit(root: Path, message: str) -> None:
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "--no-verify", "--allow-empty", "-m", message], check=True)


def _manifest(root: Path) -> dict[str, object]:
    return dict(yaml.safe_load((root / ".trw" / "managed-artifacts.yaml").read_text(encoding="utf-8")))


def _hashes(root: Path) -> dict[str, str]:
    return dict(_manifest(root)["content_hashes"])  # type: ignore[call-overload]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert not init_project(root, ide="claude-code")["errors"]
    assert (root / _REL).is_file(), "precondition: the bundled skill has this support file"
    _commit(root, "install")
    return root


def _cli_update(root: Path, capsys: pytest.CaptureFixture[str], **overrides: object) -> str:
    from trw_mcp.server._subcommands import _run_update_project

    args: dict[str, object] = {
        "target_dir": str(root),
        "pip_install": False,
        "dry_run": False,
        "ide": "claude-code",
        "log_json": False,
        "debug": False,
        "verbose": 0,
        "quiet": False,
        "reprovision": None,
        "rerender": None,
    }
    args.update(overrides)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exit_:
        _run_update_project(argparse.Namespace(**args))
    captured = capsys.readouterr()
    assert not exit_.value.code, captured.err
    return captured.out + captured.err


def _kept_line(out: str, name: str) -> str:
    lines = [line for line in out.splitlines() if line.startswith("WARNING: kept") and name in line]
    assert len(lines) == 1, out
    return lines[0]


def test_an_older_trw_copy_with_no_record_is_reported_as_unrecorded_never_as_edited(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reporter's state: a git-clean support file an earlier TRW wrote, for which no record exists."""
    assert _KEY not in _hashes(repo), "precondition: a plain install records no support file"
    (repo / _REL).write_bytes(_OLDER)
    _commit(repo, "what the earlier release left")
    before = snapshot_user_bytes(repo / ".claude")

    line = _kept_line(_cli_update(repo, capsys), "RECEIVE.md")

    assert f"WARNING: kept {_REL}: {_NO_RECORD}" in line
    assert "you edited" not in line
    assert f"trw-mcp update-project --rerender {_REL}" in line
    assert "it refuses and changes nothing if" in line
    assert (repo / _REL).read_bytes() == _OLDER, "unproven bytes are never replaced"
    assert _KEY not in _hashes(repo), "bytes TRW cannot prove it wrote never become its baseline (PRD-FIX-121)"
    assert_user_bytes_preserved(before, repo / ".claude")


def test_a_recorded_file_the_user_edited_is_kept_and_reported_as_edited(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert "trw-handoff/SKILL.md" in _hashes(repo), "precondition: SKILL.md has a record"
    mine = (repo / _SKILL_MD).read_bytes() + b"\n## Our own step\nAsk the on-call first.\n"
    (repo / _SKILL_MD).write_bytes(mine)
    _commit(repo, "our edit")
    before = snapshot_user_bytes(repo / ".claude")

    line = _kept_line(_cli_update(repo, capsys), "trw-handoff/SKILL.md")

    assert f"WARNING: kept {_SKILL_MD}: {_EDITED}" in line
    assert _NO_RECORD not in line
    assert f"trw-mcp update-project --rerender {_SKILL_MD}" in line
    assert (repo / _SKILL_MD).read_bytes() == mine
    assert _hashes(repo).get("trw-handoff/SKILL.md") != _sha(mine), "the user's bytes are never recorded as TRW's"
    assert_user_bytes_preserved(before, repo / ".claude")


def test_the_named_way_out_replaces_the_file_and_keeps_the_old_bytes_in_trash(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What both kept lines promise about ``--rerender``: TRW's version lands, the old bytes go to .trw/trash,
    the run says where, and the next update no longer keeps the file back."""
    shipped = (repo / _REL).read_bytes()
    (repo / _REL).write_bytes(_OLDER)
    _commit(repo, "what the earlier release left")

    out = _cli_update(repo, capsys, ide=None, rerender=[_REL])

    assert (repo / _REL).read_bytes() == shipped
    backup = [line for line in out.splitlines() if f"{_REL}: backup kept at" in line]
    assert len(backup) == 1 and ".trw/trash" in backup[0], out
    assert _sha(_OLDER) in snapshot_user_bytes(repo / ".trw" / "trash"), "the replaced bytes are kept, not deleted"
    assert _hashes(repo)[_KEY] == _sha(shipped)

    # The next plain update keeps no record for a support file (it says so: manifest_key_dropped), but the file
    # now equals TRW's version, so it is not kept back or replaced.
    again = _cli_update(repo, capsys)
    assert not [line for line in again.splitlines() if line.startswith("WARNING: kept") and "RECEIVE.md" in line]
    assert (repo / _REL).read_bytes() == shipped


def test_several_kept_files_share_one_line_that_still_names_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._update_report import print_kept

    a, b, c = (str(tmp_path / ".claude/skills/trw-handoff" / name) for name in ("RECEIVE.md", "REFERENCE.md", "X.md"))
    result: dict[str, list[str]] = {"modified": [a, b, c], "unrecorded": [a, b]}

    print_kept(result, tmp_path)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 2, lines
    grouped = next(line for line in lines if line.startswith("WARNING: kept 2 files"))
    assert _NO_RECORD in grouped and "RECEIVE.md" in grouped and "REFERENCE.md" in grouped
    assert "trw-mcp update-project --rerender <path>" in grouped
    single = next(line for line in lines if line.startswith("WARNING: kept .claude/skills/trw-handoff/X.md"))
    assert _EDITED in single and "--rerender .claude/skills/trw-handoff/X.md" in single


def test_a_kept_path_with_a_space_is_quoted_in_the_printed_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server._update_report import print_kept

    print_kept({"modified": [str(tmp_path / ".claude/hooks/my hook.sh")]}, tmp_path)

    assert "--rerender '.claude/hooks/my hook.sh'" in capsys.readouterr().out


def test_an_agent_with_no_record_is_reported_as_unrecorded(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    agent = next(p for p in sorted((repo / ".claude" / "agents").glob("trw-*.md")))
    manifest = _manifest(repo)
    assert manifest["content_hashes"].pop(agent.name), "precondition: the agent was recorded"  # type: ignore[attr-defined]
    (repo / ".trw" / "managed-artifacts.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    agent.write_bytes(agent.read_bytes() + b"\nA line from an older copy.\n")
    _commit(repo, "older agent copy, no record")

    line = _kept_line(_cli_update(repo, capsys), agent.name)

    assert _NO_RECORD in line and "you edited" not in line
    assert f"--rerender .claude/agents/{agent.name}" in line


# ── unchanged behaviour these reports sit beside: a skill is one unit for deletion and reprovision ──


def _bundled_files() -> list[str]:
    from trw_mcp.bootstrap._utils import _DATA_DIR

    return sorted(p.name for p in (_DATA_DIR / "skills" / "trw-handoff").iterdir() if p.is_file())


def test_a_deleted_skill_reprovisioned_by_its_skill_md_comes_back_whole(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill = repo / ".claude" / "skills" / "trw-handoff"
    assert set(_bundled_files()) - {"SKILL.md"}, "precondition: the skill ships support files"
    shutil.rmtree(skill)
    _commit(repo, "we deleted the skill")

    _cli_update(repo, capsys)

    assert not skill.exists(), "precondition: a deleted skill stays deleted"

    _cli_update(repo, capsys, reprovision=[_SKILL_MD])

    assert sorted(p.name for p in skill.iterdir()) == _bundled_files()


def test_a_support_file_deleted_alone_is_written_again(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shipped = (repo / _REL).read_bytes()
    (repo / _REL).unlink()
    _commit(repo, "one support file removed")

    _cli_update(repo, capsys)

    assert (repo / _REL).read_bytes() == shipped
