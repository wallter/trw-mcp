"""E2E-INC-142 (truthfulness): the update report must match the bytes on disk for a file kept for its uncommitted
changes.

swarm-e2e on canary 8.1.8.dev1 (S3): a git-dirty ``.claude/settings.json`` without a ``hooks`` key was reported as
"kept ... this update did not refresh it", yet the update rewrote it (``apply_cc03_hook_registration`` re-adds
TRW's PreToolUse entry after the uncommitted-changes restore, on purpose). r2 (codex r1 KIs): the report compares
the FINAL bytes, after tombstones and any rollback, with what the restore put back, so it never claims an
untouched file changed nor a changed file untouched, and it makes no claim about which entries changed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration

_SETTINGS = ".claude/settings.json"
_CHANGED = "later steps then changed TRW's own entries"
_UNTOUCHED = "did not refresh it"


def _real_git(repo: Path) -> None:
    # CC-03 on, as on a licensed install (swarm-e2e S3): the re-apply that rewrites settings.json runs only then.
    config = repo / ".trw" / "config.yaml"
    config.write_text(config.read_text() + "\ncc03_hook_enabled: true\n")
    shutil.rmtree(repo / ".git")
    env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x", "GIT_COMMITTER_NAME": "x",
           "GIT_COMMITTER_EMAIL": "x@x"}  # fmt: skip
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)


def _commit(repo: Path) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@x", "GIT_COMMITTER_NAME": "x",
           "GIT_COMMITTER_EMAIL": "x@x"}  # fmt: skip
    for cmd in (["git", "add", "-A"], ["git", "commit", "-qm", "warm-up"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)


def _why(result: dict[str, list[str]], repo: Path) -> str | None:
    from trw_mcp.server._update_report import kept_files

    return dict(kept_files(result, repo)).get(_SETTINGS)


def test_a_kept_settings_json_that_trw_then_changed_is_reported_as_changed(initialized_repo: Path) -> None:
    from trw_mcp.bootstrap._update_project import update_project

    repo = initialized_repo
    _real_git(repo)
    (repo / _SETTINGS).write_text(json.dumps({"concurrent": 824}) + "\n")  # uncommitted, without TRW's hooks
    before = (repo / _SETTINGS).read_bytes()

    result = update_project(repo, ide="claude-code")

    assert not result["errors"], result["errors"]  # not a failure path: the update ran to the end
    after = (repo / _SETTINGS).read_bytes()
    assert after != before, "precondition: the CC-03 re-apply changed the kept file"
    assert json.loads(after)["concurrent"] == 824, "the user's key survives"
    why = _why(result, repo)
    assert why is not None and _CHANGED in why and _UNTOUCHED not in why, why
    assert "unchanged" not in why, "the report may not claim which other entries were left alone"
    assert "_kept_digests" not in result


def test_a_kept_settings_json_left_byte_identical_is_reported_as_untouched(initialized_repo: Path) -> None:
    from trw_mcp.bootstrap._update_project import update_project

    repo = initialized_repo
    _real_git(repo)
    update_project(repo, ide="claude-code")  # warm-up: settings.json gains the CC-03 entry, then commit it
    _commit(repo)
    current = json.loads((repo / _SETTINGS).read_text())  # already carries TRW's entries
    current["concurrent"] = 1
    mine = (json.dumps(current, indent=2) + "\n").encode()
    (repo / _SETTINGS).write_bytes(mine)

    result = update_project(repo, ide="claude-code")

    assert not result["errors"], result["errors"]
    assert (repo / _SETTINGS).read_bytes() == mine, "precondition: nothing changed the kept file"
    why = _why(result, repo)
    # Either named as kept-untouched or (nothing of TRW's needed changing) not named at all; never "changed".
    assert why is None or (_UNTOUCHED in why and _CHANGED not in why), why


def test_a_rolled_back_update_never_claims_the_kept_file_changed(
    initialized_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r1 KI3: the snapshot restores the original bytes, so no change may be claimed."""
    from trw_mcp.bootstrap import _update_project as up

    repo = initialized_repo
    _real_git(repo)
    mine = (json.dumps({"concurrent": 7}) + "\n").encode()
    (repo / _SETTINGS).write_bytes(mine)

    def fail(*_a: object, **_k: object) -> None:
        raise OSError("manifest write failed")

    monkeypatch.setattr(up, "enforce_and_write_manifest", fail)
    result = up.update_project(repo, ide="claude-code")

    assert result["errors"], "precondition: the update failed and rolled back"
    assert (repo / _SETTINGS).read_bytes() == mine, "precondition: the rollback put the user's bytes back"
    # codex r2 KI3: no change claim of ANY wording (r1's "changed only TRW's own hook entry" included).
    assert not [e for e in result.get("preserved", []) if "changed" in str(e) or "merged" in str(e)], result
    why = _why(result, repo)
    assert why is None or _UNTOUCHED in why, f"a rolled-back kept file may only be reported untouched: {why}"


def test_every_spelling_of_a_changed_kept_file_is_relabelled(tmp_path: Path) -> None:
    """codex r1 KI4: an absolute and a relative entry for the same file are both relabelled."""
    from trw_mcp.bootstrap._update_phases import record_kept_digests, relabel_kept_files_that_changed
    from trw_mcp.server._update_report import kept_files

    (tmp_path / ".claude").mkdir()
    target = tmp_path / _SETTINGS
    target.write_bytes(b"{}\n")
    result: dict[str, list[str]] = {
        "preserved": [f"{target} (uncommitted_changes)", f"{_SETTINGS} (uncommitted_changes)"],
    }
    record_kept_digests(tmp_path, result)
    target.write_bytes(b'{"hooks": {}}\n')
    relabel_kept_files_that_changed(tmp_path, result)

    assert all("uncommitted_changed_after_keep" in e for e in result["preserved"]), result["preserved"]
    assert _CHANGED in dict(kept_files(result, tmp_path))[_SETTINGS]


def test_a_path_containing_a_tab_is_never_confused_with_another_kept_file(tmp_path: Path) -> None:
    """codex r2 KI2: digests are structured, so a tab in a preserved path cannot split into another entry."""
    from trw_mcp.bootstrap._update_phases import record_kept_digests, relabel_kept_files_that_changed

    (tmp_path / ".claude").mkdir()
    (tmp_path / _SETTINGS).write_bytes(b"{}\n")
    (tmp_path / ".claude" / "settings.json\tbackup").write_bytes(b"other\n")
    result: dict[str, list[str]] = {
        "preserved": [f"{_SETTINGS} (uncommitted_changes)", f"{_SETTINGS}\tbackup (uncommitted_changes)"],
    }
    record_kept_digests(tmp_path, result)
    relabel_kept_files_that_changed(tmp_path, result)  # nothing changed
    assert all(e.endswith("(uncommitted_changes)") for e in result["preserved"]), result["preserved"]
