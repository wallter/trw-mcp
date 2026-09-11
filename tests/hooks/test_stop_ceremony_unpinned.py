"""PRD-FIX-117 — stop-ceremony.sh must observe delivery in unpinned sessions.

Regression target (verified live 2026-07-24): an unpinned session that calls
``trw_deliver`` successfully was told, on every stop, that it had not. The
telemetry fallback path writes an unpinned delivery as::

    {"event":"tool_invocation","tool_name":"trw_deliver","success":true, ...}

while the hook grepped ``.trw/context/session-events.jsonl`` for the event *type*
``trw_deliver_complete``. ``has_event`` matches the ``"event"`` field only, so
that check was structurally blind — not merely unlucky.

Every scenario below runs the **real** hook script (both the bundled copy and the
``.claude`` mirror) against a fixture built from **real rows** copied verbatim out
of the live ``.trw/context/session-events.jsonl`` on 2026-07-24. Only the ``ts``
value is ever rewritten, so the shape being matched is the shape the writer
actually emits. This defect's signature is that synthetic unit tests pass while
the live path fails.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests._layout import requires_monorepo

_TESTS_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TESTS_ROOT.parent.parent

_BUNDLED_HOOK = _TESTS_ROOT.parent / "src" / "trw_mcp" / "data" / "hooks" / "stop-ceremony.sh"
_MIRROR_HOOK = _REPO_ROOT / ".claude" / "hooks" / "stop-ceremony.sh"
_BUNDLED_LIB = _BUNDLED_HOOK.parent / "lib-trw.sh"
_MIRROR_LIB = _MIRROR_HOOK.parent / "lib-trw.sh"

# Rows 120-192 of the live log on 2026-07-24, verbatim. Contains real successful
# AND real failed trw_deliver invocations, and zero trw_deliver_complete rows —
# which is precisely the state the old predicate could not read.
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "session-events-2026-07-24.jsonl"

_DEFAULT_WINDOW_MIN = 240

# Commands the hook + lib need on PATH when we deliberately remove jq.
_PATH_TOOLS = (
    "awk",
    "cat",
    "date",
    "dirname",
    "expr",
    "find",
    "git",
    "grep",
    "head",
    "mkdir",
    "mv",
    "printf",
    "rm",
    "sed",
    "sh",
    "tail",
    "touch",
    "tr",
    "wc",
)

_HOOKS = pytest.mark.parametrize(
    "hook",
    [pytest.param(_BUNDLED_HOOK, id="bundled"), pytest.param(_MIRROR_HOOK, id="mirror", marks=requires_monorepo)],
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _iso(minutes_ago: float) -> str:
    """A UTC ISO-8601 stamp in the same format the telemetry writer emits."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _rows() -> list[str]:
    return _FIXTURE.read_text(encoding="utf-8").splitlines()


def _restamp(row: str, ts: str) -> str:
    """Rewrite ONLY the ts value, leaving the rest of the real row untouched."""
    restamped, count = re.subn(r'"ts":\s*"[^"]*"', f'"ts": "{ts}"', row, count=1)
    assert count == 1, f"fixture row has no ts field: {row[:80]}"
    return restamped


def _rows_with_delivers_aged(minutes_ago: float) -> list[str]:
    """The real log, with EVERY trw_deliver row aged to a controlled age.

    All other rows keep their verbatim timestamps. Ageing every delivery row is
    what makes the negative cases deterministic — the fixture's own stamps drift
    out of the recency window as wall-clock time passes.
    """
    ts = _iso(minutes_ago)
    out = []
    for row in _rows():
        if json.loads(row).get("tool_name") == "trw_deliver":
            out.append(_restamp(row, ts))
        else:
            out.append(row)
    return out


def _deliver_rows(*, success: bool) -> list[str]:
    out = []
    for row in _rows():
        obj = json.loads(row)
        if obj.get("event") == "tool_invocation" and obj.get("tool_name") == "trw_deliver":
            if bool(obj.get("success")) is success:
                out.append(row)
    return out


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".trw" / "context").mkdir(parents=True, exist_ok=True)
    (root / ".trw" / "runtime").mkdir(parents=True, exist_ok=True)
    return root


def _write_session_events(root: Path, rows: list[str]) -> None:
    path = root / ".trw" / "context" / "session-events.jsonl"
    path.write_text("".join(f"{row}\n" for row in rows), encoding="utf-8")


def _add_run(root: Path, task: str, run_id: str, events: list[str]) -> Path:
    run = root / ".trw" / "runs" / task / run_id
    (run / "meta").mkdir(parents=True, exist_ok=True)
    (run / "meta" / "run.yaml").write_text(f"task: {task}\n", encoding="utf-8")
    (run / "meta" / "events.jsonl").write_text("".join(f"{e}\n" for e in events), encoding="utf-8")
    return run


def _pin(root: Path, session_id: str, run: Path) -> None:
    (root / ".trw" / "runtime" / "pins.json").write_text(
        json.dumps({session_id: {"run_path": str(run)}}), encoding="utf-8"
    )


def _fake_bin_without_jq(tmp_path: Path) -> Path:
    """A PATH directory holding every tool the hook needs EXCEPT jq."""
    bin_dir = tmp_path / "nojq-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool in _PATH_TOOLS:
        resolved = shutil.which(tool)
        if resolved:
            target = bin_dir / tool
            if not target.exists():
                target.symlink_to(resolved)
    return bin_dir


def _run_hook(
    hook: Path,
    root: Path,
    *,
    session_id: str | None = None,
    env_extra: dict[str, str] | None = None,
    path: str | None = None,
) -> subprocess.CompletedProcess[str]:
    payload: dict[str, str] = {"hook_event_name": "Stop"}
    if session_id is not None:
        payload["session_id"] = session_id
    env = {
        "PATH": path or "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(root),
        "CLAUDE_PROJECT_DIR": str(root),
    }
    env.update(env_extra or {})
    return subprocess.run(
        ["sh", str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def _unpinned_project_with(tmp_path: Path, rows: list[str]) -> Path:
    """Identity-unknown session + a FOREIGN run with events and no deliver marker.

    This drives conditional advice, not a blocking exit (CORE269). A valid
    recent delivery still suppresses that advice.
    """
    root = _make_project(tmp_path)
    _add_run(
        root,
        "foreigntask",
        "20260101T000000Z-f0f0f0f0",
        ['{"ts":"2026-01-01T00:00:00Z","event":"file_modified"}'],
    )
    if rows:
        _write_session_events(root, rows)
    return root


# --------------------------------------------------------------------------- #
# FR04 — the two copies must not drift
# --------------------------------------------------------------------------- #
@requires_monorepo
def test_bundled_and_repo_hook_identical() -> None:
    assert _BUNDLED_HOOK.read_bytes() == _MIRROR_HOOK.read_bytes(), (
        "stop-ceremony.sh drifted between the bundled copy and the .claude mirror"
    )


@requires_monorepo
def test_bundled_and_repo_lib_identical() -> None:
    assert _BUNDLED_LIB.read_bytes() == _MIRROR_LIB.read_bytes(), (
        "lib-trw.sh drifted between the bundled copy and the .claude mirror"
    )


# --------------------------------------------------------------------------- #
# Evidence guard — the fixture must keep reproducing the real defect conditions
# --------------------------------------------------------------------------- #
def test_fixture_reproduces_the_real_write_shape() -> None:
    rows = [json.loads(row) for row in _rows()]
    assert rows, "fixture is empty"
    assert not [r for r in rows if r.get("event") == "trw_deliver_complete"], (
        "fixture must contain NO trw_deliver_complete rows — that absence is the defect"
    )
    successful = _deliver_rows(success=True)
    failed = _deliver_rows(success=False)
    assert successful, "fixture must contain a real successful trw_deliver invocation"
    assert failed, "fixture must contain a real failed trw_deliver invocation"


# --------------------------------------------------------------------------- #
# FR02 — ownership, never recency
#
# PRD-FIX-117 FR02 declared this test and it was never written: the FR shipped in
# 0d4c605c9e, the PRD cited `test_no_foreign_run_attribution` in three places, and
# the name existed nowhere in code. An independent review caught it. That is the
# same defect UF-046 recorded for PRD-QUAL-056 — a PRD citing a nonexistent test —
# reproduced inside the very field introduced to prevent it.
# --------------------------------------------------------------------------- #
@_HOOKS
def test_no_foreign_run_attribution(hook: Path, tmp_path: Path) -> None:
    """A session that owns no run must not be judged against a foreign one.

    The project contains a foreign run whose events would drive a block, and the
    session declares an identity that owns no pin. The hook must decline to
    enforce rather than borrow the stranger's run.

    Non-vacuity: the same fixture WITHOUT an identity (the legacy branch) still
    reaches the advisory path, so this asserts the ownership path specifically and
    not merely that the fixture is quiet.
    """
    if not shutil.which("jq"):
        pytest.skip("pin resolution requires jq")
    root = _unpinned_project_with(tmp_path, [])

    # Identity known, owns no pin -> must not adopt the foreign run.
    owned = _run_hook(hook, root, env_extra={"TRW_SESSION_ID": "sess-owns-nothing"})

    assert owned.returncode == 0, f"a session owning no run was enforced against a foreign run: {owned.stderr}"
    assert "foreigntask" not in owned.stderr, f"the foreign run leaked into the hook's output: {owned.stderr}"


@_HOOKS
def test_foreign_run_is_reachable_without_identity(hook: Path, tmp_path: Path) -> None:
    """Non-vacuity guard for the test above.

    Without a session identity the legacy branch still resolves the foreign run,
    so the fixture genuinely presents a run that COULD be adopted. If this ever
    stops emitting advice, `test_no_foreign_run_attribution` loses this control.
    """
    root = _unpinned_project_with(tmp_path, [])

    result = _run_hook(hook, root)

    assert result.returncode == 0
    assert "If you have material unfinished work" in result.stderr


# --------------------------------------------------------------------------- #
# FR01 — an unpinned delivery clears the gate
# --------------------------------------------------------------------------- #
@_HOOKS
def test_unpinned_deliver_satisfies_gate(hook: Path, tmp_path: Path) -> None:
    """Genuine historical trw_deliver rows, re-stamped as recent, clear the gate."""
    root = _unpinned_project_with(tmp_path, _rows_with_delivers_aged(90))

    result = _run_hook(hook, root)

    assert result.returncode == 0, f"unpinned delivery was still nagged: {result.stderr}"
    assert "If you have material unfinished work" not in result.stderr


@_HOOKS
def test_pinned_session_cleared_by_recent_unpinned_deliver(hook: Path, tmp_path: Path) -> None:
    """The fallback-B site: a pinned run with events but no run-scoped marker."""
    if not shutil.which("jq"):
        pytest.skip("pin resolution requires jq")
    root = _make_project(tmp_path)
    run = _add_run(
        root,
        "mytask",
        "20260202T000000Z-aaaa1111",
        ['{"ts":"2026-02-02T00:00:00Z","event":"file_modified"}'],
    )
    _pin(root, "sess-p", run)
    _write_session_events(root, [_restamp(_deliver_rows(success=True)[-1], _iso(10))])

    result = _run_hook(hook, root, session_id="sess-p")

    assert result.returncode == 0, result.stderr
    assert "If you have material unfinished work" not in result.stderr


@_HOOKS
def test_pinned_deliver_still_satisfies_gate(hook: Path, tmp_path: Path) -> None:
    """Fallback A is untouched: a run-scoped trw_deliver_complete still clears."""
    if not shutil.which("jq"):
        pytest.skip("pin resolution requires jq")
    root = _make_project(tmp_path)
    run = _add_run(
        root,
        "donetask",
        "20260303T000000Z-bbbb2222",
        [
            '{"ts":"2026-03-03T00:00:00Z","event":"file_modified"}',
            '{"ts":"2026-03-03T00:01:00Z","event":"trw_deliver_complete","session_id":"sess-d"}',
        ],
    )
    _pin(root, "sess-d", run)

    result = _run_hook(hook, root, session_id="sess-d")

    assert result.returncode == 0, result.stderr
    assert "If you have material unfinished work" not in result.stderr


# --------------------------------------------------------------------------- #
# CORE269 supersedes FIX117 FR03 blocking; true detection remains advisory
# --------------------------------------------------------------------------- #
@_HOOKS
def test_true_negative_still_nudges(hook: Path, tmp_path: Path) -> None:
    """No delivery row at all: the reminder must still fire."""
    rows = [row for row in _rows() if '"trw_deliver"' not in row]
    root = _unpinned_project_with(tmp_path, rows)

    result = _run_hook(hook, root)

    assert result.returncode == 0, f"an undelivered session was NOT nudged: {result.stderr}"
    assert "If you have material unfinished work" in result.stderr


@_HOOKS
def test_missing_delivery_never_creates_counter_or_lock(hook: Path, tmp_path: Path) -> None:
    """CORE269 supersedes FIX117 FR03's coercive two-reminder state."""
    root = _unpinned_project_with(tmp_path, [])
    for _ in range(3):
        result = _run_hook(hook, root)
        assert result.returncode == 0
        assert "If you have material unfinished work" in result.stderr
        assert not (root / ".trw/context/stop_block_count").exists()
        assert not (root / ".trw/context/stop_hook.lock").exists()


@_HOOKS
def test_failed_deliver_does_not_satisfy(hook: Path, tmp_path: Path) -> None:
    """Real ``"success": false`` rows, re-stamped as recent, must not clear."""
    failed = set(_deliver_rows(success=False))
    rows = [
        _restamp(row, _iso(5)) if row in failed else row
        for row in _rows()
        if row in failed or '"trw_deliver"' not in row
    ]
    assert any('"trw_deliver"' in row for row in rows)
    root = _unpinned_project_with(tmp_path, rows)

    result = _run_hook(hook, root)

    assert result.returncode == 0, "missing delivery alone is advisory"
    assert "If you have material unfinished work" in result.stderr


@_HOOKS
def test_stale_deliver_outside_window(hook: Path, tmp_path: Path) -> None:
    """Recency is judged on the ROW's ts — the log file itself is freshly written."""
    root = _unpinned_project_with(tmp_path, _rows_with_delivers_aged(_DEFAULT_WINDOW_MIN + 60))

    result = _run_hook(hook, root)

    assert result.returncode == 0
    assert "If you have material unfinished work" in result.stderr


# --------------------------------------------------------------------------- #
# NFR01 — fail open: an unreadable log yields TODAY's behaviour (nudge)
# --------------------------------------------------------------------------- #
@_HOOKS
def test_missing_session_events_fails_open(hook: Path, tmp_path: Path) -> None:
    root = _unpinned_project_with(tmp_path, [])
    assert not (root / ".trw" / "context" / "session-events.jsonl").exists()

    result = _run_hook(hook, root)

    assert result.returncode == 0
    assert "If you have material unfinished work" in result.stderr
    assert "Traceback" not in result.stderr
    assert "not found" not in result.stderr


@_HOOKS
def test_malformed_jsonl_fails_open(hook: Path, tmp_path: Path) -> None:
    """A truncated final line must not be read as a delivery."""
    truncated = _deliver_rows(success=True)[-1][:60]
    rows = [row for row in _rows() if '"trw_deliver"' not in row] + [truncated]
    root = _unpinned_project_with(tmp_path, rows)

    result = _run_hook(hook, root)

    assert result.returncode == 0
    assert "If you have material unfinished work" in result.stderr


@_HOOKS
def test_malformed_tail_does_not_hide_a_real_delivery(hook: Path, tmp_path: Path) -> None:
    """A truncated final line must not abort the scan of the valid rows above it."""
    rows = [
        _restamp(_deliver_rows(success=True)[-1], _iso(3)),
        _deliver_rows(success=True)[0][:60],
    ]
    root = _unpinned_project_with(tmp_path, rows)

    result = _run_hook(hook, root)

    assert result.returncode == 0, result.stderr
    assert "If you have material unfinished work" not in result.stderr


# --------------------------------------------------------------------------- #
# NFR02 — same verdict without jq
# --------------------------------------------------------------------------- #
@_HOOKS
@pytest.mark.parametrize(
    ("minutes_ago", "expected"),
    [(30, 0), (_DEFAULT_WINDOW_MIN + 60, 0)],
    ids=["recent", "stale"],
)
def test_no_jq_grep_fallback(hook: Path, tmp_path: Path, minutes_ago: int, expected: int) -> None:
    rows = _rows_with_delivers_aged(minutes_ago)

    with_jq = _unpinned_project_with(tmp_path / "withjq", rows)
    without_jq = _unpinned_project_with(tmp_path / "nojq", rows)
    bin_dir = _fake_bin_without_jq(tmp_path)
    assert shutil.which("jq", path=str(bin_dir)) is None

    jq_result = _run_hook(hook, with_jq)
    nojq_result = _run_hook(hook, without_jq, path=str(bin_dir))

    assert jq_result.returncode == expected, jq_result.stderr
    assert nojq_result.returncode == expected, nojq_result.stderr
    assert nojq_result.returncode == jq_result.returncode
    assert ("If you have material unfinished work" in jq_result.stderr) == (minutes_ago > _DEFAULT_WINDOW_MIN)
    assert ("If you have material unfinished work" in nojq_result.stderr) == (minutes_ago > _DEFAULT_WINDOW_MIN)


# --------------------------------------------------------------------------- #
# The window is a documented knob (FR01 / rollback plan)
# --------------------------------------------------------------------------- #
@_HOOKS
def test_window_zero_disables_the_predicate(hook: Path, tmp_path: Path) -> None:
    """The rollback knob: window 0 restores the pre-fix reminder behaviour."""
    root = _unpinned_project_with(tmp_path, _rows_with_delivers_aged(1))

    enabled = _run_hook(hook, root)
    (root / ".trw" / "context" / "stop_block_count").unlink(missing_ok=True)
    disabled = _run_hook(hook, root, env_extra={"TRW_STOP_DELIVER_WINDOW_MIN": "0"})

    assert enabled.returncode == 0
    assert "If you have material unfinished work" not in enabled.stderr
    assert disabled.returncode == 0
    assert "If you have material unfinished work" in disabled.stderr


@_HOOKS
def test_window_config_yaml_override_is_wired(hook: Path, tmp_path: Path) -> None:
    """A tightened project window must actually tighten the verdict."""
    root = _unpinned_project_with(tmp_path, _rows_with_delivers_aged(45))

    default_window = _run_hook(hook, root)
    (root / ".trw" / "context" / "stop_block_count").unlink(missing_ok=True)
    (root / ".trw" / "config.yaml").write_text("stop_deliver_window_minutes: 5\n", encoding="utf-8")
    tightened = _run_hook(hook, root)

    assert default_window.returncode == 0, "a 45-minute-old delivery is inside the 240m default"
    assert "If you have material unfinished work" not in default_window.stderr
    assert "If you have material unfinished work" in tightened.stderr
    assert tightened.returncode == 0, "a 5-minute window must exclude a 45-minute-old delivery"


@pytest.mark.parametrize("identity", ["pinned", "unpinned", "unknown", "no-work"])
@pytest.mark.parametrize("hook_name", ["stop-ceremony.sh", "session-end.sh"])
def test_missing_deliver_is_not_stop_failure(tmp_path: Path, identity: str, hook_name: str) -> None:
    """CORE269 FR02: execute installed hooks, preserving work without a delivery claim."""
    root = _make_project(tmp_path)
    installed = root / "hooks"
    shutil.copytree(_BUNDLED_HOOK.parent, installed)
    before_runs = []
    before_state: dict[Path, bytes] = {}
    session_id = None if identity == "unknown" else "caller"
    if identity != "no-work":
        run = _add_run(root, "work", "20260101T000000Z-abc", ['{"event":"file_modified"}'])
        if identity == "pinned":
            _pin(root, "caller", run)
        before_runs = list((root / ".trw" / "runs").rglob("run.yaml"))
        before_state = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    for _ in range(3):
        result = _run_hook(installed / hook_name, root, session_id=session_id)
        assert result.returncode == 0, result.stderr
        assert not (root / ".trw/context/stop_block_count").exists()
        assert not (root / ".trw/context/stop_hook.lock").exists()
        assert "captures your learnings" not in result.stderr
        assert "Session complete" not in result.stderr
        if identity in {"pinned", "unknown"}:
            assert "If you have material unfinished work" in result.stderr
            assert "next-read pointer" in result.stderr
    assert list((root / ".trw" / "runs").rglob("run.yaml")) == before_runs
    for path, content in before_state.items():
        assert path.read_bytes() == content
    assert not list((root / ".trw").rglob("checkpoints.jsonl"))


@_HOOKS
@pytest.mark.parametrize("delivered", [False, True])
def test_stop_does_not_mutate_historical_counter_or_lock(hook: Path, tmp_path: Path, delivered: bool) -> None:
    """Existing stop bookkeeping is not a new coercive or destructive action."""
    root = _unpinned_project_with(tmp_path, _rows_with_delivers_aged(1) if delivered else [])
    counter = root / ".trw/context/stop_block_count"
    counter.write_text("2\n", encoding="utf-8")
    lock = root / ".trw/context/stop_hook.lock"
    lock.mkdir()
    marker = lock / "historical-owner"
    marker.write_text("retain", encoding="utf-8")
    result = _run_hook(hook, root)
    assert result.returncode == 0
    assert counter.read_text(encoding="utf-8") == "2\n"
    assert marker.read_text(encoding="utf-8") == "retain"
    assert ("If you have material unfinished work" in result.stderr) is not delivered
