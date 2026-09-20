"""FR11 facade matrix for ``trw_mcp.comms.inbox(wait_seconds=...)``.

Calls the facade function directly (not through the MCP transport — that
boundary is ``test_inbox_contract.py``'s job) using the two-member
``SendScene`` from ``test_policy.py``. All identity switches go through
``scene.actor(member)`` SEQUENTIALLY, one call at a time, never while another
thread is mid-call: every mid-wait mutation below is either a raw SQL/config
write or a pre-resolved snapshot used with no env-var access at all, so no
thread ever races ``TRW_SESSION_ID`` against another thread's read of it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import trw_mcp.comms as comms
from tests._formation_test_support import formation_env  # noqa: F401

#: NOTE: ``resolve_project_root``/``resolve_trw_dir`` are imported inside each
#: test function that needs them, never at module scope: ``formation_env``
#: monkeypatches the attributes on ``trw_mcp.state._paths`` per-test, and a
#: module-level ``from ... import`` would bind the pre-patch originals.
from tests._layout import MONOREPO_ROOT, requires_monorepo
from tests.comms.conftest import (
    core,
    enable_comms,  # noqa: F401
)
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp import formation
from trw_mcp.comms._admission import admit
from trw_mcp.comms._endpoints import _reset_process_incarnations_for_test
from trw_mcp.comms._envelope import Envelope, canonical_bytes
from trw_mcp.comms._identity import resolve_snapshot
from trw_mcp.comms._policy import MAX_COUNTER, count_refusal
from trw_mcp.comms._store import connect, database_path, effective_time, immediate, touch_group_time
from trw_mcp.models.config import TRWConfig

_REPO_ROOT = MONOREPO_ROOT or Path(__file__).resolve().parents[3]
#: Pinned SHA256 of the pre-amendment (git HEAD) readers, so a future edit to
#: either file is caught here rather than silently validated against a moved
#: target. Recomputed with ``git show HEAD:<path> | shasum -a 256``.
#: Last commit whose comms schema is v3 (before PRD-CORE-274 Amendment 02). The v3
#: reader is loaded from here, not HEAD, so it stays the genuine pre-upgrade reader.
_V3_READER_REF = "016aed8dd"
_FROZEN_SCHEMA_SHA256 = "78d8421a48d529d1708fd7eecf36f1d16ccc116359d7bb6f0a2a54bb08b71e72"
_FROZEN_POLICY_SHA256 = "96cf21e3ecb9f4f920fe686959e262d29b2fbeace7bf2670b810699267a3d571"


def _inbox(scene: SendScene, member: str, **kwargs: Any) -> dict[str, Any]:
    scene.actor(member)
    return comms.inbox(ctx=None, **kwargs)


def _refusal_counts(scene: SendScene) -> dict[str, int]:
    return dict(scene.rows("SELECT reason,count FROM refusal_counts"))


def _mid_wait(scene: SendScene, mutate: Any, *, wait_seconds: int = 1, delay: float = 0.05) -> dict[str, Any]:
    """Run ``mutate`` in a background thread partway through a wait, then block on the wait."""

    def background() -> None:
        time.sleep(delay)
        mutate()

    thread = threading.Thread(target=background)
    thread.start()
    try:
        return _inbox(scene, "impl-2", wait_seconds=wait_seconds)
    finally:
        thread.join()


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 100}], indirect=True)
def test_zero_wait_is_byte_identical_to_omitting_wait_seconds(scene: SendScene) -> None:
    scene.send("k1")
    omitted = _inbox(scene, "impl-2", action="fetch")
    explicit = _inbox(scene, "impl-2", action="fetch", cursor=omitted["next_cursor"])
    zero = _inbox(scene, "impl-2", action="fetch", wait_seconds=0, cursor=omitted["next_cursor"])
    assert explicit == zero
    assert canonical_bytes(explicit) == canonical_bytes(zero)
    status_omitted = _inbox(scene, "impl-2", action="status")
    status_zero = _inbox(scene, "impl-2", action="status", wait_seconds=0)
    assert status_omitted == status_zero
    assert canonical_bytes(status_omitted) == canonical_bytes(status_zero)


@pytest.mark.parametrize("scene", [{"comms_wait_max_seconds": 0}], indirect=True)
def test_positive_wait_with_cap_zero_refuses_wait_disabled(scene: SendScene) -> None:
    result = core(_inbox(scene, "impl-2", wait_seconds=1))
    assert result == {
        "status": "refused",
        "reason": "wait_disabled",
        "detail": "fetch without wait_seconds",
        "delivery": "pull_only",
    }
    assert _refusal_counts(scene) == {"invalid_inbox_arguments": 1}


@pytest.mark.parametrize("wait_seconds", [-1, 31, 10**1000], ids=["negative", "over_cap", "oversized"])
def test_wait_seconds_outside_bounds_refuses_invalid_wait_seconds_with_no_overflow(
    scene: SendScene, wait_seconds: int
) -> None:
    result = _inbox(scene, "impl-2", wait_seconds=wait_seconds)
    assert result["status"] == "refused"
    assert result["reason"] == "invalid_wait_seconds"


@pytest.mark.parametrize(
    "kwargs", [{"action": "status"}, {"message_ids": ["0" * 32]}, {"cursor": "x"}], ids=["status", "ids", "cursor"]
)
def test_positive_wait_with_wrong_combination_refuses_wait_requires_fresh_fetch(
    scene: SendScene, kwargs: dict[str, Any]
) -> None:
    result = _inbox(scene, "impl-2", wait_seconds=1, **kwargs)
    assert result["reason"] == "wait_requires_fresh_fetch"


def test_zero_wait_status_action_is_unaffected(scene: SendScene) -> None:
    scene.send("k")
    result = _inbox(scene, "impl-2", action="status", wait_seconds=0)
    assert result["status"] == "ok" and len(result["items"]) == 1


@pytest.mark.parametrize("wait_seconds", [0, 1, -1, 31, 10**1000])
def test_all_terminal_group_records_closure_before_any_wait_input_is_evaluated(
    scene: SendScene, wait_seconds: int
) -> None:
    scene.send("pending")
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-1": {"status": "abandoned"}, "impl-2": {"status": "abandoned"}},
        trw_dir=scene.formation.trw_dir,
    )
    result = _inbox(scene, "impl-2", wait_seconds=wait_seconds)
    assert result["status"] == "refused" and result["reason"] == "group_closed"
    assert scene.rows("SELECT closed FROM groups") == [(1,)]


def _owner_change_probe(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch, *, delay_first_attempt: float = 0.0
) -> dict[str, Any]:
    """Deterministically replace impl-2's incarnation strictly AFTER the first
    ``_inbox_attempt`` call has captured the owner tuple, by wrapping the module
    function itself rather than racing a background thread against real time
    (root cause of a prior flake, reviewer board-seq-186..191: a 20ms sleep
    thread could occasionally beat the synchronous first attempt under load).
    The wrapper runs entirely within the calling thread's own call stack, so
    there is no concurrency and no timing window to lose.
    """
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    scene.actor("impl-1")
    sender_snapshot = resolve_snapshot(None, trw_dir=resolve_trw_dir(), project_root=resolve_project_root())
    scene.actor("impl-2")

    import trw_mcp.comms as comms_module

    original_attempt = comms_module._inbox_attempt
    calls = {"count": 0}

    def wrapped(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        calls["count"] += 1
        is_first = calls["count"] == 1
        if is_first and delay_first_attempt:
            # Reproducer (lead board-seq-191): an artificially slow first
            # attempt must not change the outcome — the replacement below
            # still runs strictly after THIS call returns.
            time.sleep(delay_first_attempt)
        result = original_attempt(*args, **kwargs)
        if is_first:
            soon = time.time() + 0.02
            scene.rows("UPDATE endpoints SET lease_expires_at=? WHERE member_id='impl-2'", (soon,))
            time.sleep(0.03)  # let the persisted expiry actually lapse before replacing
            _reset_process_incarnations_for_test()
            scene.actor("impl-2")
            assert comms_module.peers("enroll", ctx=None)["status"] == "ok"
            manifest_path = scene.formation.manifest_path()
            with (
                connect(manifest_path, busy_timeout_ms=scene.config.comms_sqlite_busy_timeout_ms) as conn,
                immediate(conn),
            ):
                now = effective_time(conn, sender_snapshot.binding.group_id)
                touch_group_time(conn, sender_snapshot.binding.group_id, now)
                envelope = Envelope("impl-2", "after-replace", "should-not-be-seen", "request", "on_demand")
                admit(conn, sender_snapshot, envelope, now, ttl_seconds=86400)
        return result

    monkeypatch.setattr(comms_module, "_inbox_attempt", wrapped)
    return _inbox(scene, "impl-2", wait_seconds=1)


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 300}], indirect=True)
def test_owner_change_mid_wait_refuses_before_any_fetch_prepared_milestone(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Incarnation replacement (a realistic owner change) mid-wait must trip wait_owner_changed.

    Uses lease-expiry + re-enrollment for the SAME session/member (``impl-2``
    throughout — no identity swap) so the endpoint fencing check does not fire
    first; only the wait's own owner-tuple comparison distinguishes this from
    an ordinary EndpointError refusal. A message is admitted (from a snapshot
    pre-resolved BEFORE the wait starts, so no thread touches an env var)
    right after the replacement, so a naive implementation would return it.
    """
    result = _owner_change_probe(scene, monkeypatch)
    assert result["status"] == "refused" and result["reason"] == "wait_owner_changed"
    assert scene.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(0,)]
    assert scene.rows("SELECT state FROM admissions") == [("pending",)]


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 300}], indirect=True)
def test_owner_change_still_detected_when_the_first_attempt_is_artificially_slow(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression reproducer (lead board-seq-191): a slow first attempt must not
    change the outcome, since the replacement always runs strictly after it.
    """
    result = _owner_change_probe(scene, monkeypatch, delay_first_attempt=0.25)
    assert result["status"] == "refused" and result["reason"] == "wait_owner_changed"


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 100}], indirect=True)
def test_lease_expiry_mid_wait_neither_refuses_nor_is_renewed_by_retries(scene: SendScene) -> None:
    """PRD-CORE-274 FR12 supersedes the FR07 lease gate: an expired lease is advisory and
    refuses nothing. FR11 still holds: retries inside the wait never renew the lease, so
    the lapsed value the retries observed is exactly what remains."""
    expiry: dict[str, float] = {}

    def expire_lease() -> None:
        # Ahead of "now" at write time (so the persisted clock ordering check
        # accepts it) but well short of the wait's next attempt, so it has
        # genuinely lapsed by the time that attempt reads it.
        soon = time.time() + 0.02
        expiry["value"] = soon
        scene.rows("UPDATE endpoints SET lease_expires_at=? WHERE member_id='impl-2'", (soon,))

    result = _mid_wait(scene, expire_lease)
    assert result["status"] == "ok" and result["items"] == [], result
    assert scene.rows("SELECT lease_expires_at FROM endpoints WHERE member_id='impl-2'") == [(expiry["value"],)]


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 100}], indirect=True)
def test_closure_mid_wait_refuses_as_a_plain_fetch_would(scene: SendScene) -> None:
    def close_group() -> None:
        formation.revise(
            "release-train",
            scene.formation.orchestrator_run,
            {"impl-1": {"status": "abandoned"}, "impl-2": {"status": "abandoned"}},
            trw_dir=scene.formation.trw_dir,
        )

    result = _mid_wait(scene, close_group)
    assert result["status"] == "refused" and result["reason"] == "group_closed"
    assert scene.rows("SELECT closed FROM groups") == [(1,)]


@pytest.mark.parametrize(
    ("attr", "value", "reason"),
    [("comms_enabled", False, "comms_disabled"), ("comms_wait_max_seconds", 0, "wait_disabled")],
)
@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 100}], indirect=True)
@pytest.mark.parametrize("cached_provider", [False, True], ids=["reload", "cached-provider-control"])
def test_reload_config_mid_wait_refuses_as_a_plain_call_would_at_that_moment(
    scene: SendScene, attr: str, value: object, reason: str, cached_provider: bool
) -> None:
    from trw_mcp.comms._wait import run_bounded_wait
    from trw_mcp.models import config as config_module

    original: TRWConfig = scene.config
    before = original.model_dump()
    replacement = TRWConfig.model_validate({**before, attr: value})
    assert replacement is not original and getattr(original, attr) != getattr(replacement, attr)
    current = original
    attempts: list[tuple[dict[str, Any], bool]] = []
    real_attempt = comms._inbox_attempt

    def provider() -> TRWConfig:
        # Negative control: publishing a new object cannot update a cached one.
        return original if cached_provider else current

    def attempt(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], bool]:
        nonlocal current
        result = real_attempt(*args, **kwargs)
        attempts.append(result)
        if len(attempts) == 1:
            assert result[1] and result[0]["status"] == "ok" and result[0]["items"] == []
            assert args[5].get("tuple"), "first real attempt did not capture its owner"
            current = replacement  # Strictly after the real first empty attempt committed.
        return result

    def deterministic_loop(retry: Any, **kwargs: Any) -> dict[str, Any]:
        # Keep the REAL loop/attempts; local virtual sleep avoids timing guesses.
        now = float(kwargs["deadline"]) - 1.0

        def sleep(seconds: float) -> None:
            nonlocal now
            now += seconds

        return run_bounded_wait(retry, **kwargs, clock=lambda: now, sleep=sleep)

    scene.monkeypatch.setattr(config_module, "get_config", provider)
    scene.monkeypatch.setattr(comms, "_inbox_attempt", attempt)
    scene.monkeypatch.setattr(comms, "run_bounded_wait", deterministic_loop)
    result = _inbox(scene, "impl-2", wait_seconds=1)
    assert current is replacement and scene.config is original
    assert original.model_dump() == before, "reload mutated the cached object in place"
    if cached_provider:
        assert len(attempts) > 1
        assert all(page["status"] == "ok" and page["items"] == [] and retry for page, retry in attempts)
        assert result["status"] == "ok" and result["items"] == []
        assert _refusal_counts(scene) == {}
    else:
        assert len(attempts) == 2, "the next ordinary attempt must observe the replacement"
        if reason == "comms_disabled":
            assert core(result) == {"status": "disabled", "reason": "comms_disabled", "delivery": "pull_only"}
            assert _refusal_counts(scene) == {}
        else:
            assert result["status"] == "refused" and result["reason"] == reason
            assert _refusal_counts(scene) == {"invalid_inbox_arguments": 1}


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 100}], indirect=True)
def test_empty_attempts_leave_admissions_milestones_endpoints_and_lease_unchanged(scene: SendScene) -> None:
    before = (
        scene.rows("SELECT * FROM admissions"),
        scene.rows("SELECT * FROM milestones"),
        # The ordinary first attempt renews the lease (FR12); retries never do (proved by
        # test_lease_expiry_mid_wait_neither_refuses_nor_is_renewed_by_retries).
        scene.rows("SELECT group_id,member_id,incarnation FROM endpoints"),
    )
    result = _inbox(scene, "impl-2", wait_seconds=1)
    assert result["status"] == "ok" and result["items"] == []
    after = (
        scene.rows("SELECT * FROM admissions"),
        scene.rows("SELECT * FROM milestones"),
        scene.rows("SELECT group_id,member_id,incarnation FROM endpoints"),
    )
    assert before == after


@pytest.mark.parametrize("scene", [{"comms_wait_interval_ms": 100}], indirect=True)
def test_a_message_admitted_during_the_wait_is_returned_before_the_deadline(scene: SendScene) -> None:
    """Positive control for the empty-attempt test: a real arrival IS observed and DOES mutate state.

    The sender's snapshot is resolved once, sequentially, before the wait
    starts; the background thread then admits directly against that already-
    resolved snapshot and its own connection, touching no env var at all.
    """
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    scene.actor("impl-1")
    sender_snapshot = resolve_snapshot(None, trw_dir=resolve_trw_dir(), project_root=resolve_project_root())
    scene.actor("impl-2")

    def admit_from_sender() -> None:
        manifest_path = scene.formation.manifest_path()
        with connect(manifest_path, busy_timeout_ms=scene.config.comms_sqlite_busy_timeout_ms) as conn, immediate(conn):
            now = effective_time(conn, sender_snapshot.binding.group_id)
            touch_group_time(conn, sender_snapshot.binding.group_id, now)
            envelope = Envelope("impl-2", "mid-wait", "arrived", "request", "on_demand")
            admit(conn, sender_snapshot, envelope, now, ttl_seconds=86400)

    result = _mid_wait(scene, admit_from_sender)
    assert result["status"] == "ok"
    assert [item["body"] for item in result["items"]] == ["arrived"]
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]


def test_legacy_poll_15_lease_30_config_loads_with_the_new_wait_defaults() -> None:
    config = TRWConfig.model_validate({"comms_poll_interval_seconds": 15, "comms_lease_ttl_seconds": 30})
    assert config.comms_wait_max_seconds == 30
    assert config.comms_wait_interval_ms == 1000


@pytest.mark.parametrize("reason", ["wait_disabled", "invalid_wait_seconds", "wait_requires_fresh_fetch"])
def test_fr11_reasons_are_counted_only_under_the_legacy_bucket(scene: SendScene, reason: str) -> None:
    scene.config.comms_wait_max_seconds = 0 if reason == "wait_disabled" else scene.config.comms_wait_max_seconds
    wait_seconds = {"wait_disabled": 1, "invalid_wait_seconds": -1, "wait_requires_fresh_fetch": 1}[reason]
    kwargs = {"cursor": "x"} if reason == "wait_requires_fresh_fetch" else {}
    result = _inbox(scene, "impl-2", wait_seconds=wait_seconds, **kwargs)
    assert result["reason"] == reason
    counts = _refusal_counts(scene)
    assert counts == {"invalid_inbox_arguments": 1}
    assert reason not in counts


def test_repeated_fr11_refusals_aggregate_in_the_legacy_bucket_and_saturate(scene: SendScene) -> None:
    scene.config.comms_wait_max_seconds = 0
    assert _inbox(scene, "impl-2", wait_seconds=1)["reason"] == "wait_disabled"
    assert _refusal_counts(scene) == {"invalid_inbox_arguments": 1}
    scene.rows("UPDATE refusal_counts SET count=?", (MAX_COUNTER,))
    for _ in range(3):
        assert _inbox(scene, "impl-2", wait_seconds=1)["reason"] == "wait_disabled"
    assert _refusal_counts(scene) == {"invalid_inbox_arguments": MAX_COUNTER}


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"action": "ack", "message_ids": []}, "invalid_ack_ids"),
        ({"action": "ack", "cursor": "x"}, "invalid_inbox_arguments"),
    ],
)
def test_pre_existing_refusal_accounting_is_unchanged_by_the_bucket_mapping(
    scene: SendScene, kwargs: dict[str, Any], reason: str
) -> None:
    result = _inbox(scene, "impl-2", **kwargs)
    assert result["reason"] == reason
    assert _refusal_counts(scene) == {reason: 1}


def test_count_refusal_fails_closed_for_an_unmapped_reason_with_no_row_written(scene: SendScene) -> None:
    manifest_path = scene.formation.manifest_path()
    with connect(manifest_path, busy_timeout_ms=scene.config.comms_sqlite_busy_timeout_ms) as conn, immediate(conn):
        with pytest.raises(ValueError, match="refusal category not admitted"):
            count_refusal(conn, "any-group", "not_a_real_reason")
    assert scene.rows("SELECT COUNT(*) FROM refusal_counts") == [(0,)]


def _load_frozen_module(relative_path: str, expected_sha256: str) -> Any:
    blob = subprocess.run(
        ["git", "show", f"{_V3_READER_REF}:{relative_path}"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    assert digest == expected_sha256, f"{relative_path} at {_V3_READER_REF} drifted from the pinned frozen baseline"
    spec = importlib.util.spec_from_loader(f"frozen_{Path(relative_path).stem}", loader=None)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # Registered in sys.modules under its own name BEFORE exec: the frozen
    # ``_policy.py`` defines a ``@dataclass`` with string annotations (PEP 563),
    # whose field-type resolution looks the defining module up by name in
    # ``sys.modules`` — an unregistered throwaway module crashes that lookup.
    import sys

    sys.modules[spec.name] = module
    exec(compile(blob, f"<git HEAD {relative_path}>", "exec"), module.__dict__)
    return module


@requires_monorepo
def test_v3_reader_refuses_a_v4_mailbox_explicitly_and_changes_nothing(scene: SendScene) -> None:
    """PRD-CORE-274 FR16 (Amendment 02) supersedes the FR11-era guarantee that a
    pre-amendment reader accepts a mailbox the new code produced. A v3 reader, loaded
    from the last v3 commit, must now REFUSE a fresh v4 mailbox with an explicit schema
    version error, and the refusal must leave every row intact (no silent migration).
    """
    scene.send("kept", "kept")
    assert _inbox(scene, "impl-2", action="fetch")["items"][0]["body"] == "kept"
    assert _inbox(scene, "impl-2", wait_seconds=-1)["reason"] == "invalid_wait_seconds"
    scene.config.comms_wait_max_seconds = 0
    assert _inbox(scene, "impl-2", wait_seconds=1)["reason"] == "wait_disabled"

    frozen_schema = _load_frozen_module("trw-mcp/src/trw_mcp/comms/_schema.py", _FROZEN_SCHEMA_SHA256)
    _load_frozen_module("trw-mcp/src/trw_mcp/comms/_policy.py", _FROZEN_POLICY_SHA256)  # imported for its hash pin

    import sqlite3

    conn = sqlite3.connect(database_path(scene.formation.manifest_path()))
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(frozen_schema.SchemaVersionError):
            frozen_schema.verify(conn)
        assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == "4"
        assert conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM refusal_counts").fetchone()[0] >= 1
    finally:
        conn.close()
