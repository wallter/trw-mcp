"""PRD-CORE-305-FR05 sol round-2 — CENSUS: every allowlisted verb, actually run, writes nothing and dials nothing.

Round-1 built the read-only allowlist from ``--help`` text and docstrings.
Round-2 found two of those claims false by reading the actual code path:
``hook-flags`` ("publishes resolved switches") creates ``.trw/runtime/`` and
writes a file every call; ``doctor`` ("read-only diagnostics") checkpoints
the memory store's SQLite WAL and makes a live network request; ``memory
migrate`` without ``--apply`` ("preview, writing nothing") still creates a
temp directory and copies the store's SQLite file. All three were removed
from the allowlist and are covered as NEGATIVE cases below (refused, not
run) — but "read the code and trust it" is exactly how the first two claims
survived a whole review round unchallenged.

This file is the replacement evidence: it actually RUNS every currently
allowlisted verb (each always-safe entry, and each conditional entry in its
allowed mode) under ``TRW_SURFACE_ROLE=reviewer``, in-process through the
real ``trw-mcp`` CLI entry point (``server._cli.main``, not a hand-called
handler), inside a fixture that:

1. Snapshots every file and directory under an isolated project root AND an
   isolated ``HOME``/``TRW_USER_DIR`` before and after the call, and fails on
   any created, modified, or deleted path (ignoring nothing except the
   snapshot roots' own existence).
2. Patches ``socket.getaddrinfo``, ``socket.create_connection``, and
   ``socket.socket.connect`` to record the call and raise, so any attempt to
   resolve a host or open a connection — DNS or TCP, loopback or remote —
   fails the test with the call site in the traceback.

Everything runs IN-PROCESS (never a subprocess) so both patches actually
apply; ``server._cli.main`` is called directly with ``sys.argv`` patched,
matching the real console-script entry point rather than a handler function
pulled out of context. No case here needs a subprocess.

A command that writes or connects is a finding to act on — remove it from
the allowlist — never a reason to special-case this test.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.server._cli_reviewer_policy import classified_command_paths

pytestmark = pytest.mark.integration

_GOLDEN_PRDS = Path(__file__).resolve().parent / "fixtures" / "golden_prds"


# ---------------------------------------------------------------------------
# Filesystem snapshot: every file AND directory under the isolated roots
# ---------------------------------------------------------------------------


def _snapshot(roots: list[Path]) -> dict[str, object]:
    """(size, mtime_ns) per file and a marker per directory, keyed by an
    (absolute root, relative path) pair so two isolated roots never collide."""
    state: dict[str, object] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            key = f"{root}::{path.relative_to(root)}"
            if path.is_dir():
                state[key] = "dir"
            elif path.is_file():
                st = path.stat()
                state[key] = (st.st_size, st.st_mtime_ns)
    return state


def _diff(before: dict[str, object], after: dict[str, object]) -> str:
    created = sorted(set(after) - set(before))
    deleted = sorted(set(before) - set(after))
    modified = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    parts = []
    if created:
        parts.append(f"created: {created}")
    if deleted:
        parts.append(f"deleted: {deleted}")
    if modified:
        parts.append(f"modified: {modified}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Network trap
# ---------------------------------------------------------------------------


class NetworkAttempted(AssertionError):
    """Raised the instant a patched socket primitive is called -- never swallowed."""


@dataclass
class _NetworkTrap:
    calls: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)


def _install_network_trap(monkeypatch: pytest.MonkeyPatch) -> _NetworkTrap:
    trap = _NetworkTrap()

    def _record_and_raise(name: str) -> Callable[..., object]:
        def _fn(*args: object, **_kwargs: object) -> object:
            trap.calls.append((name, args))
            raise NetworkAttempted(f"{name} called with args={args!r} during a supposedly read-only CLI verb")

        return _fn

    monkeypatch.setattr(socket, "getaddrinfo", _record_and_raise("socket.getaddrinfo"))
    monkeypatch.setattr(socket, "create_connection", _record_and_raise("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", _record_and_raise("socket.socket.connect"))
    return trap


# ---------------------------------------------------------------------------
# In-process CLI invocation (the real entry point, never a subprocess)
# ---------------------------------------------------------------------------


def _run_main(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    import sys

    from trw_mcp.server._cli import main

    monkeypatch.setattr(sys, "argv", ["trw-mcp", *argv])
    try:
        main()
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code) if isinstance(exc.code, int) else 1
    return 0


@pytest.fixture
def isolated_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Path, Path]]:
    """An isolated project root + HOME/TRW_USER_DIR, chdir'd into the project.

    Several allowlisted handlers resolve paths from ``Path.cwd()`` (auth,
    channel-doctor's ``--project-dir` default, config-reference's implicit
    cwd) rather than ``TRW_PROJECT_ROOT`` alone, so both are set.
    """
    project_root = tmp_path / "project"
    home = tmp_path / "home"
    project_root.mkdir()
    home.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_USER_DIR", str(home / ".trw-user"))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    yield project_root, home


def _arrange(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    """Run a SETUP command with no bounded-lane role and no network trap active."""
    return _run_main(argv, monkeypatch)


@dataclass
class Case:
    command_path: str
    argv: list[str]
    #: Runs BEFORE the snapshot/traps, with no bounded-lane role -- builds
    #: whatever this verb needs to exercise its real code path rather than
    #: an early "nothing here yet" return.
    setup: Callable[[Path, Path, pytest.MonkeyPatch], list[str]] | None = None
    #: sol round-3 P1: an ignored exit code let an unavailable-backend FAILURE
    #: pass the write/network checks without ever exercising the real read
    #: (``local recall`` against no configured store). Every case now
    #: declares its expected exit explicitly; 0 unless documented otherwise
    #: at the call site below.
    expected_exit: int = 0


def _setup_local_run(project_root: Path, _home: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    _arrange(["local", "init", "--task", "census"], monkeypatch)
    # The run directory is found by walking .trw/runs (its layout is a stable
    # public contract) rather than parsing stdout, which capsys does not
    # reliably intercept from inside a plain helper function.
    runs_root = project_root / ".trw" / "runs" / "census"
    (run_dir,) = sorted(runs_root.iterdir())
    return [str(run_dir)]


def _setup_channel_manifest(project_root: Path, _home: Path, _monkeypatch: pytest.MonkeyPatch) -> list[str]:
    manifest_dir = project_root / ".trw" / "channels"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    # format_version is required by _manifest_loader.load(); a fragment
    # without it (as this file lacked before) raises before validate() even
    # gets to a real read, silently passing an exit-1 that never exercised
    # the actual manifest-parsing code path -- the same shape of gap round-3
    # found in "local recall".
    (manifest_dir / "manifest.yaml").write_text("format_version: manifest/v1\nchannels: []\n", encoding="utf-8")
    return []


def _setup_runs_root(project_root: Path, _home: Path, _monkeypatch: pytest.MonkeyPatch) -> list[str]:
    (project_root / ".trw" / "runs").mkdir(parents=True, exist_ok=True)
    return []


_PRD_BEFORE = str(_GOLDEN_PRDS / "tier_draft.md")
_PRD_AFTER = str(_GOLDEN_PRDS / "tier_approved.md")

#: One case per entry in classified_command_paths(). ``{run_path}`` in argv is
#: substituted with setup's return value (index 0) when present.
CASES: list[Case] = [
    Case("config-reference", ["config-reference"]),
    Case("check-instructions", ["check-instructions", "."]),
    Case("tendencies", ["tendencies"]),
    Case("local status", ["local", "status", "--run-path", "{0}"], setup=_setup_local_run),
    # "local recall" is deliberately NOT a case here: it needs a real,
    # configured store to exercise its actual read path (see
    # test_local_recall_succeeds_and_writes_nothing_under_each_bounded_lane
    # below), and a real store means a real daemon over a loopback socket --
    # exactly what this file's blanket network trap exists to catch. Rather
    # than special-case the trap for one entry, it gets its own test with its
    # own real store and no trap (loopback IPC to a daemon this fixture
    # itself starts is not the network-egress class this trap targets).
    # "repo_root" is positional on this subparser, not --repo-root.
    Case("code risk", ["code", "risk", "."]),
    Case(
        "channel-doctor validate", ["channel-doctor", "--project-dir", ".", "validate"], setup=_setup_channel_manifest
    ),
    Case("channel-doctor stats", ["channel-doctor", "--project-dir", ".", "stats"]),
    Case("channel-doctor scan", ["channel-doctor", "--project-dir", ".", "scan"], setup=_setup_channel_manifest),
    Case("probe budget", ["probe", "budget", "--run-id", "census-run"]),
    Case("telemetry events", ["telemetry", "events", "--session-id", "census-session"]),
    Case("telemetry classify", ["telemetry", "classify", "--path", "CLAUDE.md"]),
    Case("telemetry surface-diff", ["telemetry", "surface-diff", "--snapshot-id-a", "a", "--snapshot-id-b", "b"]),
    Case("telemetry security", ["telemetry", "security"]),
    Case("telemetry channel-stats", ["telemetry", "channel-stats", "--repo-root", "."]),
    Case("telemetry pipeline-health", ["telemetry", "pipeline-health"]),
    Case("prd diff", ["prd", "diff", "--before-path", _PRD_BEFORE, "--after-path", _PRD_AFTER]),
    Case("profile explain", ["profile", "explain"]),
    Case("auth status", ["auth", "status"]),
    Case("version-status", ["version-status"]),
    # "." is not a run inside any formation, so status() returns None and
    # run_formation() reports "no formation is active for this run" and exits
    # 1 -- a truthful, expected report of absence (verified read-only either
    # way: formation._views.status/pause_roll_call only ever read files), not
    # a bug to paper over with a real formation fixture just to force a 0.
    Case("formation status", ["formation", "status", "--run", "."], expected_exit=1),
    Case("gc", ["gc"], setup=_setup_runs_root),
    Case("session-changelog", ["session-changelog", "{0}"], setup=_setup_local_run),
    Case(
        "channel-doctor clean",
        ["channel-doctor", "--project-dir", ".", "clean", "--dry-run"],
        setup=_setup_channel_manifest,
    ),
]


#: "local recall" is allowlisted but covered by its own dedicated test (see
#: module docstring update / the test below) rather than by a CASES entry.
_COVERED_ELSEWHERE: frozenset[str] = frozenset({"local recall"})


def test_every_allowlist_entry_has_a_census_case() -> None:
    """The census must cover the WHOLE allowlist -- a case missing here is untested, not proven safe."""
    covered = {case.command_path for case in CASES} | _COVERED_ELSEWHERE
    assert covered == classified_command_paths(), (
        f"allowlist/census mismatch -- missing from census: {classified_command_paths() - covered}; "
        f"stale in census: {covered - classified_command_paths()}"
    )


#: sol round-3 P1 finding 3: the guard covers TWO bounded-lane markers
#: (TRW_SURFACE_ROLE=reviewer and TRW_DISPATCH_CHILD), but the census only
#: ever ran under the first. Parametrized independently so a write that only
#: happens under the dispatched-child marker (as ``local recall`` did --
#: execute_recall's write suppression checked only reviewer_role_active())
#: cannot hide behind the other marker's test passing.
_BOUNDED_LANE_ENVS: list[tuple[str, str]] = [
    ("TRW_SURFACE_ROLE", "reviewer"),
    ("TRW_DISPATCH_CHILD", "1"),
]


@pytest.mark.parametrize("lane_env", _BOUNDED_LANE_ENVS, ids=["reviewer_role", "dispatched_child"])
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.command_path)
def test_allowlisted_verb_writes_nothing_and_dials_nothing(
    case: Case,
    lane_env: tuple[str, str],
    isolated_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root, home = isolated_roots

    substitutions: list[str] = case.setup(project_root, home, monkeypatch) if case.setup else []
    argv = [part.format(*substitutions) if "{" in part else part for part in case.argv]

    before = _snapshot([project_root, home])

    lane_key, lane_value = lane_env
    monkeypatch.setenv(lane_key, lane_value)
    trap = _install_network_trap(monkeypatch)
    try:
        code = _run_main(argv, monkeypatch)
    except NetworkAttempted as exc:
        pytest.fail(f"{case.command_path!r} attempted a network call: {exc}")

    after = _snapshot([project_root, home])
    assert before == after, f"{case.command_path!r} changed the filesystem: {_diff(before, after)}"
    assert not trap.calls, f"{case.command_path!r} attempted network calls: {trap.calls}"
    assert code == case.expected_exit, (
        f"{case.command_path!r} exited {code}, expected {case.expected_exit} "
        f"(an ignored/mismatched exit can mask a failure that never exercised the real read)"
    )


# ---------------------------------------------------------------------------
# "local recall" — a real store, a real read, under EACH bounded lane
# ---------------------------------------------------------------------------
#
# sol round-3 P2: round-2's case removed TRW_PROJECT_NAMESPACE and never gave
# "local recall" a store to read, so it always hit StoreUnavailableError --
# an exit-1 failure that never reached execute_recall's real code at all,
# and so never exercised (or could have caught a regression in) the write
# suppression that code performs. This uses the SAME daemon-backed fixture
# the rest of the suite uses for real recall behaviour (tests/_memory_fixtures.py,
# already relied on by test_core_247_local_cli_surface.py's own local-recall
# round trip) rather than a hand-rolled store, seeds one entry, and asserts
# BOTH that the recall succeeds and returns it, AND that recalling it leaves
# the entry's own counters untouched under either bounded-lane marker --
# directly covering the exact regression class sol round-3 found
# (execute_recall's write suppression checked only reviewer_role_active(),
# so a dispatched child bumped access_count/recall_count and wrote surface/
# ceremony records that a reviewer lane correctly suppressed).
#
# No network trap here: the daemon is reached over a real loopback TCP
# socket this fixture itself starts, which is exactly the local IPC this
# file's trap is not meant to catch (see the CASES comment above "local
# recall" for why it is not in the blanket-trapped loop instead).


def _seed_recall_entry(daemon_checkout: DaemonCheckout, marker: str) -> str:
    import asyncio

    entry_id = f"L-census-{marker}"

    async def _do() -> None:
        await daemon_checkout.client.store(
            f"census recall marker {marker} unique content",
            daemon_checkout.namespace,
            entry_id=entry_id,
        )

    asyncio.run(_do())
    return entry_id


def _get_recall_entry(daemon_checkout: DaemonCheckout, entry_id: str) -> dict[str, object]:
    import asyncio

    async def _do() -> dict[str, object]:
        result = await daemon_checkout.client.get(entry_id, daemon_checkout.namespace)
        return dict(result["entry"])

    return asyncio.run(_do())


@pytest.mark.parametrize("lane_env", _BOUNDED_LANE_ENVS, ids=["reviewer_role", "dispatched_child"])
def test_local_recall_succeeds_and_writes_nothing_under_each_bounded_lane(
    lane_env: tuple[str, str],
    daemon_checkout: DaemonCheckout,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = f"wf{abs(hash(lane_env)) % 100000}"
    entry_id = _seed_recall_entry(daemon_checkout, marker)
    before_entry = _get_recall_entry(daemon_checkout, entry_id)
    project_root = daemon_checkout.trw_dir.parent
    before_fs = _snapshot([project_root])

    # run_local_recall's default trw_dir is Path.cwd() / ".trw" (not
    # TRW_PROJECT_ROOT) -- without this chdir the CLI reads whatever
    # directory pytest happened to start in, finds no project_namespace, and
    # "succeeds" with an empty result that never touched daemon_checkout's
    # store at all. The same silent-wrong-directory shape as this file's P1.
    monkeypatch.chdir(project_root)
    lane_key, lane_value = lane_env
    monkeypatch.setenv(lane_key, lane_value)
    code = _run_main(["local", "recall", "--query", marker], monkeypatch)
    out = capsys.readouterr().out

    assert code == 0, f"local recall failed to run against a configured store: {out}"
    assert marker in out, f"the seeded entry was not returned by recall: {out}"

    after_entry = _get_recall_entry(daemon_checkout, entry_id)
    assert after_entry.get("access_count") == before_entry.get("access_count"), (
        "local recall must not bump access_count under a bounded lane"
    )
    assert after_entry.get("recall_count") == before_entry.get("recall_count"), (
        "local recall must not bump recall_count under a bounded lane"
    )
    after_fs = _snapshot([project_root])
    assert before_fs == after_fs, f"local recall wrote to the checkout: {_diff(before_fs, after_fs)}"


# ---------------------------------------------------------------------------
# Negative controls: the trap and the snapshot actually catch something
# ---------------------------------------------------------------------------


def test_network_trap_actually_fires_on_a_real_connect_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves the trap isn't a no-op: a bare socket.create_connection call must raise."""
    _install_network_trap(monkeypatch)
    with pytest.raises(NetworkAttempted):
        socket.create_connection(("127.0.0.1", 1), timeout=0.01)


def test_snapshot_diff_actually_catches_a_write(tmp_path: Path) -> None:
    """Proves the snapshot isn't a no-op: writing a file changes the diff."""
    root = tmp_path / "r"
    root.mkdir()
    before = _snapshot([root])
    (root / "new-file.txt").write_text("x", encoding="utf-8")
    after = _snapshot([root])
    assert before != after
    assert "created" in _diff(before, after)


def test_hook_flags_and_doctor_and_memory_migrate_preview_are_not_in_the_census(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sol round-2's three removed entries must stay OUT of classified_command_paths()
    (and therefore out of CASES, via the completeness test above) -- this is the
    negative-space proof that they are refused, not silently re-added."""
    allowed = classified_command_paths()
    assert "hook-flags" not in allowed
    assert "doctor" not in allowed
    assert "memory migrate" not in allowed
