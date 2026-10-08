"""Shared sidecar behavior against real repositories and linked worktrees."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH, deploy_distill_hint
from tests.test_sidecar_ancestry import _batch, _commit, _git, _repo, emitted  # noqa: F401
from tests.test_sidecar_rebuild_request import Ports, ports  # noqa: F401
from trw_mcp.channels.claude_code._hook_helpers import sidecar_remedy_once
from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint
from trw_mcp.tools._post_commit import _request_sidecar_rebuild
from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL


def _linked(tmp_path: Path) -> tuple[Path, Path, str]:
    main, head = _repo(tmp_path)
    wt = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", "--detach", str(wt))
    (wt / ".trw").mkdir()
    (wt / ".trw/entitlements.yaml").write_bytes((main / ".trw/entitlements.yaml").read_bytes())
    return main, wt, head


@pytest.mark.parametrize("empty_local", [False, True])
@pytest.mark.parametrize("descendant", [False, True])
def test_shared_exact_and_ancestor(
    tmp_path: Path, emitted: list[dict[str, Any]], empty_local: bool, descendant: bool
) -> None:
    main, wt, head = _linked(tmp_path)
    _batch(main, head)
    if empty_local:
        (wt / DEFAULT_CACHE_DIR_REL).mkdir(parents=True)
    if descendant:
        _commit(wt, "other.py", "y = 1\n")
    result = compute_before_edit_hint(file_path="foo.py", repo_root=str(wt))
    assert result.distill_status == ("hint_available_stale" if descendant else "hint_available")
    assert result.distill_hint is not None
    assert result.distill_hint.risk_score == 0.42
    if descendant:
        assert result.distill_as_of is not None
        assert result.distill_as_of.commits_behind == 1


def test_nearest_shared_commit_wins(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    main, wt, head = _linked(tmp_path)
    _batch(main, head, mtime=900)
    nearer = _commit(wt, "other.py", "y = 1\n")
    _batch(main, nearer, mtime=100)
    _commit(wt, "third.py", "z = 1\n")
    result = compute_before_edit_hint(file_path="foo.py", repo_root=str(wt))
    assert result.distill_as_of is not None
    assert result.distill_as_of.sidecar_sha == nearer
    assert result.distill_as_of.commits_behind == 1


def test_post_commit_refresh_uses_worktree_head_and_shared_cache(tmp_path: Path, ports: Ports) -> None:
    main, wt, _ = _linked(tmp_path)
    assert _request_sidecar_rebuild(main, {}) == "spawned"
    head = _commit(wt, "other.py", "y = 1\n")
    (wt / DEFAULT_CACHE_DIR_REL).mkdir(parents=True)
    assert _request_sidecar_rebuild(wt, {"TRW_PROJECT_DIR": str(main)}) == "min_interval"
    ports.clock.now += 15 * 60
    assert _request_sidecar_rebuild(wt, {"TRW_PROJECT_DIR": str(main)}) == "spawned"
    argv, kwargs = ports.popen.calls[-1]
    build_repo = Path(argv[argv.index("--repo") + 1])
    cache = Path(argv[argv.index("--cache-dir") + 1])
    assert _git(build_repo, "rev-parse", "HEAD") == head
    assert cache == main / DEFAULT_CACHE_DIR_REL
    assert kwargs["cwd"] == wt
    assert kwargs["env"]["TRW_PROJECT_DIR"] == str(wt)
    assert kwargs["start_new_session"] is True
    assert kwargs["pass_fds"]
    _batch(main, head)  # producer contract: commit-keyed artifact at the requested cache
    assert _request_sidecar_rebuild(wt, {}) == "not_due"


@pytest.mark.parametrize(
    ("status", "action", "state"),
    [
        ("sidecar_missing", None, "missing"),
        ("sidecar_too_far_behind", "the nearest is 501 commits behind", "501 commits behind"),
        ("target_not_in_sidecar", None, "does not cover this file"),
    ],
)
def test_remedy_once_per_session(tmp_path: Path, status: str, action: str | None, state: str) -> None:
    _, wt, _ = _linked(tmp_path)
    (wt / ".trw/context/cc03-hints").mkdir(parents=True)
    assert sidecar_remedy_once(wt, "session", "hint_available", None) == ""
    first = sidecar_remedy_once(wt, "session", status, action)
    assert state in first
    assert f"trw-distill self-improve refresh-sidecars --repo {wt}" in first
    command = shlex.split(first.split("run: ", 1)[1])
    from trw_mcp.tools._sidecar_ancestry import shared_cache_dir

    assert Path(command[command.index("--cache-dir") + 1]) == shared_cache_dir(wt, DEFAULT_CACHE_DIR_REL)
    from trw_mcp.channels.claude_code._hook_helpers import sidecar_remedy_marker

    assert sidecar_remedy_once(wt, "session", status, action) == first
    sidecar_remedy_marker(wt, "session").write_text("")
    assert sidecar_remedy_once(wt, "session", status, action) == ""
    assert sidecar_remedy_once(wt, "next-session", status, action) == first


def _behavior_clock(tmp_path: Path) -> dict[str, str]:
    """Remove scheduler races from content assertions; deadline tests own expiry."""
    shim = tmp_path / "behavior-clock"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text("import signal\nsignal.setitimer = lambda *args: (0.0, 0.0)\n")
    timeout = shim / "timeout"
    timeout.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    timeout.chmod(0o755)
    return {
        "PATH": str(shim) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(shim) + os.pathsep + CHECKOUT_PYTHONPATH,
    }


def test_real_hook_emits_remedy_once_across_files(tmp_path: Path) -> None:
    _, wt, _ = _linked(tmp_path)
    channels = wt / ".trw/channels"
    channels.mkdir()
    (channels / "cc03-python.txt").write_text(sys.executable)
    (wt / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    hook = deploy_distill_hint(wt)
    behavior_env = _behavior_clock(tmp_path)
    outputs = []
    for index, target in enumerate(["foo.py", "other.py"]):
        proc = subprocess.run(
            ["sh", str(hook)],
            cwd=wt,
            capture_output=True,
            text=True,
            timeout=30,
            input=json.dumps(
                {
                    "session_id": "test-session",
                    "tool_use_id": f"test-{index}",
                    "tool_name": "Edit",
                    "tool_input": {"file_path": str(wt / target)},
                }
            ),
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path),
                "PYTHONPATH": CHECKOUT_PYTHONPATH,
                "TRW_HINT_SIDECAR_REFRESH_ENABLED": "true",
                **behavior_env,
            },
        )
        assert proc.returncode == 0
        outputs.append(
            json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"] if proc.stdout.strip() else ""
        )
    assert "Sidecar missing" in outputs[0]
    assert "refresh-sidecars" in outputs[0]
    assert "Sidecar missing" not in outputs[1]


def test_post_commit_shell_ignores_stale_project_env(tmp_path: Path) -> None:
    main, wt, _ = _linked(tmp_path)
    head = _commit(wt, "other.py", "y = 1\n")
    fake = tmp_path / "python-recorder"
    record = tmp_path / "record.json"
    fake.write_text(
        "#!" + sys.executable + "\nimport os,json\nfrom pathlib import Path\n"
        'if "TRW_POST_COMMIT_REPO" in os.environ:\n'
        f' Path({str(record)!r}).write_text(json.dumps([os.environ["TRW_POST_COMMIT_REPO"], os.environ["TRW_POST_COMMIT_HEAD"]]))\n'
    )
    fake.chmod(0o755)
    (wt / ".trw/channels").mkdir()
    (wt / ".trw/channels/cc03-python.txt").write_text(str(fake))
    hook = Path(__file__).parents[1] / "src/trw_mcp/data/git_hooks/trw-post-commit.sh"
    subprocess.run(
        ["sh", str(hook)],
        cwd=wt,
        check=True,
        timeout=15,
        env={**os.environ, "TRW_PROJECT_DIR": str(main), "TRW_POST_COMMIT_SYNC": "1"},
    )
    assert json.loads(record.read_text()) == [str(wt), head]


def test_invalid_newer_sidecar_does_not_hide_usable_ancestor(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    main, wt, head = _linked(tmp_path)
    _batch(main, head)
    current = _commit(wt, "other.py", "y = 1\n")
    _batch(main, current).write_text("{corrupt")
    result = compute_before_edit_hint(file_path="foo.py", repo_root=str(wt))
    assert result.distill_status == "hint_available_stale"
    assert result.distill_as_of is not None
    assert result.distill_as_of.sidecar_sha == head


def test_detached_refresh_records_worktree_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the real detached spawn with a tiny CLI contract producer, never a map build."""
    import time

    from trw_mcp.tools import _distill_spawn
    from trw_mcp.tools._distill_spawn import SpawnPorts

    main, wt, _ = _linked(tmp_path)
    head = _commit(wt, "other.py", "y = 1\n")
    cli = tmp_path / "trw-distill"
    cli.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json, subprocess, sys
from pathlib import Path
args = sys.argv
repo = Path(args[args.index('--repo') + 1])
cache = Path(args[args.index('--cache-dir') + 1])
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
cache.mkdir(parents=True, exist_ok=True)
(cache / ('before-edit-batch-' + sha + '.json')).write_text(json.dumps({
    'schema_version': 'risk-report-sidecar/v0', 'sha': sha,
    'payload': {'hints': [{'target_path': 'foo.py', 'target_exists_in_map': True, 'risk_score': 0.42}]}}))
(cache / 'build-receipt.json').write_text(json.dumps({'sha': sha, 'repo': str(repo)}))
"""
    )
    cli.chmod(0o755)
    monkeypatch.setenv("TRW_HINT_SIDECAR_REFRESH_ENABLED", "true")
    monkeypatch.setattr(
        _distill_spawn,
        "DEFAULT_PORTS",
        SpawnPorts(which=lambda name, path: str(cli) if name == "trw-distill" else "/usr/bin/nice"),
    )
    assert _request_sidecar_rebuild(wt, {"PATH": os.environ["PATH"], "TRW_PROJECT_DIR": str(main)}) == "spawned"
    receipt = main / DEFAULT_CACHE_DIR_REL / "build-receipt.json"
    deadline = time.monotonic() + 5
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert json.loads(receipt.read_text()) == {"sha": head, "repo": str(wt)}
    assert _request_sidecar_rebuild(wt, {}) == "not_due"


@pytest.mark.parametrize("layout", ["shallow", "unborn", "non_git"])
def test_real_hook_handles_incomplete_repositories(tmp_path: Path, layout: str) -> None:
    source, old = _repo(tmp_path)
    _commit(source, "other.py", "x=2\n")
    repo = tmp_path / "hook-repo"
    if layout == "shallow":
        _git(tmp_path, "clone", "-q", "--depth=1", source.as_uri(), str(repo))
    else:
        repo.mkdir()
        if layout == "unborn":
            _git(repo, "init", "-q")
    (repo / ".trw/channels").mkdir(parents=True)
    (repo / ".trw/channels/cc03-python.txt").write_text(sys.executable)
    (repo / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    (repo / ".trw/entitlements.yaml").write_bytes((source / ".trw/entitlements.yaml").read_bytes())
    if layout == "shallow":
        _batch(repo, old)  # the sidecar commit is unavailable until a later fetch
    hook = deploy_distill_hint(repo)
    proc = subprocess.run(
        ["sh", str(hook)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=8,
        input=json.dumps(
            {
                "session_id": "edge",
                "tool_use_id": "edge-1",
                "tool_name": "Edit",
                "tool_input": {"file_path": str(repo / "foo.py")},
            }
        ),
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "PYTHONPATH": CHECKOUT_PYTHONPATH},
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
    assert "RISK: 0.42" not in proc.stdout  # the text the hook prints for a served sidecar
    assert not list((repo / DEFAULT_CACHE_DIR_REL).glob("**/rebuild-requested.json"))
