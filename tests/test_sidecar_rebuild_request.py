"""The detached sidecar rebuild request (``hint_sidecar_refresh_enabled``, 8.2 T2 S2b).

Driven through the production call site, ``compute_before_edit_hint``, with
fakes at the three ports (process, PATH, clock) via ``_distill_spawn.DEFAULT_PORTS``
and the ancestry test's fake ``GitReader``. No test here can start a build:
``Popen`` is fake everywhere except the one ``spawn_detached`` test, which
starts a plain ``sleep`` interpreter and kills its process group.
"""

from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from tests.test_sidecar_ancestry import _NEAR, FakeGit, _batch, _commit, _git, _repo, emitted  # noqa: F401  (fixture)
from tests.test_sidecar_ancestry import _hint as _read_hint
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools import _distill_spawn
from trw_mcp.tools._distill_spawn import (
    AUTO_REFRESH_FLAG,
    REBUILD_STAMP_NAME,
    SpawnPorts,
    rebuild_reason,
    request_rebuild_if_due,
    spawn_detached,
)
from trw_mcp.tools._sidecar_ancestry import shared_cache_dir
from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL, CurrentSidecarResult

_FLAG_ENV = "TRW_HINT_SIDECAR_REFRESH_ENABLED"
_CLI = "/fake/bin/trw-distill"
_NICE = "/usr/bin/nice"
_T0 = 1_800_000_000.0


@dataclass
class _Child:
    pid: int


@dataclass
class FakePopen:
    """Records every spawn; ``fail`` makes the spawn itself raise."""

    calls: list[tuple[list[str], dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    def __call__(self, argv: list[str], **kwargs: Any) -> _Child:
        self.calls.append((argv, kwargs))
        if self.fail:
            raise OSError("fork failed: simulated")
        return _Child(pid=4242)


@dataclass
class FakeWhich:
    """``shutil.which`` answering from a table; records the search path of each lookup."""

    found: dict[str, str] = field(default_factory=lambda: {"trw-distill": _CLI, "nice": _NICE})
    paths: dict[str, str | None] = field(default_factory=dict)

    def __call__(self, command: str, path: str | None) -> str | None:
        self.paths[command] = path
        return self.found.get(command)


@dataclass
class Clock:
    now: float = _T0

    def __call__(self) -> float:
        return self.now


@dataclass
class Ports:
    popen: FakePopen = field(default_factory=FakePopen)
    which: FakeWhich = field(default_factory=FakeWhich)
    clock: Clock = field(default_factory=Clock)

    def spawn_ports(self) -> SpawnPorts:
        return SpawnPorts(popen=self.popen, which=self.which, clock=self.clock)


@pytest.fixture
def ports(monkeypatch: pytest.MonkeyPatch) -> Ports:
    """Flag on (conftest pins it off), and fakes installed at the hint's own call site."""
    monkeypatch.setenv(_FLAG_ENV, "true")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    fakes = Ports()
    monkeypatch.setattr(_distill_spawn, "DEFAULT_PORTS", fakes.spawn_ports())
    return fakes


def _stamp(repo: Path) -> Path:
    return repo / DEFAULT_CACHE_DIR_REL / REBUILD_STAMP_NAME


def _hint(repo: Path, git: FakeGit) -> Any:
    """Read the hint, then explicitly exercise the post-commit request path."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._entitlements import DISTILL_SIDECAR_FEATURE
    from trw_mcp.tools._sidecar_substrate import resolve_current_sidecar

    result = _read_hint(repo, git)
    config = get_config()
    lookup = resolve_current_sidecar(
        repo_root=str(repo),
        cache_dir=None,
        feature=DISTILL_SIDECAR_FEATURE,
        artifact_name="before-edit-batch",
        cli_remediation=None,
        ancestor_bound=config.hint_sidecar_max_commits_behind,
        git_reader=git,
    )
    request_rebuild_if_due(lookup, cache_dir=None, trigger="post-commit")
    return result


# -- The trigger, through compute_before_edit_hint ------------------------------------


@pytest.mark.parametrize(
    ("setup", "expect_spawn"),
    [
        ("missing", True),
        ("too_far", True),
        ("behind_threshold", True),
        ("below_threshold", False),
        ("fresh", False),
    ],
)
def test_the_trigger_decides_from_the_lookup(
    tmp_path: Path, emitted: list[dict[str, Any]], ports: Ports, setup: str, expect_spawn: bool
) -> None:
    repo, head = _repo(tmp_path)
    git = FakeGit()
    if setup == "too_far":
        _batch(repo, _NEAR)
        git.behind[_NEAR] = 600  # past hint_sidecar_max_commits_behind=500
    elif setup in ("behind_threshold", "below_threshold"):
        _batch(repo, _NEAR)
        git.behind[_NEAR] = 150 if setup == "behind_threshold" else 149
    elif setup == "fresh":
        _batch(repo, head)

    result = _hint(repo, git)

    expected_status = {
        "missing": "sidecar_missing",
        "too_far": "sidecar_too_far_behind",
        "behind_threshold": "hint_available_stale",
        "below_threshold": "hint_available_stale",
        "fresh": "hint_available",
    }[setup]
    assert result.distill_status == expected_status
    assert len(ports.popen.calls) == (1 if expect_spawn else 0)
    assert _stamp(repo).is_file() is expect_spawn
    # 2026-09-27 audit (touchpoint #5): "missing"/"too_far" are the two
    # _NO_USABLE_SIDECAR statuses a spawned rebuild replaces the manual-command
    # action for. The others either already carry a hint (no action to swap)
    # or never spawned (action stays the manual command).
    if setup in ("missing", "too_far"):
        assert result.distill_action is not None
        assert "rebuild was requested" not in result.distill_action
    elif setup in ("behind_threshold", "below_threshold"):
        assert result.distill_action is None  # a hint was served; nothing to remediate


def test_the_spawn_is_detached_niced_and_names_the_shared_cache(
    tmp_path: Path, emitted: list[dict[str, Any]], ports: Ports
) -> None:
    repo, _head = _repo(tmp_path)

    _hint(repo, FakeGit())

    [(argv, kwargs)] = ports.popen.calls
    cache = repo / ".trw" / "distill" / "map-cache"
    assert argv == [
        _NICE, "-n", "10", _CLI, "self-improve", "refresh-sidecars",
        "--repo", str(repo), "--cache-dir", str(cache), "--trigger", "post-commit",
    ]  # fmt: skip
    assert kwargs["start_new_session"] is True
    assert (kwargs["stdin"], kwargs["stdout"], kwargs["stderr"]) == (subprocess.DEVNULL,) * 3
    assert kwargs["cwd"] == repo


def _cache_dir_arg(ports: Ports) -> Path:
    [(argv, _kwargs)] = ports.popen.calls
    return Path(argv[argv.index("--cache-dir") + 1])


def test_a_linked_worktree_builds_into_the_cache_the_hint_reads(
    tmp_path: Path, emitted: list[dict[str, Any]], ports: Ports
) -> None:
    """Through the hint, from a real ``git worktree``: --cache-dir is S1's shared resolver output, the main checkout's."""
    repo, _head = _repo(tmp_path)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    (worktree / ".trw").mkdir()
    (worktree / ".trw" / "entitlements.yaml").write_text((repo / ".trw" / "entitlements.yaml").read_text())

    result = _hint(worktree, FakeGit())

    assert result.distill_status == "sidecar_missing"
    built_into = _cache_dir_arg(ports)
    assert built_into == shared_cache_dir(worktree, DEFAULT_CACHE_DIR_REL)
    assert built_into.resolve() == (repo / DEFAULT_CACHE_DIR_REL).resolve()
    assert built_into.resolve() != (worktree / DEFAULT_CACHE_DIR_REL).resolve()


def test_a_hand_built_worktree_layout_resolves_through_commondir(tmp_path: Path, ports: Ports) -> None:
    """A ``.git`` FILE plus ``commondir``, no git involved: the spawn names the main checkout's cache."""
    main, _head = _repo(tmp_path)
    admin = main / ".git" / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..\n")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {admin}\n")
    (linked / ".trw").mkdir()
    (linked / ".trw" / "entitlements.yaml").write_text((main / ".trw" / "entitlements.yaml").read_text())

    outcome = request_rebuild_if_due(_missing(linked), cache_dir=None, trigger="post-commit")

    assert outcome.status == "spawned"
    assert _cache_dir_arg(ports) == shared_cache_dir(linked, DEFAULT_CACHE_DIR_REL)
    assert _cache_dir_arg(ports).resolve() == (main / DEFAULT_CACHE_DIR_REL).resolve()
    stamp = json.loads((main / DEFAULT_CACHE_DIR_REL / REBUILD_STAMP_NAME).read_text())
    assert stamp["trigger"] == "post-commit"


def test_the_child_env_carries_the_surface_role_and_nothing_unlisted(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _head = _repo(tmp_path)
    source = {"PATH": "/usr/bin", "TRW_SURFACE_ROLE": "agent", "AWS_SECRET_ACCESS_KEY": "s3cr3t"}

    outcome = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit", source_env=source)

    assert outcome.status == "spawned"
    env = ports.popen.calls[0][1]["env"]
    assert env["TRW_SURFACE_ROLE"] == "agent"
    assert env["PATH"] == "/usr/bin"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_the_cli_is_also_found_in_the_interpreters_own_bin(tmp_path: Path, ports: Ports) -> None:
    repo, _head = _repo(tmp_path)

    request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit", source_env={"PATH": "/usr/bin"})

    searched = ports.which.paths["trw-distill"]
    assert searched is not None
    assert searched.split(os.pathsep) == ["/usr/bin", str(Path(sys.executable).parent)]


def test_the_hint_is_identical_whether_or_not_a_spawn_happened(
    tmp_path: Path, emitted: list[dict[str, Any]], ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)
    git = FakeGit(behind={_NEAR: 200})

    spawned = _hint(repo, git)
    assert len(ports.popen.calls) == 1
    ports.popen.fail = True
    ports.clock.now += 3600
    _commit(repo, "later.py", "x=1\n")
    failed = _hint(repo, git)
    monkeypatch.setenv(_FLAG_ENV, "false")
    from trw_mcp.models.config import reload_config

    reload_config()
    off = _hint(repo, git)

    assert len(ports.popen.calls) == 2  # the flag-off run never reached the port
    assert spawned.model_dump() == failed.model_dump() == off.model_dump()
    assert spawned.distill_status == "hint_available_stale"


# -- Every refusal spawns nothing ----------------------------------------------------


def _missing(repo: Path) -> CurrentSidecarResult:
    return CurrentSidecarResult(tier="pro", payload=None, status="sidecar_missing", repo_root=repo)


@pytest.mark.parametrize(
    "refusal",
    ["disabled", "reviewer_role", "no_entitlement", "min_interval", "cli_unavailable", "nice_unavailable"],
)
def test_each_refusal_spawns_nothing(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    repo, _head = _repo(tmp_path)
    if refusal == "disabled":
        monkeypatch.setenv(_FLAG_ENV, "false")
    elif refusal == "reviewer_role":
        monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    elif refusal == "no_entitlement":
        (repo / ".trw" / "entitlements.yaml").unlink()
    elif refusal == "min_interval":
        _stamp(repo).parent.mkdir(parents=True)
        _stamp(repo).write_text(json.dumps({"requested_at_unix": _T0 - 14 * 60}))
    elif refusal == "cli_unavailable":
        del ports.which.found["trw-distill"]
    elif refusal == "nice_unavailable":
        del ports.which.found["nice"]

    with capture_logs() as logs:
        outcome = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")

    assert outcome.status == refusal
    assert ports.popen.calls == []
    events = [log for log in logs if log["event"] == "sidecar_rebuild_request"]
    assert [event["outcome"] for event in events] == [refusal]
    assert events[0]["disable"] == AUTO_REFRESH_FLAG
    assert "hint_sidecar_refresh_enabled" in AUTO_REFRESH_FLAG
    if refusal != "min_interval":
        assert not _stamp(repo).exists()


def test_the_interval_elapses_and_the_stamp_is_rewritten_atomically(tmp_path: Path, ports: Ports) -> None:
    repo, _head = _repo(tmp_path)

    first = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")
    ports.clock.now += 14 * 60
    inside = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")
    ports.clock.now += 60
    _commit(repo, "later.py", "x=1\n")
    after = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")

    assert (first.status, inside.status, after.status) == ("spawned", "min_interval", "spawned")
    stamp = json.loads(_stamp(repo).read_text(encoding="utf-8"))
    assert (stamp["requested_at_unix"], stamp["trigger"]) == (_T0 + 15 * 60, "post-commit")
    assert [p.name for p in _stamp(repo).parent.iterdir() if p.suffix == ".tmp"] == []


@pytest.mark.parametrize("body", ["{not json", '{"requested_at_unix": "soon"}', "[]"])
def test_a_corrupt_stamp_counts_as_elapsed(tmp_path: Path, ports: Ports, body: str) -> None:
    repo, _head = _repo(tmp_path)
    _stamp(repo).parent.mkdir(parents=True)
    _stamp(repo).write_text(body)

    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "spawned"


def test_an_oversized_stamp_is_read_bounded_and_counts_as_elapsed(tmp_path: Path, ports: Ports) -> None:
    """Review KI: the stamp read is bounded, so a huge file never stalls the hint (it is treated as unreadable)."""
    repo, _head = _repo(tmp_path)
    _stamp(repo).parent.mkdir(parents=True)
    _stamp(repo).write_text(json.dumps({"requested_at_unix": _T0, "pad": "x" * 100_000}))

    assert request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit").status == "spawned"


def test_a_failed_spawn_still_waits_out_the_interval(tmp_path: Path, ports: Ports) -> None:
    """The stamp is written before the spawn, so a host that cannot fork does not retry on every edit."""
    repo, _head = _repo(tmp_path)
    ports.popen.fail = True

    with capture_logs() as logs:
        failed = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")
    again = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")

    assert (failed.status, again.status) == ("spawn_failed", "min_interval")
    assert any(log.get("outcome") == "spawn_failed" and log["disable"] == AUTO_REFRESH_FLAG for log in logs)


def test_an_unwritable_cache_dir_spawns_nothing(tmp_path: Path, ports: Ports) -> None:
    repo, _head = _repo(tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")

    outcome = request_rebuild_if_due(_missing(repo), cache_dir=blocker / "cache", trigger="post-commit")

    assert outcome.status == "unsafe_cache"
    assert ports.popen.calls == []


@pytest.mark.parametrize(
    ("status", "behind", "expected"),
    [
        ("sidecar_missing", None, "sidecar_missing"),
        ("sidecar_too_far_behind", None, "sidecar_too_far_behind"),
        ("hint_available_stale", 150, "commits_behind>=150"),
        ("hint_available_stale", 149, None),
        ("hint_available", None, None),
        ("tier_required", None, None),
        ("sidecar_malformed", None, "sidecar_malformed"),
        ("sidecar_diff_failed", None, None),
    ],
)
def test_rebuild_reason(status: str, behind: int | None, expected: str | None) -> None:
    assert rebuild_reason(status, behind, after_commits=150) == expected


# -- The spawn primitive, config and the IP boundary -----------------------------------


def test_spawn_detached_returns_without_waiting(tmp_path: Path) -> None:
    """A real child that sleeps 30s is still running when the call returns, and leads its own session."""
    pid = spawn_detached([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path, env={"PATH": os.defpath})
    try:
        assert os.waitpid(pid, os.WNOHANG) == (0, 0)  # not reaped: the call did not wait for it
        assert os.getsid(pid) == pid
    finally:
        os.killpg(pid, signal.SIGKILL)
        os.waitpid(pid, 0)


def test_the_rebuild_knobs_are_typed_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_FLAG_ENV, raising=False)
    config = TRWConfig()
    assert config.hint_sidecar_refresh_enabled is True
    assert (config.hint_sidecar_rebuild_after_commits, config.hint_sidecar_rebuild_min_interval_minutes) == (150, 15)
    for bad in (
        {"hint_sidecar_rebuild_after_commits": 0},
        {"hint_sidecar_rebuild_after_commits": 10_001},
        {"hint_sidecar_rebuild_min_interval_minutes": 0},
        {"hint_sidecar_rebuild_min_interval_minutes": 1441},
    ):
        with pytest.raises(ValidationError):
            TRWConfig(**bad)


def test_the_threshold_is_capped_at_the_ancestor_bound(
    tmp_path: Path, emitted: list[dict[str, Any]], ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lowering only hint_sidecar_max_commits_behind stays a valid config, and the served edge still rebuilds."""
    monkeypatch.setenv("TRW_HINT_SIDECAR_MAX_COMMITS_BEHIND", "40")
    repo, _head = _repo(tmp_path)
    _batch(repo, _NEAR)

    result = _hint(repo, FakeGit(behind={_NEAR: 40}))

    assert result.distill_status == "hint_available_stale"
    assert len(ports.popen.calls) == 1


def test_the_spawn_module_never_imports_trw_distill() -> None:
    source = Path(_distill_spawn.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported, "the scan found no imports at all"
    assert not any(name.split(".")[0] == "trw_distill" for name in imported)


# -- UNINSTALL-DISTILL-RACE: the detached build holds a lock uninstall can wait on -----------


def test_the_spawned_build_holds_the_rebuild_lock_for_its_lifetime(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    fcntl = pytest.importorskip("fcntl")

    from trw_mcp.tools._distill_spawn import REBUILD_LOCK_REL

    repo, _head = _repo(tmp_path)
    held: list[int] = []

    def child_keeps_the_lock(argv: list[str], **kwargs: Any) -> _Child:
        # A real child inherits the descriptor (same open file description, so the flock lives as long as it does):
        # a dup stands in for that inheritance, because the parent closes its own copy right after the spawn.
        held.extend(os.dup(fd) for fd in kwargs["pass_fds"])
        return _Child(pid=4242)

    monkeypatch.setattr(
        _distill_spawn, "DEFAULT_PORTS", SpawnPorts(popen=child_keeps_the_lock, which=ports.which, clock=ports.clock)
    )

    outcome = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")

    assert outcome.status == "spawned" and len(held) == 1
    probe = os.open(repo / REBUILD_LOCK_REL, os.O_RDWR)
    try:
        with pytest.raises(OSError):  # another locker (an uninstall) is refused while the child lives
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(probe)
        for fd in held:
            os.close(fd)


def test_a_second_request_while_a_build_is_alive_is_refused_not_stacked(tmp_path: Path, ports: Ports) -> None:
    fcntl = pytest.importorskip("fcntl")

    from trw_mcp.tools._distill_spawn import REBUILD_LOCK_REL

    repo, _head = _repo(tmp_path)
    lock = repo / REBUILD_LOCK_REL
    lock.parent.mkdir(parents=True, exist_ok=True)
    live_build = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(live_build, fcntl.LOCK_EX)
    try:
        outcome = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")
    finally:
        os.close(live_build)

    assert outcome.status == "already_running"
    assert ports.popen.calls == [], "a second detached build was spawned on top of the live one"


def test_trw_removed_between_the_entry_check_and_the_lock_stops_with_no_trw_dir(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1's repro: uninstall lands after _request's .trw check. No lock, no stamp, no spawn, nothing recreated."""
    import shutil

    repo, _head = _repo(tmp_path)

    class _ClockThatUninstalls(Clock):
        def __call__(self) -> float:
            shutil.rmtree(repo / ".trw")  # the first thing _request does after its checks is read the clock
            return self.now

    monkeypatch.setattr(
        _distill_spawn,
        "DEFAULT_PORTS",
        SpawnPorts(popen=ports.popen, which=ports.which, clock=_ClockThatUninstalls()),
    )

    outcome = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")

    assert outcome.status == "no_trw_dir"
    assert ports.popen.calls == []
    assert not (repo / ".trw").exists(), "the stamp or lock recreated .trw after uninstall"


# -- UNINSTALL-QUIESCE-KIS (1): the rebuild lock lives beside the cache the build writes ----------------------------------


def _linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    repo, _head = _repo(tmp_path)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    (worktree / ".trw").mkdir()
    (worktree / ".trw" / "entitlements.yaml").write_text((repo / ".trw" / "entitlements.yaml").read_text())
    return repo, worktree


def _keep_child_lock(held: list[int]) -> Any:
    def child(argv: list[str], **kwargs: Any) -> _Child:
        held.extend(
            os.dup(fd) for fd in kwargs["pass_fds"]
        )  # a real child inherits the descriptor; a dup stands in for it
        return _Child(pid=4242)

    return child


def test_a_linked_worktree_build_takes_the_lock_beside_the_shared_cache_it_writes(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Uninstalling the main checkout waits on <main>/.trw/distill/.sidecar-rebuild.lock; the build from a linked worktree writes the main checkout's cache,
    so that is the file it must lock (it used to lock the worktree's own, which uninstall never looks at)."""
    fcntl = pytest.importorskip("fcntl")

    from trw_mcp.tools._distill_spawn import REBUILD_LOCK_REL

    repo, worktree = _linked_worktree(tmp_path)
    held: list[int] = []
    monkeypatch.setattr(
        _distill_spawn, "DEFAULT_PORTS", SpawnPorts(popen=_keep_child_lock(held), which=ports.which, clock=ports.clock)
    )

    outcome = request_rebuild_if_due(_missing(worktree), cache_dir=None, trigger="post-commit")

    assert outcome.status == "spawned" and len(held) == 1
    assert not (worktree / REBUILD_LOCK_REL).exists(), "the worktree's own lock is not the one that protects the cache"
    probe = os.open(repo / REBUILD_LOCK_REL, os.O_RDWR)
    try:
        with pytest.raises(OSError):  # the main checkout's uninstall is refused (it waits) while the build lives
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(probe)
        for fd in held:
            os.close(fd)


def test_one_build_at_a_time_across_every_worktree_that_shares_the_cache(
    tmp_path: Path, ports: Ports, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("fcntl")  # the lock under test is flock-based
    repo, worktree = _linked_worktree(tmp_path)
    held: list[int] = []
    monkeypatch.setattr(
        _distill_spawn, "DEFAULT_PORTS", SpawnPorts(popen=_keep_child_lock(held), which=ports.which, clock=ports.clock)
    )
    assert request_rebuild_if_due(_missing(worktree), cache_dir=None, trigger="post-commit").status == "spawned"
    try:
        (_stamp(repo)).unlink(missing_ok=True)  # the rate limit is not what is under test
        again = request_rebuild_if_due(_missing(repo), cache_dir=None, trigger="post-commit")
        assert again.status == "already_running", "two builds were stacked on one shared cache"
    finally:
        for fd in held:
            os.close(fd)
