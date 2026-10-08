"""Behavior regressions for the independent sidecar review."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_sidecar_ancestry import FakeGit, _batch, _commit, _git, _hint, _repo, emitted  # noqa: F401
from tests.test_sidecar_rebuild_request import Ports, _missing, ports  # noqa: F401
from trw_mcp.tools import _sidecar_ancestry as ancestry
from trw_mcp.tools._distill_spawn import REBUILD_STAMP_NAME, rebuild_reason, request_rebuild_if_due
from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL


def test_pre_edit_never_requests_build(tmp_path: Path, ports: Ports, emitted: list[dict[str, Any]]) -> None:
    repo, _ = _repo(tmp_path)
    assert _hint(repo, FakeGit()).distill_status == "sidecar_missing"
    assert ports.popen.calls == []


def test_two_heads_share_interval(tmp_path: Path, ports: Ports) -> None:
    repo, _ = _repo(tmp_path)
    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "spawned"
    _commit(repo, "other.py", "x=2\n")
    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "min_interval"
    assert len(ports.popen.calls) == 1


def test_prune_tolerates_concurrent_unlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vanished = tmp_path / ("ancestry-" + "a" * 40 + ".json")
    vanished.write_text("{}")
    stat = Path.stat

    def race(path: Path, *args: Any, **kwargs: Any) -> os.stat_result:
        if path == vanished:
            raise FileNotFoundError(path)
        return stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", race)
    ancestry._write_cache(tmp_path, "b" * 40, {})
    assert json.loads((tmp_path / ("ancestry-" + "b" * 40 + ".json")).read_text())["entries"] == {}


def test_malformed_batch_is_due() -> None:
    assert rebuild_reason("sidecar_malformed", None, after_commits=150) == "sidecar_malformed"


@pytest.mark.parametrize("dirty_builder", [False, True])
def test_exact_head_filters_changed_content(tmp_path: Path, emitted: list[dict[str, Any]], dirty_builder: bool) -> None:
    repo, head = _repo(tmp_path)
    _batch(repo, head, extra={"dirty_paths": ["foo.py"] if dirty_builder else []})
    result = _hint(repo, FakeGit(dirty=frozenset() if dirty_builder else frozenset({"foo.py"})))
    assert result.distill_hint is not None
    assert result.distill_hint.risk_score is None
    assert result.distill_hint.importers == []
    assert result.distill_hint.co_change_neighbors == ["bar.py", "baz.py"]


@pytest.mark.parametrize("linked_component", ["above_checkout", ".trw", "distill"])
def test_cache_refusal_covers_the_checkout_not_the_path_above_it(tmp_path: Path, linked_component: str) -> None:
    """A symlink above the checkout (macOS /var, a home on another volume) is the user's layout, not an attack."""
    from trw_mcp.tools._sidecar_substrate import cache_is_safe

    real = tmp_path / "real"
    real.mkdir()
    repo, head = _repo(real)
    _batch(repo, head)
    if linked_component == "above_checkout":
        (tmp_path / "alias").symlink_to(real, target_is_directory=True)
        repo = tmp_path / "alias" / "repo"
    else:
        inside = repo / ".trw" if linked_component == ".trw" else repo / ".trw" / "distill"
        moved = tmp_path / "moved"
        inside.rename(moved)
        inside.symlink_to(moved, target_is_directory=True)

    assert cache_is_safe(repo / DEFAULT_CACHE_DIR_REL, repo) is (linked_component == "above_checkout")


@pytest.mark.parametrize("unsafe", ["symlink", "tracked"])
def test_unsafe_cache_neither_reads_nor_writes(
    tmp_path: Path, ports: Ports, emitted: list[dict[str, Any]], unsafe: str
) -> None:
    repo, head = _repo(tmp_path)
    sidecar = _batch(repo, head)
    cache = sidecar.parent
    if unsafe == "symlink":
        actual = tmp_path / "outside"
        cache.rename(actual)
        cache.symlink_to(actual, target_is_directory=True)
    else:
        _git(repo, "add", "-f", str(sidecar))
    before = sorted(str(p.relative_to(cache)) for p in cache.rglob("*"))
    result = _hint(repo, FakeGit())
    assert result.distill_hint is None
    assert result.distill_status == "sidecar_missing"
    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status != "spawned"
    assert sorted(str(p.relative_to(cache)) for p in cache.rglob("*")) == before


def test_exit_128_is_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, head = _repo(tmp_path)
    candidate = "a" * 40
    _batch(repo, candidate)
    reader = ancestry.SubprocessGitReader(repo)
    calls = 0

    def run(
        self: Any, args: tuple[str, ...], ok_codes: frozenset[int] = frozenset({0})
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal calls
        calls += 1
        if calls == 1:
            if 128 not in ok_codes:
                raise ancestry.GitReadError("transient exit 128")
            return subprocess.CompletedProcess(args, 128, b"", b"transient")
        return subprocess.CompletedProcess(args, 0, b"0 1" if args[0] == "rev-list" else b"", b"")

    monkeypatch.setattr(ancestry.SubprocessGitReader, "_run", run)
    cache = repo / DEFAULT_CACHE_DIR_REL
    assert ancestry.find_ancestor_sidecar(cache, head, git=reader, max_commits_behind=500).status == "git_failed"
    assert ancestry.find_ancestor_sidecar(cache, head, git=reader, max_commits_behind=500).status == "found"


def test_request_markers_pruned_by_age(tmp_path: Path, ports: Ports) -> None:
    repo, _ = _repo(tmp_path)
    stale = repo / DEFAULT_CACHE_DIR_REL / "refresh-requests" / ("a" * 40) / REBUILD_STAMP_NAME
    stale.parent.mkdir(parents=True)
    stale.write_text("{}")
    os.utime(stale, (ports.clock.now - 8 * 86400,) * 2)
    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "spawned"
    assert not stale.parent.exists()


def test_lookup_saves_progress_with_total_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, head = _repo(tmp_path)
    for index in range(1, 25):
        _batch(repo, f"{index:040x}")
    clock = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    checked: list[str] = []

    class SlowGit(FakeGit):
        def ancestor_distance(self, sha: str, head: str) -> int | None:
            clock[0] += 0.4
            checked.append(sha)
            return None

    git = SlowGit()
    cache = repo / DEFAULT_CACHE_DIR_REL
    ancestry.find_ancestor_sidecar(cache, head, git=git, max_commits_behind=500)
    assert 0 < len(checked) <= 3
    first = list(checked)
    ancestry.find_ancestor_sidecar(cache, head, git=git, max_commits_behind=500)
    assert len(checked) > len(first)
    assert len(checked) == len(set(checked))


def test_twelve_worktrees_keep_progress(tmp_path: Path) -> None:
    for index in range(24):
        ancestry._write_cache(tmp_path, f"{index:040x}", {})
    assert len(list(tmp_path.glob("ancestry-*.json"))) == 24


def test_exact_single_file_filters_dirty_content(tmp_path: Path, emitted: list[dict[str, Any]]) -> None:
    repo, head = _repo(tmp_path)
    batch = _batch(repo, head, extra={"dirty_paths": ["foo.py"]})
    envelope = json.loads(batch.read_text())
    envelope["payload"] = envelope["payload"]["hints"][0]
    batch.unlink()
    (batch.parent / f"before-edit-hint-{head}.json").write_text(json.dumps(envelope))
    result = _hint(repo, FakeGit())
    assert result.distill_hint is not None
    assert result.distill_hint.risk_score is None


def test_pre_edit_git_calls_have_short_timeouts(
    tmp_path: Path, emitted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, head = _repo(tmp_path)
    _batch(repo, head)
    real_run = subprocess.run
    timeouts: list[float] = []

    def run(*args: Any, **kwargs: Any) -> Any:
        timeouts.append(kwargs.get("timeout", 999))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    result = _hint(repo, FakeGit())
    assert result.distill_hint is not None
    assert timeouts and max(timeouts) <= 1.0


def test_busy_build_records_no_pending_request(tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _distill_spawn
    from trw_mcp.tools._distill_spawn import SpawnPorts

    repo, _ = _repo(tmp_path)
    held: list[int] = []
    original = ports.popen

    def spawn(argv: list[str], **kwargs: Any) -> Any:
        held.extend(os.dup(fd) for fd in kwargs["pass_fds"])
        return original(argv, **kwargs)

    monkeypatch.setattr(_distill_spawn, "DEFAULT_PORTS", SpawnPorts(popen=spawn, which=ports.which, clock=ports.clock))
    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "spawned"
    head = _commit(repo, "later.py", "x=1\n")
    try:
        assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "already_running"
        assert not (repo / DEFAULT_CACHE_DIR_REL / "refresh-requests" / head).exists()
    finally:
        for fd in held:
            os.close(fd)
    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "min_interval"
    assert len(ports.popen.calls) == 1


def test_legacy_negative_cache_is_rechecked(tmp_path: Path) -> None:
    repo, head = _repo(tmp_path)
    candidate = "a" * 40
    cache = _batch(repo, candidate).parent
    (cache / f"ancestry-{head}.json").write_text(
        json.dumps(
            {
                "schema_version": "trw-sidecar-ancestry/v1",
                "head": head,
                "entries": {candidate: {"is_ancestor": False}},
            }
        )
    )
    outcome = ancestry.find_ancestor_sidecar(cache, head, git=FakeGit(behind={candidate: 1}), max_commits_behind=500)
    assert outcome.status == "found"


def test_session_admission_has_no_git_or_lookup(tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools import _distill_spawn as spawn

    repo, _ = _repo(tmp_path)
    monkeypatch.setattr(spawn, "distill_available", lambda: True)  # the dev venv has it; a bare CI does not

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("session admission must not run git or look up hints")

    monkeypatch.setattr(subprocess, "run", forbidden)
    spawn.request_session_refresh(repo, interval_s=900)
    assert len(ports.popen.calls) == 1
    argv, kwargs = ports.popen.calls[0]
    assert "_request_sidecar_rebuild" in argv[-1]
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert spawn._write_stamp(repo / DEFAULT_CACHE_DIR_REL, ports.clock.now, "post-commit")
    spawn.request_session_refresh(repo, interval_s=900)
    assert len(ports.popen.calls) == 1


def test_session_finalizer_requests_refresh_without_response_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools import _ceremony_helpers, _distill_spawn, ceremony
    from trw_mcp.tools._ceremony_session_start_steps import finalize_session_start

    repo, _ = _repo(tmp_path)
    requests: list[tuple[Path, float]] = []
    monkeypatch.setattr(ceremony, "resolve_trw_dir", lambda: repo / ".trw")
    monkeypatch.setattr(_ceremony_helpers, "step_mark_session_started", lambda **kw: None)
    monkeypatch.setattr(_ceremony_helpers, "step_ceremony_status", lambda result: None)
    monkeypatch.setattr(
        _distill_spawn, "request_session_refresh", lambda root, *, interval_s: requests.append((root, interval_s))
    )
    result: Any = {}
    timings: dict[str, float] = {}
    finalize_session_start(result, TRWConfig(hint_sidecar_refresh_enabled=True), timings, [])
    assert requests == [(repo, 900)]
    assert result["success"] is True
    assert "sidecar_refresh" not in result
    assert timings["sidecar_refresh"] >= 0
    finalize_session_start({}, TRWConfig(hint_sidecar_refresh_enabled=False), {}, [])
    assert len(requests) == 1


def test_delivery_receipts_prune_only_old_seen_markers(tmp_path: Path) -> None:
    import time

    from trw_mcp.channels.claude_code._hook_helpers import mark_sidecar_remedy_delivered, sidecar_remedy_marker

    repo, _ = _repo(tmp_path)
    old = sidecar_remedy_marker(repo, "old")
    old.parent.mkdir(parents=True)
    old.write_text("delivered")
    os.utime(old, (time.time() - 8 * 86400,) * 2)
    recent = sidecar_remedy_marker(repo, "recent")
    recent.write_text("delivered")
    other = old.parent / "unrelated.seen"
    other.write_text("keep")
    os.utime(other, (time.time() - 8 * 86400,) * 2)
    mark_sidecar_remedy_delivered(repo, "new")
    assert not old.exists()
    assert recent.exists() and other.read_text() == "keep"
    assert sidecar_remedy_marker(repo, "new").read_text() == "delivered\n"


def test_a_git_probe_timeout_is_not_reported_as_a_missing_sidecar(
    tmp_path: Path, emitted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy cache whose tracked-files probe timed out: its own status, and no rebuild advice."""
    from trw_mcp.channels.claude_code._hook_helpers import sidecar_remedy_once
    from trw_mcp.tools import _sidecar_paths

    repo, head = _repo(tmp_path)
    _batch(repo, head)
    real_run = subprocess.run
    timeouts: list[float] = []

    def run(argv: list[str], **kwargs: Any) -> Any:
        timeouts.append(kwargs["timeout"])
        if "ls-files" in argv:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, **kwargs)

    monkeypatch.setattr(_sidecar_paths.subprocess, "run", run)
    result = _hint(repo, FakeGit())
    assert result.distill_status == "sidecar_check_timed_out"
    assert result.distill_hint is None
    assert sidecar_remedy_once(repo, "session", result.distill_status, result.distill_action) == ""
    # Each probe is given what is left of one shared second, not a fixed quarter second.
    assert timeouts and all(0.25 < timeout <= 1.0 for timeout in timeouts)


def test_deadline_expiry_serves_the_best_ancestor_already_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, head = _repo(tmp_path)
    shas = [f"{index:040x}" for index in range(1, 9)]
    for index, sha in enumerate(shas):
        _batch(repo, sha, mtime=1000.0 - index)
    clock = [0.0]
    monkeypatch.setattr("time.monotonic", lambda: clock[0])

    class SlowGit(FakeGit):
        def ancestor_distance(self, sha: str, head: str) -> int | None:
            clock[0] += 0.45
            return 3 if len(self.calls_made) == 0 and not self.calls_made.append(sha) else None

    git = SlowGit()
    git.calls_made = []  # type: ignore[attr-defined]
    outcome = ancestry.find_ancestor_sidecar(repo / DEFAULT_CACHE_DIR_REL, head, git=git, max_commits_behind=500)
    assert len(git.calls_made) < len(shas), "non-vacuity: the budget ran out before every candidate was asked"  # type: ignore[attr-defined]
    assert outcome.status == "found"
    assert outcome.ancestor is not None and outcome.ancestor.commits_behind == 3


def test_session_start_forks_nothing_when_distill_is_unavailable_or_unentitled(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _distill_spawn as spawn

    repo, _ = _repo(tmp_path)
    monkeypatch.setattr(spawn, "distill_available", lambda: False)
    spawn.request_session_refresh(repo, interval_s=900)
    assert ports.popen.calls == []
    monkeypatch.setattr(spawn, "distill_available", lambda: True)
    (repo / ".trw/entitlements.yaml").unlink()
    spawn.request_session_refresh(repo, interval_s=900)
    assert ports.popen.calls == []
    assert not (repo / DEFAULT_CACHE_DIR_REL).exists(), "an install that cannot use the cache does not get one"


def test_session_start_stamps_its_check_so_the_next_session_forks_nothing(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker may find nothing due (a fresh sidecar) and stamp nothing: the admission's own stamp throttles."""
    from trw_mcp.tools import _distill_spawn as spawn

    repo, _ = _repo(tmp_path)
    monkeypatch.setattr(spawn, "distill_available", lambda: True)
    spawn.request_session_refresh(repo, interval_s=900)
    spawn.request_session_refresh(repo, interval_s=900)
    assert len(ports.popen.calls) == 1
    ports.clock.now += 901
    spawn.request_session_refresh(repo, interval_s=900)
    assert len(ports.popen.calls) == 2


def test_the_session_worker_program_really_requests_a_build(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the worker's own argv once: a rename of the function it imports must fail here, not silently in production."""
    import sys
    import time

    from tests.channels.claude_code._distill_hint_support import CHECKOUT_PYTHONPATH
    from trw_mcp.tools import _distill_spawn as spawn

    repo, head = _repo(tmp_path)
    monkeypatch.setattr(spawn, "distill_available", lambda: True)
    spawn.request_session_refresh(repo, interval_s=900)
    argv, kwargs = ports.popen.calls[0]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    receipt = tmp_path / "build-receipt.json"
    cli = bin_dir / "trw-distill"
    cli.write_text(
        f"#!{sys.executable}\nimport json, sys\nopen({str(receipt)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
    )
    cli.chmod(0o755)
    env = {
        **kwargs["env"],
        "TRW_HINT_SIDECAR_REFRESH_ENABLED": "true",
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin",
        "PYTHONPATH": CHECKOUT_PYTHONPATH,
    }
    env.pop("TRW_SURFACE_ROLE", None)
    done = subprocess.run(argv, cwd=kwargs["cwd"], env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    deadline = time.monotonic() + 10
    while not receipt.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    built = json.loads(receipt.read_text())
    assert built[built.index("--repo") + 1] == str(repo)
    # trw-distill's --trigger choices have no session-start yet; the CLI is told post-commit, the stamp the truth.
    assert built[built.index("--trigger") + 1] == "post-commit"
    stamp = json.loads((repo / DEFAULT_CACHE_DIR_REL / REBUILD_STAMP_NAME).read_text())
    assert stamp["trigger"] == "session-start"


def test_the_deleted_auto_refresh_key_is_reported_as_retired_with_its_replacement(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models.config import _retired_keys

    monkeypatch.delenv("TRW_QUIET", raising=False)
    monkeypatch.setattr(_retired_keys, "_stderr_warnings_off", lambda: False)
    _retired_keys._reset_warned_keys()
    try:
        warned = _retired_keys.warn_retired_env_vars({"TRW_HINT_SIDECAR_AUTO_REFRESH_ENABLED": "true"})
    finally:
        _retired_keys._reset_warned_keys()
    assert warned == ["TRW_HINT_SIDECAR_AUTO_REFRESH_ENABLED"]
    assert "it was retired; use hint_sidecar_refresh_enabled" in capsys.readouterr().err


def test_session_start_forks_nothing_when_its_stamp_cannot_be_written(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the stamp nothing throttles the next session start, so an unwritable cache must not fork at all."""
    from trw_mcp.tools import _distill_spawn as spawn

    repo, _ = _repo(tmp_path)
    monkeypatch.setattr(spawn, "distill_available", lambda: True)
    monkeypatch.setattr(spawn, "_write_stamp", lambda *args, **kwargs: False)
    spawn.request_session_refresh(repo, interval_s=900)
    assert ports.popen.calls == []


def test_an_expired_lookup_deadline_runs_no_git_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from trw_mcp.tools import _sidecar_paths

    repo, head = _repo(tmp_path)
    cache = _batch(repo, head).parent

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a probe ran after the lookup's budget was spent")

    monkeypatch.setattr(_sidecar_paths.subprocess, "run", forbidden)
    spent = time.monotonic() - 1
    assert _sidecar_paths.cache_safety(cache, repo, spent) == "unknown"
    assert _sidecar_paths.resolve_git_sha(repo, spent) is None
    assert _sidecar_paths.resolve_repo_root(None, spent) is None


def test_root_discovery_draws_on_the_lookups_one_budget(
    tmp_path: Path, emitted: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery used to get its own second before the shared deadline even started."""
    from trw_mcp.tools import _sidecar_paths, _sidecar_substrate

    repo, head = _repo(tmp_path)
    _batch(repo, head)
    seen: list[float | None] = []
    real = _sidecar_paths.resolve_repo_root

    def spy(repo_root: str | None, deadline: float | None = None) -> Path | None:
        seen.append(deadline)
        return real(repo_root, deadline)

    monkeypatch.setattr(_sidecar_substrate, "resolve_repo_root", spy)
    assert _hint(repo, FakeGit()).distill_hint is not None
    assert seen and all(deadline is not None for deadline in seen)
