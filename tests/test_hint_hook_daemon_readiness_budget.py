"""The CC-03 hint hook's T1 recall must not sleep through a daemon-readiness poll.

Measured 2026-09-27 (hook-latency profile, cProfile on a cold call, daemon killed
first to force auto-start): 0.961s of literal ``time.sleep()`` across 18 calls in
``trw_memory.daemon.client.DaemonClient._attach``'s discovery-file poll -- ~46% of
a 2.081s ``compute_before_edit_hint`` call and ~40% of the ~2.4-2.6s cold
end-to-end wall time. ``pre-tool-distill-hint.sh`` bounds this call at 2.4-2.5s
and is purely advisory (FR26: never blocking), so it must not pay to spawn and
wait for a fresh daemon; the existing ``MEMORY_DAEMON_AUTOSTART=false`` client-side
override (already used by ``git_hooks/trw-post-commit.sh``, PRD-CORE-310 FR04)
makes ``_attach`` fail closed at once instead of polling, and
``compute_before_edit_hint`` already turns that into "no T1 lessons" so the T2
sidecar hint still serves (see ``trw_mcp/state/_memory_recall.py``'s
``StoreUnavailableError`` handling).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "claude_code" / "hooks"
_HOOK = _DATA_DIR / "pre-tool-distill-hint.sh"


def _prefix_lines_before_bounded_py_call(body: str) -> list[str]:
    """The backslash-continued lines directly above the ``_trw_bounded_python``-bound ``"$_py" -c`` call.

    Written locally rather than importing ``test_edit_hint_hook_budget._bounded_call_prefix``:
    that helper's own ``_BOUND`` regex expects a bare number straight after
    ``_trw_bounded_python`` (``_trw_bounded_python\\s+[\\d.]+``), but the real call is
    ``_trw_bounded_python "${TRW_CC03_BOUND_S:-2.5}" \\`` -- a quoted parameter
    expansion, never a literal -- so that regex has never matched this hook's own
    bounded call (confirmed against the pre-existing, unmodified file; a
    pre-existing gap in that test, out of scope here). This walk instead anchors on
    the literal function name ``_trw_bounded_python`` appearing anywhere in the
    continuation prefix, which is sufficient to identify the one block this fix
    changed.
    """
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r'^\s*"\$_py"\s+-c\s', line):
            continue
        prefix: list[str] = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].rstrip().endswith("\\"):
            prefix.append(lines[cursor].strip().rstrip("\\").strip())
            cursor -= 1
        if any(entry.startswith("_trw_bounded_python ") for entry in prefix):
            return prefix
    return []


def test_the_hook_sets_the_autostart_override_on_its_bounded_call() -> None:
    """The bounded ``compute_before_edit_hint`` call must disable daemon autostart."""
    body = _HOOK.read_text(encoding="utf-8")
    prefix = _prefix_lines_before_bounded_py_call(body)

    assert prefix, 'no _trw_bounded_python-wrapped "$_py" -c call was found in pre-tool-distill-hint.sh'
    assert any(entry.startswith("MEMORY_DAEMON_AUTOSTART=") for entry in prefix), (
        f"pre-tool-distill-hint.sh bounds compute_before_edit_hint but does not set "
        f"MEMORY_DAEMON_AUTOSTART, so a cold T1 recall spawns a daemon and blocks on "
        f"its readiness poll (measured ~0.96s). Env prefix found: {prefix}"
    )


def test_the_override_defaults_to_false_but_stays_settable() -> None:
    """The default is off; an operator can restore autostart with one env var.

    Every flag or off-switch names how to disable it (FAST-RULES UPDATE): the
    hook comment documents ``TRW_CC03_DAEMON_AUTOSTART=true`` as the escape hatch,
    and this test proves the shell expression actually implements that escape
    rather than just describing it in prose.
    """
    body = _HOOK.read_text(encoding="utf-8")

    assert 'MEMORY_DAEMON_AUTOSTART="${TRW_CC03_DAEMON_AUTOSTART:-false}"' in body
    assert "TRW_CC03_DAEMON_AUTOSTART=true" in body, "the disable instructions must be documented in the hook itself"


def test_bundled_and_claude_mirror_stay_byte_identical() -> None:
    """The bundle is the source of truth (check-bundle-sync.sh); the dev mirror must match it."""
    repo_root = Path(__file__).resolve().parents[2]  # tests/ -> trw-mcp/ -> repo root
    mirror_path = repo_root / ".claude" / "hooks" / "pre-tool-distill-hint.sh"
    if not mirror_path.is_file():
        pytest.skip("not running inside a checkout with a .claude hooks mirror")

    bundled = _HOOK.read_text(encoding="utf-8")
    mirror = mirror_path.read_text(encoding="utf-8")

    assert bundled == mirror


def test_attach_with_autostart_off_never_sleeps(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Deterministic proof of the behaviour the env var buys: zero polling sleeps.

    Fakes ``read_live_discovery`` to always report an absent daemon (the exact
    state a killed/never-started daemon leaves) and monkeypatches ``time.sleep``
    to fail the test if it is ever called. With ``memory_daemon_autostart=False``
    (what the hook's ``MEMORY_DAEMON_AUTOSTART=false`` maps onto), ``_attach``
    must raise :class:`DaemonUnreachableError` at once -- the exact production
    call site ``trw_mcp.state._daemon_store`` reaches via
    ``_require_matching_security`` -- never entering the discovery-poll loop.
    """
    pytest.importorskip("fastmcp")
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._discovery import DiscoveryAbsent
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.exceptions import DaemonUnreachableError
    from trw_memory.models.config import MemoryConfig

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    paths = DaemonPaths.resolve()

    def _absent(_paths: object) -> DiscoveryAbsent:
        return DiscoveryAbsent(reason="no discovery file")

    monkeypatch.setattr("trw_memory.daemon.client.read_live_discovery", _absent)

    def _sleep_must_not_be_called(_seconds: float) -> None:
        raise AssertionError("time.sleep() was called: the readiness poll ran despite autostart being off")

    monkeypatch.setattr("trw_memory.daemon.client.time.sleep", _sleep_must_not_be_called)

    config = MemoryConfig(memory_daemon_autostart=False, memory_daemon_startup_timeout_seconds=5.0)
    client = DaemonClient("test-token", config=config, paths=paths)

    with pytest.raises(DaemonUnreachableError, match="auto-start is off"):
        client._attach()


def test_attach_with_autostart_on_still_polls_as_before(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Boundary/negative case: the default (autostart on) is untouched by this fix.

    Every other daemon caller (the long-lived MCP server, ``trw-mcp doctor``,
    everything that does not set ``MEMORY_DAEMON_AUTOSTART=false``) keeps the
    exact spawn-then-poll behaviour this fix does not change. Uses a fake clock
    so the assertion is deterministic and fast rather than sleeping for real.
    """
    pytest.importorskip("fastmcp")
    from trw_memory.daemon import DaemonPaths
    from trw_memory.daemon._discovery import DiscoveryAbsent
    from trw_memory.daemon.client import DaemonClient
    from trw_memory.exceptions import DaemonUnreachableError
    from trw_memory.models.config import MemoryConfig

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "userhome"))
    paths = DaemonPaths.resolve()

    def _absent(_paths: object) -> DiscoveryAbsent:
        return DiscoveryAbsent(reason="no discovery file")

    monkeypatch.setattr("trw_memory.daemon.client.read_live_discovery", _absent)
    monkeypatch.setattr("trw_memory.daemon.client.start_daemon_detached", lambda _paths: object())

    sleep_calls = []
    fake_clock = {"t": 0.0}

    def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        fake_clock["t"] += seconds

    def _fake_monotonic() -> float:
        return fake_clock["t"]

    monkeypatch.setattr("trw_memory.daemon.client.time.sleep", _fake_sleep)
    monkeypatch.setattr("trw_memory.daemon.client.time.monotonic", _fake_monotonic)

    config = MemoryConfig(memory_daemon_autostart=True, memory_daemon_startup_timeout_seconds=0.5)
    client = DaemonClient("test-token", config=config, paths=paths)

    with pytest.raises(DaemonUnreachableError, match="did not publish a discovery file"):
        client._attach()

    assert len(sleep_calls) > 0, "the default (autostart on) path must still poll for readiness"
    assert all(s == pytest.approx(0.05) for s in sleep_calls), "the poll interval itself is unchanged by this fix"
