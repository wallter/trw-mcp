"""Tests for meta_tune._correlator.

Uses synthetic JSONL data; no live MCP, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels._manifest_models import CLIENT_CORRECTION_FACTORS
from trw_mcp.channels.meta_tune._correlator import (
    adjusted_rate,
    correlate,
    load_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    log_path = tmp_path / "channel-events.jsonl"
    with log_path.open("w") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")
    return log_path


def _push_event(
    channel_id: str = "cc-01",
    client: str = "claude-code",
    session_id: str = "sess-1",
    file_path: str = "CLAUDE.md",
    ts: str = "2026-05-28T10:00:00.000Z",
    event_type: str = "push_write",
) -> dict:
    return {
        "schema_version": "channel-event/v1",
        "channel_id": channel_id,
        "client": client,
        "event_type": event_type,
        "ts": ts,
        "session_id": session_id,
        "file_path": file_path,
    }


def _outcome_event(
    channel_id: str = "cc-01",
    client: str = "claude-code",
    session_id: str = "sess-1",
    file_path: str = "CLAUDE.md",
    ts: str = "2026-05-28T10:00:30.000Z",
    event_type: str = "edit_correlated",
) -> dict:
    return {
        "schema_version": "channel-event/v1",
        "channel_id": channel_id,
        "client": client,
        "event_type": event_type,
        "ts": ts,
        "session_id": session_id,
        "file_path": file_path,
    }


# ---------------------------------------------------------------------------
# load_events
# ---------------------------------------------------------------------------


def test_load_events_empty_file(tmp_path: Path) -> None:
    log = tmp_path / "empty.jsonl"
    log.write_text("")
    assert load_events(log) == []


def test_load_events_missing_file(tmp_path: Path) -> None:
    assert load_events(tmp_path / "nope.jsonl") == []


def test_load_events_skips_malformed(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text('{"valid": 1}\nnot-json\n{"valid": 2}\n')
    result = load_events(log)
    assert len(result) == 2


def test_load_events_skips_blank_lines(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    log.write_text('{"a": 1}\n\n{"b": 2}\n')
    result = load_events(log)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# correlate — basic
# ---------------------------------------------------------------------------


def test_correlate_push_followed_by_outcome_within_window(tmp_path: Path) -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:30.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert len(results) == 1
    r = results[0]
    assert r.channel_id == "cc-01"
    assert r.total_pushes == 1
    assert r.correlated == 1
    assert r.raw_rate == pytest.approx(1.0)


def test_correlate_no_outcome_events_at_all_is_unmeasured_not_zero(
    tmp_path: Path,
) -> None:
    """A log with no outcome events yields no rate, rather than a rate of zero.

    This test previously asserted ``raw_rate == 0.0`` here, which encoded the
    defect: it made "we cannot measure this" indistinguishable from "we
    measured, and the answer is none". Nothing in the codebase has ever emitted
    any member of ``OUTCOME_EVENT_TYPES``, so this branch was not an edge case —
    it was every call, in every project, and the 0.0 it produced was surfaced
    through ``trw_channel_stats`` and ``channel-doctor stats`` as "0.0%".
    """
    events = [_push_event()]
    results = correlate(events, window_seconds=3600)
    assert len(results) == 1
    assert results[0].correlated == 0
    assert results[0].outcome_unmeasured is True
    assert results[0].raw_rate is None
    assert results[0].adj_rate is None


def test_a_genuine_zero_is_still_reported_as_zero() -> None:
    """The discrimination, not just the new default.

    When outcome events DO exist and simply do not correlate, that is a real
    measurement of zero and must be reported as ``0.0`` — otherwise the fix
    would have swapped one blanket answer for another and destroyed the
    signal it was meant to protect.
    """
    events = [
        _push_event(session_id="sess-1", file_path="CLAUDE.md"),
        # Same session — so outcome instrumentation demonstrably ran here — but
        # a different file, so it cannot join to the push above. That makes the
        # non-correlation a real observation rather than an absence of one.
        #
        # An earlier version of this test used a different *session*, which the
        # per-channel measurability rule now (correctly) classifies as
        # unmeasured: an outcome in another session says nothing about whether
        # this one was being watched.
        _outcome_event(session_id="sess-1", file_path="other.md"),
    ]

    results = correlate(events, window_seconds=3600)

    pushes = [r for r in results if r.total_pushes > 0]
    assert len(pushes) == 1
    assert pushes[0].correlated == 0
    assert pushes[0].outcome_unmeasured is False, "outcome events were present, so the rate WAS measurable"
    assert pushes[0].raw_rate == pytest.approx(0.0)


def test_correlate_outcome_outside_window_uncorrelated() -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T12:00:01.000Z"),  # 7201s later > 3600s
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_outcome_before_push_uncorrelated() -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:30.000Z"),
        _outcome_event(ts="2026-05-28T10:00:00.000Z"),  # earlier than push
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_different_session_no_correlation() -> None:
    events = [
        _push_event(session_id="sess-A", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(session_id="sess-B", ts="2026-05-28T10:00:10.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_different_file_no_correlation() -> None:
    events = [
        _push_event(file_path="CLAUDE.md", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(file_path="other.md", ts="2026-05-28T10:00:10.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 0


def test_correlate_multiple_pushes_partial_correlation() -> None:
    events = [
        _push_event(session_id="s1", ts="2026-05-28T10:00:00.000Z"),
        _push_event(session_id="s2", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(session_id="s1", ts="2026-05-28T10:00:10.000Z"),
        # s2 has no outcome
    ]
    results = correlate(events, window_seconds=3600)
    assert len(results) == 1
    assert results[0].total_pushes == 2
    assert results[0].correlated == 1
    assert results[0].raw_rate == pytest.approx(0.5)


def test_correlate_per_channel_grouping() -> None:
    # JOIN_KEY_FIELDS = (session_id, file_path) — channel_id is NOT a join key.
    # Both ch-A and ch-B pushes share session_id=s1 + file_path=CLAUDE.md,
    # so a single outcome for that key correlates BOTH pushes.
    # Use distinct sessions to isolate per-channel correlation.
    events = [
        _push_event(channel_id="ch-A", session_id="s-A", ts="2026-05-28T10:00:00.000Z"),
        _push_event(channel_id="ch-B", session_id="s-B", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(channel_id="ch-A", session_id="s-A", ts="2026-05-28T10:00:05.000Z"),
        # ch-B / s-B has no outcome
    ]
    results = correlate(events, window_seconds=3600)
    by_channel = {r.channel_id: r for r in results}
    assert by_channel["ch-A"].correlated == 1
    assert by_channel["ch-B"].correlated == 0


def test_correlate_empty_events() -> None:
    assert correlate([]) == []


def test_correlate_only_outcomes_no_pushes() -> None:
    events = [_outcome_event()]
    assert correlate(events) == []


# ---------------------------------------------------------------------------
# adjusted_rate
# ---------------------------------------------------------------------------


def test_adjusted_rate_applies_correction_factor() -> None:
    # claude-code factor = 0.85 → raw=0.85 → adj=1.0
    adj = adjusted_rate(0.85, "claude-code")
    assert adj == pytest.approx(1.0)


def test_adjusted_rate_capped_at_one() -> None:
    adj = adjusted_rate(1.0, "claude-code")
    assert adj <= 1.0


def test_adjusted_rate_zero_raw() -> None:
    adj = adjusted_rate(0.0, "claude-code")
    assert adj == pytest.approx(0.0)


def test_adjusted_rate_unknown_client_no_adjustment() -> None:
    raw = 0.5
    adj = adjusted_rate(raw, "unknown-client")
    assert adj == pytest.approx(raw)


def test_adjusted_rate_copilot_factor() -> None:
    factor = CLIENT_CORRECTION_FACTORS["copilot"]  # 0.50
    raw = 0.30
    adj = adjusted_rate(raw, "copilot")
    assert adj == pytest.approx(min(raw / factor, 1.0))


def test_adjusted_rate_all_known_clients() -> None:
    for client, factor in CLIENT_CORRECTION_FACTORS.items():
        raw = 0.4
        adj = adjusted_rate(raw, client)
        expected = min(raw / factor, 1.0)
        assert adj == pytest.approx(expected), f"client={client}"


# ---------------------------------------------------------------------------
# correlate via file (integration with load_events)
# ---------------------------------------------------------------------------


def test_correlate_from_file(tmp_path: Path) -> None:
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:05.000Z"),
    ]
    log_path = _write_events(tmp_path, events)
    loaded = load_events(log_path)
    results = correlate(loaded, window_seconds=3600)
    assert results[0].correlated == 1


def test_correlate_at_window_boundary_inclusive() -> None:
    """Outcome exactly at push_ts + window should be correlated."""
    events = [
        _push_event(ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T11:00:00.000Z"),  # exactly 3600s
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 1


def test_correlate_push_ephemeral_event_type() -> None:
    events = [
        _push_event(event_type="push_ephemeral", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:05.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 1


def test_correlate_pull_tool_call_event_type() -> None:
    events = [
        _push_event(event_type="pull_tool_call", ts="2026-05-28T10:00:00.000Z"),
        _outcome_event(ts="2026-05-28T10:00:05.000Z"),
    ]
    results = correlate(events, window_seconds=3600)
    assert results[0].correlated == 1


def test_one_channels_outcomes_do_not_mark_another_channel_measured() -> None:
    """Measurability is per (channel, client), never global over the log.

    A first version of this fix computed `unmeasured = not parsed_outcomes`
    across the whole file, so a single outcome event anywhere handed every
    OTHER channel back the fabricated 0.0 — and with n past `min_n` that is a
    real tier demotion. A mixed log was enough to re-create the exact defect
    the flag exists to prevent. Found in review, not by the original tests.
    """
    events = [
        # cursor pushes in a session where nothing ever observes outcomes.
        *[
            _push_event(
                channel_id="cur-01",
                client="cursor",
                session_id="sess-cursor",
                ts="2026-05-28T10:00:00.000Z",
            )
            for _ in range(3)
        ],
        # An unrelated claude-code session DOES record an outcome.
        _push_event(channel_id="cc-01", client="claude-code", session_id="sess-cc"),
        _outcome_event(channel_id="cc-01", client="claude-code", session_id="sess-cc"),
    ]

    results = correlate(events, window_seconds=3600)
    by_key = {(r.channel_id, r.client): r for r in results}

    cursor = by_key[("cur-01", "cursor")]
    assert cursor.outcome_unmeasured is True, (
        "no outcome was ever recorded in this channel's session, so its rate "
        "is unmeasured — another client's outcome must not vouch for it"
    )
    assert cursor.raw_rate is None

    claude = by_key[("cc-01", "claude-code")]
    assert claude.outcome_unmeasured is False
    assert claude.raw_rate == pytest.approx(1.0)
