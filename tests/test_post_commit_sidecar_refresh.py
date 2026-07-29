"""PRD-CORE-231-FR01: post-commit T2 sidecar refresh.

The sidecar cache is keyed by HEAD sha, so every commit invalidates the T2 tier
and the PreToolUse hook falls back to T1/T0 forever. These tests cover the
planner that decides what the post-commit hook regenerates, its env hygiene,
and the bundled hook's fail-open contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._hint_sidecar_refresh import (
    RefreshPlan,
    build_subprocess_specs,
    changed_files,
    resolve_refresh_plan,
    sanitized_env,
)

_HOOK = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "git_hooks" / "trw-post-commit.sh"


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A git repo with one commit touching three files."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def _plan(repo: Path, **overrides: object) -> RefreshPlan:
    config = TRWConfig()
    kwargs: dict[str, object] = {
        "enabled": config.hint_sidecar_refresh_enabled,
        "file_cap": config.hint_sidecar_refresh_file_cap,
        "distill_available": True,
    }
    kwargs.update(overrides)
    return resolve_refresh_plan(repo, **kwargs)  # type: ignore[arg-type]


def test_regenerates_sidecar_for_changed_files(git_repo: Path) -> None:
    """The plan covers exactly the files HEAD touched."""
    plan = _plan(git_repo)

    assert plan.should_run
    assert set(plan.files) == {"a.py", "b.py", "c.py"}
    assert not plan.truncated

    specs = build_subprocess_specs(plan, git_repo, {"PATH": "/usr/bin"})
    assert len(specs) == 3
    argv = specs[0].argv
    assert argv[:3] == ("trw-distill", "self-improve", "before-edit")
    assert "--persist-sidecar" in argv
    assert str(git_repo) in argv


def test_file_cap_bounds_the_plan(git_repo: Path) -> None:
    """NFR01: a large commit cannot fan out unbounded subprocesses."""
    plan = _plan(git_repo, file_cap=2)

    assert len(plan.files) == 2
    assert plan.truncated


def test_disabled_flag_is_a_no_op(git_repo: Path) -> None:
    """FR01 rollback: the config switch turns the whole mechanism off."""
    plan = _plan(git_repo, enabled=False)

    assert not plan.should_run
    assert plan.skipped_reason == "disabled_by_config"
    assert build_subprocess_specs(plan, git_repo, {}) == ()


def test_missing_distill_is_a_silent_no_op(git_repo: Path) -> None:
    """Free-tier fail-open contract, unchanged: no entitlement => no work."""
    plan = _plan(git_repo, distill_available=False)

    assert not plan.should_run
    assert plan.skipped_reason == "distill_unavailable"


def test_non_git_directory_degrades(tmp_path: Path) -> None:
    """No git => empty plan, never an exception."""
    assert changed_files(tmp_path) == ()

    plan = _plan(tmp_path)
    assert not plan.should_run
    assert plan.skipped_reason == "no_changed_files"


def test_subprocess_env_is_sanitized() -> None:
    """NFR03: an explicit allowlist, never os.environ passthrough."""
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/dev",
        "AWS_SECRET_ACCESS_KEY": "super-secret",
        "GITHUB_TOKEN": "ghp_secret",
        "TRW_PLATFORM_API_KEY": "secret",
    }

    env = sanitized_env(source)

    assert env == {"PATH": "/usr/bin", "HOME": "/home/dev"}
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "TRW_PLATFORM_API_KEY" not in env


def test_specs_carry_the_sanitized_env(git_repo: Path) -> None:
    """The allowlist reaches the actual invocation, not just the helper."""
    plan = _plan(git_repo)

    specs = build_subprocess_specs(plan, git_repo, {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_secret"})

    assert specs
    for spec in specs:
        assert "GITHUB_TOKEN" not in spec.env


def test_shell_metacharacter_paths_are_argv_not_interpolated(git_repo: Path) -> None:
    """A hostile filename must land as one argv element, never as shell text."""
    hostile = "evil$(touch /tmp/pwned).py"
    plan = RefreshPlan(files=(hostile,))

    specs = build_subprocess_specs(plan, git_repo, {})

    assert hostile in specs[0].argv
    joined = " ".join(specs[0].argv)
    assert joined.count(hostile) == 1


def _run_refresh_with_stub_cli(repo: Path, exit_code: int) -> list[dict[str, object]]:
    """Drive ``run_post_commit_refresh`` against a REAL subprocess with a stub CLI.

    A ``trw-distill`` shim on PATH exits with *exit_code*, so the returncode
    under test comes from an actual ``CompletedProcess``, not a mock. Returns
    the captured structlog events.
    """
    from structlog.testing import capture_logs

    import trw_mcp.tools._hint_sidecar_refresh as refresh_mod

    bindir = repo / "stub-bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "trw-distill"
    shim.write_text(f'#!/bin/sh\necho "stub failure" >&2\nexit {exit_code}\n', encoding="utf-8")
    shim.chmod(0o755)

    with (
        pytest.MonkeyPatch.context() as mp,
        capture_logs() as logs,
    ):
        mp.setattr(refresh_mod, "distill_available", lambda: True)
        refresh_mod.run_post_commit_refresh(repo, {"PATH": f"{bindir}:/usr/bin:/bin"})
    return list(logs)


def _completion(logs: list[dict[str, object]]) -> dict[str, object]:
    return next(e for e in logs if e.get("event") == "sidecar_refresh_complete")


def test_failed_refreshes_are_not_logged_as_completed(git_repo: Path) -> None:
    """A run in which every invocation failed must not read like N successes.

    ``run_post_commit_refresh`` used ``check=False`` and never inspected
    ``returncode``, then logged ``sidecar_refresh_complete files=N``
    unconditionally — indistinguishable from "all N succeeded". That is not
    hypothetical: ``trw-distill`` missing from the hook's sanitized PATH fails
    every invocation, and the line still read like success.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=3)

    completion = _completion(logs)
    assert completion["files"] == 3
    assert completion["succeeded"] == 0
    assert completion["failed"] == 3

    failures = [e for e in logs if e.get("event") == "sidecar_refresh_subprocess_nonzero_exit"]
    assert {e["file_path"] for e in failures} == {"a.py", "b.py", "c.py"}
    assert {e["returncode"] for e in failures} == {3}
    # A maintainer-needed failure reason must not go to `debug` — under a
    # default install that level reaches nowhere, which deletes the signal
    # rather than moving it (.claude/rules/trw-mcp-python.md).
    assert {e["log_level"] for e in failures} == {"warning"}


def test_successful_refreshes_are_counted_as_successes(git_repo: Path) -> None:
    """Non-vacuity control for the test above.

    An implementation that reported ``failed=len(plan.files)`` unconditionally,
    or that warned on every invocation, would pass the failure test. The same
    code path with an exit-0 CLI must report the opposite.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=0)

    completion = _completion(logs)
    assert completion["succeeded"] == 3
    assert completion["failed"] == 0
    assert not [e for e in logs if e.get("event") == "sidecar_refresh_subprocess_nonzero_exit"]


def test_unspawnable_cli_is_counted_and_warned(git_repo: Path) -> None:
    """An OSError (binary absent) is a failure too, not an unrecorded no-op."""
    from structlog.testing import capture_logs

    import trw_mcp.tools._hint_sidecar_refresh as refresh_mod

    with (
        pytest.MonkeyPatch.context() as mp,
        capture_logs() as logs,
    ):
        mp.setattr(refresh_mod, "distill_available", lambda: True)
        # An empty PATH makes exec of "trw-distill" raise FileNotFoundError.
        refresh_mod.run_post_commit_refresh(git_repo, {"PATH": str(git_repo / "no-such-bin")})

    completion = _completion(list(logs))
    assert completion["succeeded"] == 0
    assert completion["failed"] == 3

    spawn_failures = [e for e in logs if e.get("event") == "sidecar_refresh_subprocess_failed"]
    assert len(spawn_failures) == 3
    assert {e["log_level"] for e in spawn_failures} == {"warning"}


def test_refresh_failures_stay_out_of_the_return_value(git_repo: Path) -> None:
    """Diagnostics go to structlog, never to the caller-facing result.

    ``RefreshPlan`` is what the hook was asked to run; per-invocation outcomes
    are maintainer signal and must not widen it.
    """
    from dataclasses import fields

    field_names = {f.name for f in fields(RefreshPlan)}
    assert field_names == {"files", "skipped_reason", "truncated"}


def test_bundled_hook_exists_and_is_posix_sh() -> None:
    """The hook ships in the bundled data dir and parses under POSIX sh."""
    assert _HOOK.is_file()
    completed = subprocess.run(["sh", "-n", str(_HOOK)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_bundled_hook_never_blocks_the_commit(tmp_path: Path) -> None:
    """NFR02: the hook exits 0 even with no Python, no distill, no repo state."""
    completed = subprocess.run(
        ["sh", str(_HOOK)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "TRW_PROJECT_DIR": str(tmp_path)},
        timeout=60,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_bundled_hook_does_not_interpolate_repo_path_into_python() -> None:
    """The repo path reaches Python via env, so a quoted path cannot inject code."""
    body = _HOOK.read_text(encoding="utf-8")

    assert "TRW_POST_COMMIT_REPO" in body
    assert 'os.environ["TRW_POST_COMMIT_REPO"]' in body
    # The classic injection shape — "$_repo" pasted inside the -c program.
    assert "Path('$_repo')" not in body
