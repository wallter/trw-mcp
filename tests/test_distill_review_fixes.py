"""Regression evidence for the independent distill hook review."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from tests.channels.claude_code._distill_hint_support import (
    CHECKOUT_PYTHONPATH,
    deploy_distill_hint,
    init_isolated_repo,
)
from trw_mcp.channels.claude_code import _hook_helpers as helpers
from trw_mcp.channels.claude_code._explorer_subagent import get_explorer_agent_content
from trw_mcp.models.config._pre_edit_channels import render_pre_edit_hint_instruction
from trw_mcp.tools.code import _distill_remediation


@pytest.mark.parametrize(
    ("query", "response", "expected"),
    [
        ("callers", {"results": [{"symbol": "callers"}]}, None),
        ("callers foo", {"remediation": "index first"}, None),
        ("callers of foo", {}, None),
        ("callees", {}, None),
        ("callers a;touch", {}, "trw-distill query callers " + shlex.quote("a;touch")),
        ("foo;echo bad", {}, "trw-distill query def " + shlex.quote("foo;echo bad")),
    ],
)
def test_symbol_remediation(query, response, expected, monkeypatch):
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
    assert _distill_remediation(query, response) == expected


@pytest.mark.parametrize(
    ("status", "action", "state"),
    [
        ("sidecar_missing", None, "missing"),
        ("sidecar_malformed", None, "unreadable"),
        ("schema_mismatch", None, "built by an older version"),
        ("target_not_in_sidecar", None, "does not cover this file"),
        ("stale_sha", None, "out of date"),
        ("sidecar_too_far_behind", "nearest is 151 commits behind", "151 commits behind"),
    ],
)
def test_remedy_state_command_and_no_premature_marker(tmp_path, monkeypatch, status, action, state):
    repo = tmp_path / "repo with spaces"
    hints = repo / helpers.CC03_HINTS_DIR
    hints.mkdir(parents=True)
    cache = tmp_path / "shared cache"
    monkeypatch.setattr("trw_mcp.tools._sidecar_ancestry.shared_cache_dir", lambda root, rel: cache)
    line = helpers.sidecar_remedy_once(repo, "session", status, action)
    assert state in line
    command = line.split("run: ")[1]
    args = shlex.split(command)
    assert args[args.index("--repo") + 1] == str(repo)
    assert args[args.index("--cache-dir") + 1] == str(cache)
    assert not list(hints.iterdir()), "formatting is not proof of delivery"


def test_reviewer_remedy_writes_nothing(tmp_path, monkeypatch):
    hints = tmp_path / helpers.CC03_HINTS_DIR
    hints.mkdir(parents=True)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    helpers.sidecar_remedy_once(tmp_path, "session", "sidecar_missing", None)
    assert not list(hints.iterdir())


def test_explorer_and_static_guidance(monkeypatch):
    content = get_explorer_agent_content()
    assert "no-shell" not in content and "no shell access" not in content
    assert "not enforced by the harness" in content
    monkeypatch.setattr("trw_mcp.tools._sidecar_substrate.distill_installed", lambda: True)
    # One instruction that cannot be wrong for a linked worktree: no second, cache-less command.
    rendered = render_pre_edit_hint_instruction()
    assert "Exit 3 means the map cache is missing: run the command the hint prints.\n" in rendered
    assert "refresh-sidecars" not in rendered


def _setup(repo):
    init_isolated_repo(repo)
    (repo / ".trw/channels").mkdir(parents=True, exist_ok=True)
    (repo / ".trw/channels/cc03-python.txt").write_text(sys.executable)
    (repo / ".trw/config.yaml").write_text("cc03_hook_enabled: true\n")
    (repo / "a.py").write_text("")


def _hook(repo, target, tmp_path, *, slow=False, extra=None):
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    (stub / "sitecustomize.py").write_text("""
import os, sys, types, time, signal
if not os.environ.get("SLOW"):
    signal.setitimer = lambda *args: (0.0, 0.0)
from pathlib import Path
from types import SimpleNamespace
cache = types.ModuleType('trw_mcp.tools._sidecar_ancestry')
cache.shared_cache_dir = lambda repo, rel: repo / rel
sys.modules[cache.__name__] = cache
m = types.ModuleType('trw_mcp.tools._before_edit_hint_core')
m.T2_STATUSES = set()
def compute_before_edit_hint(file_path, repo_root=None):
    Path(os.environ['CAPTURE']).write_text(str(repo_root) + '\\n' + file_path)
    if os.environ.get('RAISE_HINT'):
        raise RuntimeError('fake failure')
    return SimpleNamespace(distill_status='sidecar_missing', distill_action=None, distill_as_of=None, distill_hint=None, learnings=[])
m.compute_before_edit_hint = compute_before_edit_hint
sys.modules[m.__name__] = m
if os.environ.get('SLOW'):
    from trw_mcp.channels.claude_code import _hook_helpers as h
    h.write_hint_file = lambda **kwargs: time.sleep(3)
""")
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "TRW_PROJECT_DIR": str(repo),
        "CLAUDE_PROJECT_DIR": str(repo),
        "PYTHONPATH": str(stub) + os.pathsep + CHECKOUT_PYTHONPATH,
        "CAPTURE": str(tmp_path / "capture"),
        "TRW_CC03_BOUND_S": "2.5",
        **(extra or {}),
    }
    if not slow:
        timeout_shim = stub / "timeout"
        timeout_shim.write_text('#!/bin/sh\nshift\nexec "$@"\n')
        timeout_shim.chmod(0o755)
        env["PATH"] = str(stub) + os.pathsep + env["PATH"]
    if slow:
        env["SLOW"] = "1"
    return subprocess.run(
        ["sh", str(deploy_distill_hint(repo))],
        cwd=repo,
        env=env,
        input=json.dumps(
            {
                "tool_name": "Edit",
                "tool_use_id": "review",
                "session_id": "session",
                "tool_input": {"file_path": str(target)},
            }
        ),
        capture_output=True,
        text=True,
        timeout=8,
    )


def test_main_session_uses_edited_worktree(tmp_path):
    main = tmp_path / "main"
    _setup(main)
    subprocess.run(
        ["git", "-C", str(main), "-c", "user.name=T", "-c", "user.email=t@t", "commit", "--allow-empty", "-qm", "init"],
        check=True,
    )
    wt = main / ".trw/worktrees/wt"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-qb", "wt", str(wt)], check=True)
    _setup(wt)
    result = _hook(main, wt / "a.py", tmp_path)
    assert result.returncode == 0
    assert (tmp_path / "capture").read_text().splitlines() == [str(wt), "a.py"]
    assert "--repo " + str(wt) in result.stdout


def test_timeout_does_not_consume_remedy(tmp_path):
    repo = tmp_path / "repo"
    _setup(repo)
    result = _hook(repo, repo / "a.py", tmp_path, slow=True)
    assert result.stdout == ""
    marker = repo / helpers.CC03_HINTS_DIR / ("sidecar-" + hashlib.sha256(b"session").hexdigest() + ".seen")
    assert not marker.exists()


@pytest.mark.parametrize("symlink", [False, True])
def test_hook_record_safe_and_private(tmp_path, symlink):
    repo = tmp_path / "repo"
    _setup(repo)
    hints = repo / helpers.CC03_HINTS_DIR
    hints.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if symlink:
        hints.symlink_to(outside, target_is_directory=True)
    result = _hook(repo, repo / "a.py", tmp_path)
    assert result.returncode == 0
    if symlink:
        assert list(outside.iterdir()) == []
    else:
        assert (hints / "review.json").stat().st_mode & 0o777 == 0o600


def test_post_commit_never_probes_python_in_foreground(tmp_path):
    repo = tmp_path / "repo"
    _setup(repo)
    fake = tmp_path / "python-worker"
    fake.write_text(
        '#!/bin/sh\ncase "$2" in *"import trw_mcp.tools._post_commit"*) touch "'
        + str(tmp_path / "probe")
        + '";; *) sleep 2;; esac\n'
    )
    fake.chmod(0o700)
    (repo / ".trw/channels/cc03-python.txt").write_text(str(fake))
    hook = Path(__file__).parents[1] / "src/trw_mcp/data/git_hooks/trw-post-commit.sh"
    result = subprocess.run(["sh", str(hook)], cwd=repo, capture_output=True, timeout=1)
    assert result.returncode == 0
    assert not (tmp_path / "probe").exists()


def test_delivered_remedy_is_suppressed_for_next_file(tmp_path):
    repo = tmp_path / "repo"
    _setup(repo)
    first = _hook(repo, repo / "a.py", tmp_path)
    assert "Sidecar missing" in first.stdout
    marker = helpers.sidecar_remedy_marker(repo, "session")
    assert marker.read_text() == "delivered\n"
    assert marker.stat().st_mode & 0o777 == 0o600
    second = _hook(repo, repo / "b.py", tmp_path)
    assert "Sidecar" not in second.stdout


@pytest.mark.parametrize("failure", ["exception", "timeout"])
def test_fallback_records_refuse_leaf_symlinks(tmp_path, failure):
    repo = tmp_path / "repo"
    _setup(repo)
    hints = repo / helpers.CC03_HINTS_DIR
    hints.mkdir(parents=True)
    outside = tmp_path / "keep"
    outside.write_text("KEEP")
    (hints / "review.json").symlink_to(outside)
    result = _hook(
        repo,
        repo / "a.py",
        tmp_path,
        slow=failure == "timeout",
        extra={"RAISE_HINT": "1"} if failure == "exception" else {},
    )
    assert result.returncode == 0
    assert outside.read_text() == "KEEP"
    assert (hints / "review.json").is_symlink()


@pytest.mark.parametrize("kind", ["unborn", "non-git", "shallow"])
def test_hook_degenerate_git_layouts_fail_open(tmp_path, kind):
    repo = tmp_path / "repo"
    if kind != "non-git":
        _setup(repo)
    else:
        repo.mkdir()
    if kind == "shallow":
        origin = tmp_path / "origin"
        _setup(origin)
        subprocess.run(
            [
                "git",
                "-C",
                str(origin),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@t",
                "commit",
                "--allow-empty",
                "-qm",
                "init",
            ],
            check=True,
        )
        clone = tmp_path / "clone"
        subprocess.run(["git", "clone", "-q", "--depth=1", origin.as_uri(), str(clone)], check=True)
        repo = clone
        _setup(repo)
    result = _hook(repo, repo / "a.py", tmp_path)
    assert result.returncode == 0
    if kind == "non-git":
        assert result.stdout == "" and not (repo / ".trw").exists()
    else:
        assert "Sidecar missing" in result.stdout


def test_exported_remedy_can_be_called_without_writing(tmp_path):
    namespace = {}
    exec("from trw_mcp.channels.claude_code._hook_helpers import *", namespace)
    assert "Sidecar missing" in namespace["sidecar_remedy_once"](tmp_path, "session", "sidecar_missing", None)
    assert not list(tmp_path.iterdir())
