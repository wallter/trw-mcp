"""PRD-DIST-2482 FR04: post-commit refreshes the risk report and, opt-in, spawns incremental ingest.

A fake ``trw-distill`` executable on PATH stands in for the proprietary CLI (the
IP boundary keeps trw-mcp from importing it). It writes the risk-report
envelope exactly as ``persist_sidecar`` does, and records the argv of every
invocation so the tests can see what the post-commit worker actually ran.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

import trw_mcp.tools._post_commit as pc
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._post_commit_distill import (
    DISTILL_CLI_UNAVAILABLE,
    INCREMENTAL_LOCK_REL,
    refresh_risk_report,
    spawn_incremental_run,
)

_FAKE_CLI = """#!{python}
import json, os, sys, time
from pathlib import Path
argv = sys.argv[1:]
log = Path(os.environ["FAKE_DISTILL_LOG"])
with log.open("a") as sink:
    sink.write(json.dumps(argv) + "\\n")
if argv[:2] == ["self-improve", "risk-report"] and os.environ.get("FAKE_DISTILL_FAIL") != "1":
    repo = Path(argv[argv.index("--repo") + 1])
    sha = os.popen("git -C '%s' rev-parse HEAD" % repo).read().strip()
    out = repo / ".trw" / "distill" / "map-cache" / ("risk-report-%s.json" % sha)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = [{{"path": "a.py", "composite_score": 0.3, "churn_score": 0.5}}]
    out.write_text(json.dumps({{"schema_version": "risk-report-sidecar/v0", "sha": sha,
                               "generated_at_unix": time.time(), "payload": payload}}))
time.sleep(float(os.environ.get("FAKE_DISTILL_SLEEP", "0")))
sys.exit(1 if os.environ.get("FAKE_DISTILL_FAIL") == "1" else 0)
"""


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *cmd], cwd=root, check=True)
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return root


@pytest.fixture()
def fake_cli(tmp_path: Path) -> dict[str, str]:
    """An env whose PATH resolves ``trw-distill`` to the recorder above."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "trw-distill"
    cli.write_text(_FAKE_CLI.format(python=sys.executable), encoding="utf-8")
    cli.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    log.touch()
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "FAKE_DISTILL_LOG": str(log)}


def _calls(env: dict[str, str]) -> list[list[str]]:
    lines = Path(env["FAKE_DISTILL_LOG"]).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _allow_fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake needs its log path through the child-env allowlist; nothing else is widened."""
    import trw_mcp.tools._distill_spawn as spawn

    monkeypatch.setattr(
        spawn,
        "_ENV_ALLOWLIST",
        (*spawn._ENV_ALLOWLIST, "FAKE_DISTILL_LOG", "FAKE_DISTILL_FAIL", "FAKE_DISTILL_SLEEP"),
    )


def _head(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def test_refresh_risk_report_is_true_only_for_a_current_sidecar_it_wrote(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_fake_env(monkeypatch)
    assert refresh_risk_report(repo, fake_cli) is True
    artifact = repo / ".trw" / "distill" / "map-cache" / f"risk-report-{_head(repo)}.json"
    assert json.loads(artifact.read_text(encoding="utf-8"))["sha"] == _head(repo)
    assert _calls(fake_cli) == [["self-improve", "risk-report", "--repo", str(repo), "--persist-sidecar"]]


def test_a_failed_producer_next_to_an_old_artifact_reports_false(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit codes are not the record: the pre-existing current-sha artifact must not be credited."""
    _allow_fake_env(monkeypatch)
    assert refresh_risk_report(repo, fake_cli) is True
    time.sleep(0.01)
    assert refresh_risk_report(repo, {**fake_cli, "FAKE_DISTILL_FAIL": "1"}) is False


def test_a_missing_cli_is_false_not_an_exception(repo: Path) -> None:
    assert refresh_risk_report(repo, {"PATH": "/nonexistent"}) is False


def test_a_missing_cli_never_attempts_a_spawn(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The package-importable-but-CLI-absent case (confirmed in dev worktrees): no distill subprocess is tried.

    ``resolve_git_sha`` legitimately shells out to plain ``git`` first, so only
    the distill-CLI invocation itself is asserted against — never attempted
    once the CLI cannot be resolved on PATH.
    """
    real_run = subprocess.run

    def _guard(argv: object, *args: object, **kwargs: object) -> object:
        if isinstance(argv, (list, tuple)) and "self-improve" in argv:
            raise AssertionError("the distill CLI must not be invoked when it cannot be resolved")
        return real_run(argv, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", _guard)
    assert refresh_risk_report(repo, {"PATH": "/nonexistent"}) is False


def test_spawn_incremental_reports_distill_cli_unavailable_without_attempting_a_spawn(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("subprocess.Popen must not be attempted when the CLI cannot be resolved")

    monkeypatch.setattr(subprocess, "Popen", _fail_if_called)
    assert spawn_incremental_run(repo, {"PATH": "/nonexistent"}, enabled=True) == DISTILL_CLI_UNAVAILABLE
    assert not (repo / INCREMENTAL_LOCK_REL).exists()


def test_spawn_incremental_resolves_and_uses_the_cli_when_present(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The available-CLI branch: the resolved absolute path is what gets exec'd."""
    _allow_fake_env(monkeypatch)
    assert spawn_incremental_run(repo, fake_cli, enabled=True) == "spawned"
    assert _wait_for(lambda: bool(_run_calls(fake_cli)))


def _wait_for(predicate: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _lock_is_free(repo: Path) -> bool:
    fd = os.open(repo / INCREMENTAL_LOCK_REL, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # trw-fail-silent-allow: BlockingIOError is how a held lock answers; False is the true result
    except BlockingIOError:
        return False
    finally:
        os.close(fd)  # closing releases a lock we took
    return True


def _run_calls(env: dict[str, str]) -> list[list[str]]:
    return [call for call in _calls(env) if call[:1] == ["run"]]


def test_incremental_spawns_only_when_flag_true(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _allow_fake_env(monkeypatch)
    assert TRWConfig().post_commit_distill_incremental is False  # opt-in by default
    assert spawn_incremental_run(repo, fake_cli, enabled=False) == "disabled"
    assert not (repo / INCREMENTAL_LOCK_REL).exists()

    assert spawn_incremental_run(repo, fake_cli, enabled=True) == "spawned"
    assert _wait_for(lambda: bool(_run_calls(fake_cli)))
    assert _run_calls(fake_cli) == [
        ["run", "--repo", str(repo), "--incremental", "--live-ingest", "--trigger", "post_commit"]
    ]


def test_the_child_holds_the_lock_for_its_lifetime_then_releases_it(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Single-flight while the run lives; the kernel frees the lock when it exits, leaving no stale state."""
    _allow_fake_env(monkeypatch)
    env = {**fake_cli, "FAKE_DISTILL_SLEEP": "1.5"}

    assert spawn_incremental_run(repo, env, enabled=True) == "spawned"
    assert _wait_for(lambda: bool(_run_calls(fake_cli)))
    assert spawn_incremental_run(repo, env, enabled=True) == "already_running"
    assert len(_run_calls(fake_cli)) == 1

    assert _wait_for(lambda: _lock_is_free(repo))
    assert spawn_incremental_run(repo, fake_cli, enabled=True) == "spawned"
    assert _wait_for(lambda: len(_run_calls(fake_cli)) == 2)


def test_a_stale_pid_file_of_an_unrelated_live_process_does_not_block(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewed defect: a recycled pid must not suppress ingest. Only the held lock counts."""
    _allow_fake_env(monkeypatch)
    old_pid_file = repo / ".trw" / "runtime" / "post-commit-distill-run.pid"
    old_pid_file.parent.mkdir(parents=True)
    old_pid_file.write_text(str(os.getpid()), encoding="utf-8")  # alive, and not a distill run
    (repo / INCREMENTAL_LOCK_REL).parent.mkdir(parents=True)
    (repo / INCREMENTAL_LOCK_REL).write_text(str(os.getpid()), encoding="utf-8")  # unlocked leftover file

    assert spawn_incremental_run(repo, fake_cli, enabled=True) == "spawned"


def test_post_commit_refreshes_risk_report_sidecar(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the real worker: receipt records the refresh, and incremental stays off by default."""
    _allow_fake_env(monkeypatch)
    monkeypatch.delenv(pc.HEAD_ENV_VAR, raising=False)
    monkeypatch.setattr(pc, "_sweep_trw_dir", lambda _root: repo / ".trw")
    monkeypatch.setattr("trw_mcp.tools._distill_spawn.distill_available", lambda: True)
    monkeypatch.setattr(
        "trw_mcp.tools._maintain_verify.run_maintain_verify_for_project",
        lambda: (_ for _ in ()).throw(RuntimeError("sweep not under test")),
    )

    receipt = pc.run_post_commit(repo, fake_cli)

    assert receipt.risk_report_refreshed is True
    assert receipt.distill_incremental == "disabled"
    assert (repo / ".trw" / "distill" / "map-cache" / f"risk-report-{_head(repo)}.json").is_file()
    assert ["self-improve", "risk-report", "--repo", str(repo), "--persist-sidecar"] in _calls(fake_cli)
    assert not any(call[:1] == ["run"] for call in _calls(fake_cli))
    written = json.loads((repo / pc.RECEIPT_REL_PATH).read_text(encoding="utf-8"))
    assert written["risk_report_refreshed"] is True


def test_post_commit_without_distill_runs_no_distill_step(
    repo: Path, fake_cli: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(pc.HEAD_ENV_VAR, raising=False)
    monkeypatch.setattr(pc, "_sweep_trw_dir", lambda _root: repo / ".trw")
    monkeypatch.setattr("trw_mcp.tools._distill_spawn.distill_available", lambda: False)
    monkeypatch.setattr("trw_mcp.tools._maintain_verify.run_maintain_verify_for_project", lambda: None)

    receipt = pc.run_post_commit(repo, fake_cli)

    assert receipt.distill_incremental == "distill_unavailable"
    assert receipt.risk_report_refreshed is False
    assert _calls(fake_cli) == []


# -- FR01: post-commit requests the detached sidecar rebuild (8.2 T2 S2b) --------------

_HOOK = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "git_hooks" / "trw-post-commit.sh"


def _entitle(repo: Path) -> None:
    from datetime import datetime, timedelta, timezone

    from trw_mcp.state._entitlements import sign_entitlement_for_dev

    future = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    sig = sign_entitlement_for_dev(tier="pro", issued_to="t@t", expires_at=future)
    (repo / ".trw").mkdir(exist_ok=True)
    (repo / ".trw" / "entitlements.yaml").write_text(
        f"tier: pro\nissued_to: t@t\nexpires_at: '{future}'\nsignature: {sig}\n", encoding="utf-8"
    )


@pytest.mark.parametrize(("refresh_enabled", "expected"), [(True, "spawned"), (False, "disabled_by_config")])
def test_post_commit_requests_the_rebuild_through_the_shared_helper(
    repo: Path, monkeypatch: pytest.MonkeyPatch, refresh_enabled: bool, expected: str
) -> None:
    """Through the real worker: no per-file planning, one detached request, recorded in the receipt."""
    from trw_mcp.tools import _distill_spawn

    spawned: list[list[str]] = []

    def _popen(argv: list[str], **kwargs: object) -> object:
        assert kwargs["start_new_session"] is True
        spawned.append(argv)
        return type("Child", (), {"pid": 7})()

    monkeypatch.setenv("TRW_HINT_SIDECAR_AUTO_REFRESH_ENABLED", "true")
    monkeypatch.setenv("TRW_HINT_SIDECAR_REFRESH_ENABLED", "true" if refresh_enabled else "false")
    monkeypatch.setattr(
        _distill_spawn,
        "DEFAULT_PORTS",
        _distill_spawn.SpawnPorts(popen=_popen, which=lambda name, _path: f"/fake/{name}", clock=lambda: 1.0e9),
    )
    monkeypatch.delenv(pc.HEAD_ENV_VAR, raising=False)
    monkeypatch.setattr(pc, "_sweep_trw_dir", lambda _root: repo / ".trw")
    monkeypatch.setattr("trw_mcp.tools._distill_spawn.distill_available", lambda: False)
    monkeypatch.setattr("trw_mcp.tools._maintain_verify.run_maintain_verify_for_project", lambda: None)
    _entitle(repo)

    receipt = pc.run_post_commit(repo, {"PATH": "/usr/bin"})

    assert receipt.sidecar_rebuild == expected
    assert [argv[argv.index("--trigger") + 1] for argv in spawned] == (["post-commit"] if refresh_enabled else [])
    written = json.loads((repo / pc.RECEIPT_REL_PATH).read_text(encoding="utf-8"))
    assert written["sidecar_rebuild"] == expected
    assert "sidecar_files" not in written


def test_the_child_env_is_an_allowlist() -> None:
    """NFR03: an explicit allowlist, never os.environ passthrough."""
    from trw_mcp.tools._distill_spawn import sanitized_env

    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/dev",
        "AWS_SECRET_ACCESS_KEY": "super-secret",
        "GITHUB_TOKEN": "ghp_secret",
        "TRW_PLATFORM_API_KEY": "secret",
    }

    assert sanitized_env(source) == {"PATH": "/usr/bin", "HOME": "/home/dev"}


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
    assert "Path('$_repo')" not in body
