"""PRD-CORE-146 FR03: trw-mcp schema-parse contract test.

Pins the shape of the nudge event contract surface consumed by downstream
eval consumers:
- ceremony-state.json persisted fields
- surface_tracking.jsonl line shape
- nudge_shown JSONL event shape
- nudge-analysis.json artifact schema versioning (PRD-QUAL-127 NFR04)

Dual-enforcement: a downstream eval consumer asserts the same shapes so
breakage is caught from whichever side changes first. See PRD-CORE-146 NFR03.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "nudge_contract"

_NUDGE_HISTORY_CAP = 100  # mirror of trw_mcp.state._ceremony_progress_state._NUDGE_HISTORY_CAP


def _load_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as handle:
        loaded: object = json.load(handle)
    assert isinstance(loaded, dict)
    return loaded


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        obj: object = json.loads(line)
        assert isinstance(obj, dict)
        rows.append(obj)
    return rows


def test_ceremony_state_fixture_parses_with_pinned_fields() -> None:
    """PRD-CORE-146 NFR03: ceremony-state.json keys MUST NOT be renamed.

    Pins: nudge_counts, nudge_history, pool_nudge_counts, pool_cooldown_until,
    session_started, deliver_called, tool_call_counter. The nudge_history
    entry shape pins turn_first_shown, last_shown_turn, phases_shown.
    """
    state = _load_json(_FIXTURE_DIR / "ceremony-state.example.json")
    required = {
        "nudge_counts",
        "nudge_history",
        "pool_nudge_counts",
        "pool_cooldown_until",
        "session_started",
        "deliver_called",
        "tool_call_counter",
    }
    missing = required - set(state.keys())
    assert not missing, f"missing pinned ceremony-state fields: {missing}"

    history = state["nudge_history"]
    assert isinstance(history, dict) and history, "nudge_history must be non-empty"
    for entry_id, entry in history.items():
        assert isinstance(entry, dict), f"{entry_id} entry is not a dict"
        for field in ("turn_first_shown", "last_shown_turn", "phases_shown"):
            assert field in entry, f"nudge_history[{entry_id}] missing {field}"


def test_surface_tracking_jsonl_shape() -> None:
    """PRD-CORE-146 NFR03: surface_tracking.jsonl line shape."""
    rows = _load_jsonl(_FIXTURE_DIR / "surface_tracking.example.jsonl")
    assert rows, "fixture must have at least one line"
    for row in rows:
        assert row.get("surface_type") in {"nudge", "recall"}, row
        learning_id = row.get("learning_id")
        assert isinstance(learning_id, str) and learning_id, row
        ts = row.get("ts")
        assert isinstance(ts, str) and ts, row


def test_nudge_shown_jsonl_shape() -> None:
    """PRD-CORE-146 NFR03: nudge_shown event shape consumed by trw-eval."""
    rows = _load_jsonl(_FIXTURE_DIR / "nudge_shown.example.jsonl")
    assert rows, "fixture must have at least one line"
    for row in rows:
        assert row.get("event") == "nudge_shown", row
        step = row.get("step")
        assert isinstance(step, str) and step, row
        learning_ids = row.get("learning_ids")
        assert isinstance(learning_ids, list) and learning_ids, row
        assert all(isinstance(lid, str) for lid in learning_ids), row


@pytest.mark.parametrize(
    "fixture_name",
    [
        "ceremony-state.example.json",
        "ceremony-state.empty.json",
        "ceremony-state.high_turn.json",
        "ceremony-state.pool_cooldown_active.json",
    ],
)
def test_ceremony_state_variants_parse_with_pinned_fields(fixture_name: str) -> None:
    """PRD-CORE-146 follow-up: edge-case ceremony-state variants must also honor the pinned schema.

    Variants exercised:
    - empty: fresh install, zero activity
    - high_turn: tool_call_counter=10000 + 55 history entries (below _NUDGE_HISTORY_CAP=100)
    - pool_cooldown_active: two pools with active cooldowns + ignore counts
    """
    state = _load_json(_FIXTURE_DIR / fixture_name)
    required = {
        "nudge_counts",
        "nudge_history",
        "pool_nudge_counts",
        "pool_cooldown_until",
        "session_started",
        "deliver_called",
        "tool_call_counter",
    }
    missing = required - set(state.keys())
    assert not missing, f"{fixture_name}: missing pinned fields {missing}"

    history = state["nudge_history"]
    assert isinstance(history, dict), f"{fixture_name}: nudge_history must be dict"

    # Variant-specific invariants
    if fixture_name == "ceremony-state.empty.json":
        assert history == {}, "empty variant must have no history entries"
        assert state["tool_call_counter"] == 0
        assert state["checkpoint_count"] == 0
    elif fixture_name == "ceremony-state.high_turn.json":
        assert state["tool_call_counter"] == 10000
        assert 1 <= len(history) <= _NUDGE_HISTORY_CAP, (
            f"high_turn variant has {len(history)} entries; must be <= _NUDGE_HISTORY_CAP={_NUDGE_HISTORY_CAP}"
        )
        # All entries must respect the inner shape
        for entry_id, entry in history.items():
            assert isinstance(entry, dict), entry_id
            for field in ("turn_first_shown", "last_shown_turn", "phases_shown"):
                assert field in entry
            assert entry["last_shown_turn"] >= entry["turn_first_shown"]
    elif fixture_name == "ceremony-state.pool_cooldown_active.json":
        cooldowns = state["pool_cooldown_until"]
        assert isinstance(cooldowns, dict) and cooldowns, "must have active cooldowns"
        assert "ceremony" in cooldowns and cooldowns["ceremony"] > state["tool_call_counter"]
        ignores = state["pool_ignore_counts"]
        assert isinstance(ignores, dict)
        assert ignores.get("ceremony", 0) > 0


@pytest.mark.parametrize(
    "fixture_name",
    [
        "surface_tracking.example.jsonl",
        "surface_tracking.empty.jsonl",
    ],
)
def test_surface_tracking_variants(fixture_name: str) -> None:
    """PRD-CORE-146 follow-up: surface_tracking variants — including empty."""
    rows = _load_jsonl(_FIXTURE_DIR / fixture_name)
    if fixture_name == "surface_tracking.empty.jsonl":
        assert rows == [], "empty variant must produce zero rows"
        return
    assert rows
    for row in rows:
        assert row.get("surface_type") in {"nudge", "recall"}


@pytest.mark.parametrize(
    "fixture_name",
    [
        "nudge_shown.example.jsonl",
        "nudge_shown.with_recall_correlation.jsonl",
    ],
)
def test_nudge_shown_variants(fixture_name: str) -> None:
    """PRD-CORE-146 follow-up: nudge_shown variants.

    with_recall_correlation includes a recall_issued event between two nudge_shown
    events referencing the same learning_id — exercises recall_pull_rate scoring.
    """
    rows = _load_jsonl(_FIXTURE_DIR / fixture_name)
    assert rows
    nudge_rows = [r for r in rows if r.get("event") == "nudge_shown"]
    assert nudge_rows, f"{fixture_name}: expected at least one nudge_shown event"
    for row in nudge_rows:
        step = row.get("step")
        assert isinstance(step, str) and step
        learning_ids = row.get("learning_ids")
        assert isinstance(learning_ids, list) and learning_ids
        assert all(isinstance(lid, str) for lid in learning_ids)

    if fixture_name == "nudge_shown.with_recall_correlation.jsonl":
        # A learning_id should appear in BOTH a nudge_shown and a recall_issued row
        nudged_ids: set[str] = set()
        for r in nudge_rows:
            ids = r.get("learning_ids")
            if isinstance(ids, list):
                nudged_ids.update(lid for lid in ids if isinstance(lid, str))
        recalled_ids: set[str] = set()
        for r in rows:
            if r.get("event") == "recall_issued":
                ids = r.get("learning_ids")
                if isinstance(ids, list):
                    recalled_ids.update(lid for lid in ids if isinstance(lid, str))
        assert nudged_ids & recalled_ids, (
            "with_recall_correlation fixture must share at least one learning_id "
            "between nudge_shown and recall_issued events"
        )


def test_live_record_nudge_shown_emits_canonical_event(tmp_path: Path) -> None:
    """PRD-CORE-146 FR03: live emitter matches the pinned contract.

    ``record_nudge_shown`` must append a session-events.jsonl entry whose
    shape matches the fixture — top-level ``event``, ``step``, ``learning_ids``
    (consumed by trw-eval scoring/analysis/nudge/pre_analyzers.py).
    """
    from trw_mcp.state._ceremony_progress_state import record_nudge_shown

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)

    record_nudge_shown(trw_dir, "L-test-001", "validate", turn=5)

    events_path = trw_dir / "context" / "session-events.jsonl"
    assert events_path.exists(), "record_nudge_shown must create session-events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert lines, "expected at least one event"

    event = json.loads(lines[-1])
    assert event["event"] == "nudge_shown"
    assert event["step"] == "validate"
    assert event["learning_ids"] == ["L-test-001"]
    # Legacy fields preserved for backward compat with existing downstream
    # eval consumers of the data.* block (NFR03 — no public rename).
    assert event["learning_id"] == "L-test-001"
    assert event["phase"] == "validate"


# ===========================================================================
# nudge-analysis.json artifact versioning (PRD-QUAL-127 FR05 / NFR04)
# ===========================================================================


def _v1_artifact() -> dict[str, object]:
    """The frozen schema-v1 ``.trw/context/nudge-analysis.json`` shape."""
    return _load_json(_FIXTURE_DIR / "nudge-analysis.v1.json")


def _v1_style_total_nudges(artifact: dict[str, object]) -> int:
    """What a v1 consumer computes when handed ``artifact``.

    Deliberately written the way a v1 reader was written: ``.get`` with a
    default, unknown keys ignored, ``total_nudges`` trusted as total nudge
    *volume*. Used to demonstrate what silently changed.
    """
    raw = artifact.get("total_nudges", 0)
    return int(raw) if isinstance(raw, (int, float)) else 0


def test_nudge_analysis_v1_fixture_pins_pre_narrowing_semantics() -> None:
    """v1 held the identity ``total_nudges == sum(nudge_counts_by_step)``.

    Captured from this repo on 2026-07-24 (PRD-QUAL-127 §Evidence). That
    identity is what a v1 consumer relies on to read total nudge volume out of
    a single field; schema v2 breaks it, which is why the version had to move.
    """
    v1 = _v1_artifact()
    assert v1["schema_version"] == 1
    counts = v1["nudge_counts_by_step"]
    assert isinstance(counts, dict) and counts
    assert _v1_style_total_nudges(v1) == sum(counts.values()) == 3041
    assert "total_emissions" not in v1, "v1 predates the per-pool emission ledger"
    assert "emissions_by_pool" not in v1


def test_nudge_analysis_schema_version_is_2() -> None:
    """FR05: the emitted artifact declares v2, distinguishable from the v1 fixture.

    Without this the ``total_nudges`` narrowing is undetectable: the artifact
    keeps the same key with the same type and a different meaning.
    """
    from trw_mcp.state.nudge_analysis import (
        _ARTIFACT_SCHEMA_VERSION,
        NudgeAnalysis,
        analysis_artifact_dict,
    )

    assert _ARTIFACT_SCHEMA_VERSION == 2
    emitted = analysis_artifact_dict(NudgeAnalysis())
    assert emitted["schema_version"] == 2
    assert emitted["schema_version"] != _v1_artifact()["schema_version"]


def test_nudge_analysis_v2_is_additive_over_v1(tmp_path: Path) -> None:
    """NFR04: no v1 key removed or retyped, so a v1 reader still parses v2.

    Goes through the real writer (``persist_nudge_analysis``) rather than
    asserting on the dataclass, because the artifact on disk is the contract.
    """
    from trw_mcp.state.nudge_analysis import NudgeAnalysis, persist_nudge_analysis

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    written = persist_nudge_analysis(
        trw_dir,
        NudgeAnalysis(
            generated_at="2026-07-25T00:00:00+00:00",
            session_id="s-additive",
            phase="validate",
            applicable=True,
            total_nudges=0,
            total_emissions=3,
            emissions_by_pool={"workflow": 3},
        ),
    )
    assert written is not None
    v2 = _load_json(written)
    v1 = _v1_artifact()

    dropped = set(v1) - set(v2)
    assert not dropped, f"v2 dropped v1 keys {dropped} — the change is not additive"
    for key, v1_value in v1.items():
        if key == "schema_version":
            continue
        assert type(v2[key]) is type(v1_value), f"{key} was retyped between v1 and v2"
    assert set(v2) - set(v1) == {"total_emissions", "emissions_by_pool"}


def test_v1_consumer_reading_v2_recovers_volume_from_the_additive_fields(tmp_path: Path) -> None:
    """The narrowing is silent to a v1 reader; the true volume must still be there.

    A session that emitted 3 nudges, none of them step-targeted, reads
    ``total_nudges == 0`` to a v1 consumer — a 3-to-0 discontinuity trw-mcp
    cannot stop downstream. What it guarantees instead: the honest volume is
    present in the SAME artifact, and ``schema_version`` says the number
    changed meaning so the consumer can branch on it.
    """
    from trw_mcp.state.nudge_analysis import NudgeAnalysis, persist_nudge_analysis

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    written = persist_nudge_analysis(
        trw_dir,
        NudgeAnalysis(
            applicable=True,
            total_nudges=0,
            total_emissions=3,
            emissions_by_pool={"workflow": 2, "learnings": 1},
        ),
    )
    assert written is not None
    v2 = _load_json(written)

    assert _v1_style_total_nudges(v2) == 0, "the v1 field is the one that silently zeroed"
    assert v2["total_emissions"] == 3
    by_pool = v2["emissions_by_pool"]
    assert isinstance(by_pool, dict)
    assert sum(by_pool.values()) == 3
    assert v2["schema_version"] != _v1_artifact()["schema_version"]
