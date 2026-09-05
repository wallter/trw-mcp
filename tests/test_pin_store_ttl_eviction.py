"""PRD-CORE-248 FR05 / NFR03 — pin eviction by heartbeat TTL AND creator-PID liveness.

``pin_ttl_hours`` has always been described as "time-to-live (hours) for entries
in the persistent pin store before GC evicts them" and nothing honoured that for
the store itself. Live inspection at authoring time: 15 entries, 2 with a live
creator PID, 13 with heartbeats 13.7 to 34.9 days old — all 13 surviving every
load and every one of them offered to the agent as a ``candidate_runs`` hint.

These tests drive the real ``load_pin_store`` / ``prune_pin_store_orphans`` /
``_candidate_run_hints`` functions against a real pins.json.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import (  # noqa: F401
    FormationFixture,
    formation_env,
    write_pin,
)
from tests._structlog_capture import captured_structlog as captured_structlog

#: A PID that is not a live process. 2**22 is above the default Linux pid_max
#: (4194304 is the ceiling, and this box's pid_max is far below it), so it can
#: never collide with a real process during the test run.
DEAD_PID = 4194303


def _iso(delta_hours: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=delta_hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _entry(run_dir: Path, *, pid: int, heartbeat: Any) -> dict[str, Any]:
    return {
        "run_path": str(run_dir),
        "created_ts": _iso(48),
        "last_heartbeat_ts": heartbeat,
        "client_hint": None,
        "pid": pid,
    }


def _write_pins(payload: dict[str, Any]) -> Path:
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, pin_store_path

    pins_path = pin_store_path()
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    pins_path.write_text(json.dumps(payload), encoding="utf-8")
    invalidate_pin_store_cache()
    return pins_path


@pytest.fixture
def run_dirs(tmp_path: Path) -> dict[str, Path]:
    """Four existing run directories so path-existence never decides the outcome."""
    made: dict[str, Path] = {}
    for name in ("expired", "fresh-heartbeat", "live-pid", "unparseable"):
        d = tmp_path / name
        d.mkdir()
        made[name] = d
    return made


# ---------------------------------------------------------------------------
# FR05 acceptance
# ---------------------------------------------------------------------------


def test_evicts_dead_pid_past_ttl_and_retains_fresh(
    run_dirs: dict[str, Path], captured_structlog: list[dict[str, object]]
) -> None:
    """The conjunction: dead PID AND past-TTL evicts; every other combination survives."""
    from trw_mcp.state._pin_store import load_pin_store

    _write_pins(
        {
            "expired": _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(72)),
            "fresh-heartbeat": _entry(run_dirs["fresh-heartbeat"], pid=DEAD_PID, heartbeat=_iso(1)),
            "live-pid": _entry(run_dirs["live-pid"], pid=os.getpid(), heartbeat=_iso(720)),
            "unparseable": _entry(run_dirs["unparseable"], pid=DEAD_PID, heartbeat="t"),
        }
    )

    result = load_pin_store()

    assert set(result) == {"fresh-heartbeat", "live-pid", "unparseable"}
    evicted = [log for log in captured_structlog if log.get("event") == "pin_ttl_expired_evicted"]
    assert len(evicted) == 1, f"expected exactly one eviction event, got {captured_structlog}"
    assert evicted[0]["pin_key"] == "expired"
    assert evicted[0]["pid"] == DEAD_PID
    assert evicted[0]["heartbeat_age_hours"] >= 24.0
    assert evicted[0]["pin_ttl_hours"] == 24


def test_live_pid_survives_an_arbitrarily_old_heartbeat(run_dirs: dict[str, Path]) -> None:
    """A running server's pin is never evicted by age alone."""
    from trw_mcp.state._pin_store import load_pin_store

    _write_pins({"live": _entry(run_dirs["live-pid"], pid=os.getpid(), heartbeat=_iso(24 * 365))})
    assert "live" in load_pin_store()


def test_unparseable_heartbeat_is_retained(run_dirs: dict[str, Path]) -> None:
    """NFR02: ambiguity resolves toward retention — losing a live pin is worse."""
    from trw_mcp.state._pin_store import load_pin_store

    _write_pins({"weird": _entry(run_dirs["unparseable"], pid=DEAD_PID, heartbeat="not-a-timestamp")})
    assert "weird" in load_pin_store()

    _write_pins({"missing": {"run_path": str(run_dirs["unparseable"]), "pid": DEAD_PID}})
    assert "missing" in load_pin_store()


def test_non_positive_ttl_disables_expiry_rather_than_purging(run_dirs: dict[str, Path]) -> None:
    """A non-positive TTL must not become 'evict everything'.

    ``pin_ttl_hours`` carries ``ge=1``, so config cannot reach this branch — it
    is a parameter-domain guard on the exported predicate, and it is asserted at
    the predicate rather than pretended to be a config path.
    """
    from trw_mcp.state._pin_ttl import pin_entry_is_expired

    entry = _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(10_000))
    assert pin_entry_is_expired(entry, pin_ttl_hours=0) == (False, None)
    assert pin_entry_is_expired(entry, pin_ttl_hours=-5) == (False, None)
    expired, age = pin_entry_is_expired(entry, pin_ttl_hours=24)
    assert expired is True
    assert age is not None and age > 24


def test_prune_persists_the_ttl_eviction_to_disk(run_dirs: dict[str, Path]) -> None:
    """The boot prune writes the shrunken store back, so it stops re-warning every load."""
    from trw_mcp.state._pin_store import prune_pin_store_orphans

    pins_path = _write_pins(
        {
            "expired": _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(72)),
            "keep": _entry(run_dirs["fresh-heartbeat"], pid=DEAD_PID, heartbeat=_iso(1)),
        }
    )

    removed = prune_pin_store_orphans()

    assert removed == 1
    on_disk = json.loads(pins_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {"keep"}
    # Surviving entries keep every field they arrived with (migration safety).
    assert set(on_disk["keep"]) == {"run_path", "created_ts", "last_heartbeat_ts", "client_hint", "pid"}


# ---------------------------------------------------------------------------
# NFR03: eviction touches pins.json only
# ---------------------------------------------------------------------------


def test_eviction_never_deletes_run_directories(run_dirs: dict[str, Path]) -> None:
    """After an eviction pass that removes N entries, all N run directories remain."""
    from trw_mcp.state._pin_store import load_pin_store, prune_pin_store_orphans

    payload = {f"expired-{i}": _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(72 + i)) for i in range(3)}
    _write_pins(payload)

    assert load_pin_store() == {}
    assert prune_pin_store_orphans() == 3
    assert run_dirs["expired"].exists(), "eviction must never touch the referenced run directory"


# ---------------------------------------------------------------------------
# FR05 second half: the hints the agent actually sees
# ---------------------------------------------------------------------------


def test_candidate_hints_exclude_expired_pins(run_dirs: dict[str, Path]) -> None:
    """candidate_runs excludes every pin the store would evict, keeping the rest."""
    from trw_mcp.tools._ceremony_runtime_helpers import _candidate_run_hints

    _write_pins(
        {
            "expired": _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(72)),
            "fresh-heartbeat": _entry(run_dirs["fresh-heartbeat"], pid=DEAD_PID, heartbeat=_iso(1)),
            "live-pid": _entry(run_dirs["live-pid"], pid=os.getpid(), heartbeat=_iso(720)),
        }
    )

    hints = _candidate_run_hints(limit=10)

    paths = {str(hint["run_path"]) for hint in hints}
    assert str(run_dirs["expired"]) not in paths
    assert paths == {str(run_dirs["fresh-heartbeat"]), str(run_dirs["live-pid"])}


def test_candidate_hints_filter_survives_a_warm_read_cache(run_dirs: dict[str, Path]) -> None:
    """A cached load must not be able to smuggle an expired pin into the hints.

    The pin store caches for one second. Priming the cache with the pre-eviction
    view is exactly the race the second filter exists for: the hint boundary
    applies the cutoff itself rather than trusting the load.
    """
    from trw_mcp.state import _pin_store
    from trw_mcp.tools._ceremony_runtime_helpers import _candidate_run_hints

    expired = _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(72))
    _write_pins({"expired": expired, "keep": _entry(run_dirs["live-pid"], pid=os.getpid(), heartbeat=_iso(1))})
    # Force a cache that still contains the expired entry.
    _pin_store._pin_store_cache = {"expired": expired}
    _pin_store._pin_store_cache_ts = __import__("time").monotonic()
    _pin_store._pin_store_cache_mtime_ns = _pin_store.pin_store_path().stat().st_mtime_ns
    try:
        assert _candidate_run_hints(limit=10) == []
    finally:
        _pin_store.invalidate_pin_store_cache()


def test_eviction_telemetry_carries_no_run_content(
    run_dirs: dict[str, Path], captured_structlog: list[dict[str, object]]
) -> None:
    """NFR03: the event carries counts, a pid, and an age — never learning content."""
    from trw_mcp.state._pin_store import load_pin_store

    _write_pins({"expired": _entry(run_dirs["expired"], pid=DEAD_PID, heartbeat=_iso(72))})
    load_pin_store()
    event = next(log for log in captured_structlog if log.get("event") == "pin_ttl_expired_evicted")
    assert set(event) <= {
        "event",
        "log_level",
        "pin_key",
        "pid",
        "heartbeat_age_hours",
        "pin_ttl_hours",
    }


# --- PRD-CORE-265-FR08: stale is REPORTED, never applied ---------------------


def test_formation_member_with_missing_or_expired_pin_is_stale(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR08. Two distinct reasons, no state transition, and no scheduler.

    ATTRIBUTION. Guards ``formation/_status._staleness`` and its use of the pin
    store's OWN ``pin_entry_is_expired``. The "pin absent" branch dies if the
    raw-store read in ``_raw_pin_store`` is replaced by ``load_pin_store``,
    because that applies the eviction pass and collapses both cases into one
    answer. The unchanged-status assertion guards the deliberate refusal to
    transition: an agent that is merely slow is indistinguishable from one that
    died, and guessing would either discard live work or fabricate completion.
    """
    import threading

    from trw_mcp.formation import create, join, status

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
    join("release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-live")
    join("release-train", "impl-2", formation_env.member_runs["impl-2"], pin_key="pin-old")

    # Only impl-2 has a pin entry, and its heartbeat is far past the TTL with a
    # dead creator PID — the conjunction _pin_ttl requires.
    write_pin(formation_env, "pin-old", formation_env.member_runs["impl-2"], age_hours=500.0)

    threads_before = threading.active_count()
    board = status(run_path=formation_env.orchestrator_run)
    assert board is not None
    rows = {row.member_id: row for row in board.rows}

    assert rows["impl-1"].stale is True
    assert rows["impl-1"].stale_reason == "pin absent", rows["impl-1"].stale_reason

    assert rows["impl-2"].stale is True
    assert rows["impl-2"].stale_reason.startswith("pin expired"), rows["impl-2"].stale_reason
    assert "h, ttl" in rows["impl-2"].stale_reason, "the expired reason must carry the heartbeat age"

    assert rows["impl-1"].status == "joined" and rows["impl-2"].status == "joined", (
        "staleness is reported, never applied: only an FR05 orchestrator revision may retire a member"
    )
    assert threading.active_count() == threads_before, "the roll-up must schedule no background task"

    # A live pin is not stale.
    write_pin(formation_env, "pin-live", formation_env.member_runs["impl-1"], age_hours=0.0)
    monkeypatch.setattr("trw_mcp.state._pin_ttl._pid_is_alive", lambda pid: False)
    fresh = status(run_path=formation_env.orchestrator_run)
    assert fresh is not None
    assert {row.member_id: row.stale for row in fresh.rows}["impl-1"] is False
