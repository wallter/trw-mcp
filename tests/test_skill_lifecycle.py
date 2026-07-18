"""PRD-QUAL-111 skill lifecycle tests.

Covers FR01 (skill surface tracking), FR02 (contribution score + recency
decay), FR03 (bounded active-cap), FR04 (reversible retirement), FR05
(duplicate flag), NFR01 (default-off byte-identical), NFR05 (honest signal).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.scoring import (
    compute_skill_contribution,
    compute_skill_lifecycle_report,
    find_duplicate_skills,
)
from trw_mcp.state.skill_surface_tracking import (
    SkillSurfaceEvent,
    log_skill_surface_event,
    read_skill_surface_events,
)
from trw_mcp.tools.skill_discovery import discover_meta_skills

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_skill(root: Path, slug: str, description: str, *, body: str = "Skill body.") -> Path:
    skill_dir = root / slug
    skill_dir.mkdir()
    skill_path = skill_dir / "SKILL.md"
    frontmatter = f"name: {slug}\ndescription: {description}\n"
    skill_path.write_text(f"---\n{frontmatter}---\n{body}\n", encoding="utf-8")
    return skill_path


def _enable_tracking(monkeypatch: pytest.MonkeyPatch, trw_dir: Path, **overrides: object) -> None:
    cfg = get_config().model_copy(update={"skill_surface_tracking_enabled": True, **overrides})
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)


def _event(skill: str, *, days_ago: float, invoked: bool) -> SkillSurfaceEvent:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "skill_name": skill,
        "surfaced_at": ts.isoformat(),
        "surface_type": "discovery",
        "invoked_after_surface": invoked,
    }


# ---------------------------------------------------------------------------
# FR01 -- skill surface tracking
# ---------------------------------------------------------------------------


def test_discovery_emits_one_skill_surface_event_per_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "skills"
    src.mkdir()
    a = _write_skill(src, "alpha", "review changed python code")
    b = _write_skill(src, "beta", "review changed python code variants")
    trw_dir = tmp_path / ".trw"
    _enable_tracking(monkeypatch, trw_dir)

    result = discover_meta_skills([a, b], query="review python")

    events = read_skill_surface_events(trw_dir)
    assert len(events) == len(result.candidates)
    names = {e["skill_name"] for e in events}
    assert names == {c.name for c in result.candidates}
    for e in events:
        assert e["skill_name"]
        assert e["surfaced_at"]


def test_writer_fails_open_on_bad_dir(tmp_path: Path) -> None:
    # trw_dir points at an existing FILE so logs/ cannot be created -> must not raise.
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    log_skill_surface_event(bad, skill_name="alpha")  # must not raise
    assert read_skill_surface_events(bad) == []


# ---------------------------------------------------------------------------
# FR02 -- contribution score with recency decay
# ---------------------------------------------------------------------------


def test_recent_events_outscore_decayed_events() -> None:
    recent = [_event("s", days_ago=1, invoked=True), _event("s", days_ago=1, invoked=False)]
    # Older than one half-life (90d default): the invoked weight is decayed.
    old = [_event("s", days_ago=200, invoked=True), _event("s", days_ago=1, invoked=False)]
    recent_score = compute_skill_contribution(recent)
    old_score = compute_skill_contribution(old)
    assert recent_score > old_score


def test_no_events_returns_cold_start() -> None:
    cfg = get_config()
    assert compute_skill_contribution([]) == pytest.approx(cfg.skill_contribution_cold_start)


def test_malformed_events_are_skipped_returns_cold_start() -> None:
    cfg = get_config()
    bad: list[SkillSurfaceEvent] = [
        {"skill_name": "x"},  # missing surfaced_at
        {"skill_name": "x", "surfaced_at": "not-a-timestamp"},  # bad ISO
    ]
    # All events unusable -> falls back to cold-start.
    assert compute_skill_contribution(bad) == pytest.approx(cfg.skill_contribution_cold_start)


def test_naive_timestamp_is_treated_as_utc() -> None:
    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    events: list[SkillSurfaceEvent] = [{"skill_name": "x", "surfaced_at": naive, "invoked_after_surface": True}]
    # Single recent invoked event -> rate approaches 1.0 (no raise on naive ts).
    assert compute_skill_contribution(events) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# FR03 -- bounded active-cap
# ---------------------------------------------------------------------------


def _five_skills(root: Path) -> list[Path]:
    root.mkdir()
    return [_write_skill(root, f"sk{i}", f"review python topic {i}") for i in range(5)]


def test_active_cap_truncates_top_n(tmp_path: Path) -> None:
    paths = _five_skills(tmp_path / "skills")
    full = discover_meta_skills(paths, query="review python")
    capped = discover_meta_skills(paths, query="review python", active_cap=2)
    assert len(capped.candidates) == 2
    assert capped.candidates == full.candidates[:2]


def test_active_cap_none_is_noop(tmp_path: Path) -> None:
    paths = _five_skills(tmp_path / "skills")
    full = discover_meta_skills(paths, query="review python")
    explicit_none = discover_meta_skills(paths, query="review python", active_cap=None)
    assert len(full.candidates) == 5
    assert explicit_none.candidates == full.candidates


def test_tracking_enabled_with_active_cap_none_surfaces_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Guard: tracking ON + active_cap None must return ALL candidates (no
    # truncation) AND write one surface event for each of them. A regression
    # that zero-truncates on active_cap=None (or skips events) would break this.
    paths = _five_skills(tmp_path / "skills")
    trw_dir = tmp_path / ".trw"
    _enable_tracking(monkeypatch, trw_dir)

    result = discover_meta_skills(paths, query="review python", active_cap=None)

    assert len(result.candidates) == 5
    events = read_skill_surface_events(trw_dir)
    assert len(events) == 5
    assert {e["skill_name"] for e in events} == {c.name for c in result.candidates}


# ---------------------------------------------------------------------------
# FR04 -- reversible retirement
# ---------------------------------------------------------------------------


def _seed_surface_log(trw_dir: Path, events: list[SkillSurfaceEvent]) -> None:
    for e in events:
        log_skill_surface_event(
            trw_dir,
            skill_name=e["skill_name"],
            invoked_after_surface=e.get("invoked_after_surface"),
        )


def test_below_floor_n_windows_retires(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    trw_dir = tmp_path / ".trw"
    # All surfaces never invoked -> contribution 0.0 < floor 0.15.
    _seed_surface_log(trw_dir, [_event("dead", days_ago=1, invoked=False) for _ in range(3)])
    cfg = get_config().model_copy(update={"skill_retirement_windows": 3})
    monkeypatch.setattr("trw_mcp.scoring._skill_contribution.get_config", lambda: cfg)

    last: list = []
    for _ in range(3):
        last = compute_skill_lifecycle_report(trw_dir, ["dead"])
    rec = last[0]
    assert rec["status"] == "retired"
    assert rec["reason"] == "below_floor"
    assert rec["windows_below_floor"] >= cfg.skill_retirement_windows


def test_below_floor_n_minus_1_windows_is_still_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Boundary guard: exactly (skill_retirement_windows - 1) consecutive
    # below-floor windows must keep status "active" (windows_below_floor <
    # needed). Catches a >= -> > regression that the N-windows test misses.
    trw_dir = tmp_path / ".trw"
    cfg = get_config().model_copy(update={"skill_retirement_windows": 3})
    monkeypatch.setattr("trw_mcp.scoring._skill_contribution.get_config", lambda: cfg)
    needed = cfg.skill_retirement_windows
    assert needed == 3

    # All surfaces never invoked -> contribution 0.0 < floor.
    _seed_surface_log(trw_dir, [_event("edge", days_ago=1, invoked=False) for _ in range(3)])

    last: list = []
    for _ in range(needed - 1):  # exactly N-1 consecutive below-floor windows
        last = compute_skill_lifecycle_report(trw_dir, ["edge"])
    rec = last[0]
    assert rec["status"] == "active"
    assert rec["reason"] == ""
    assert rec["windows_below_floor"] == needed - 1
    assert rec["windows_below_floor"] < needed


def test_retirement_is_reversible_and_no_file_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "skills"
    src.mkdir()
    skill = _write_skill(src, "comeback", "review python")
    trw_dir = tmp_path / ".trw"
    cfg = get_config().model_copy(update={"skill_retirement_windows": 2})
    monkeypatch.setattr("trw_mcp.scoring._skill_contribution.get_config", lambda: cfg)

    # Retire: never-invoked surfaces for 2 windows.
    _seed_surface_log(trw_dir, [_event("comeback", days_ago=1, invoked=False) for _ in range(2)])
    compute_skill_lifecycle_report(trw_dir, ["comeback"])
    retired = compute_skill_lifecycle_report(trw_dir, ["comeback"])
    assert retired[0]["status"] == "retired"

    # Reverse: add invoked-after-surface evidence so score returns >= floor.
    _seed_surface_log(trw_dir, [_event("comeback", days_ago=0, invoked=True) for _ in range(5)])
    revived = compute_skill_lifecycle_report(trw_dir, ["comeback"])
    assert revived[0]["status"] == "active"
    assert revived[0]["windows_below_floor"] == 0
    # NG1: SKILL.md still exists -- never deleted.
    assert skill.exists()


def test_score_exactly_at_floor_is_not_below(monkeypatch: pytest.MonkeyPatch) -> None:
    # Strict less-than boundary (FR04): a score == floor does not count.
    cfg = get_config()
    floor = cfg.skill_retirement_floor
    monkeypatch.setattr(
        "trw_mcp.scoring._skill_contribution.compute_skill_contribution",
        lambda *a, **k: floor,
    )
    recs = compute_skill_lifecycle_report(Path("/nonexistent"), ["x"], persist=False)
    assert recs[0]["status"] == "active"
    assert recs[0]["windows_below_floor"] == 0


# ---------------------------------------------------------------------------
# FR05 -- duplicate flag
# ---------------------------------------------------------------------------


def test_near_duplicate_descriptions_flagged_no_mutation(tmp_path: Path) -> None:
    src = tmp_path / "skills"
    src.mkdir()
    desc = "review changed python code for correctness and style"
    a = _write_skill(src, "rev_a", desc)
    b = _write_skill(src, "rev_b", desc)  # identical description
    c = _write_skill(src, "unrelated", "generate release notes from git history")
    before = {p: p.read_bytes() for p in (a, b, c)}

    flags = find_duplicate_skills(
        {"rev_a": desc, "rev_b": desc, "unrelated": "generate release notes from git history"},
        threshold=0.85,
    )
    pairs = {frozenset((f["name_a"], f["name_b"])) for f in flags}
    assert frozenset(("rev_a", "rev_b")) in pairs
    assert frozenset(("rev_a", "unrelated")) not in pairs
    # No mutation of any SKILL.md.
    assert {p: p.read_bytes() for p in (a, b, c)} == before


def test_duplicate_flag_uses_embeddings_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the embedding branch: identical vectors -> cosine 1.0 >= threshold.
    class _Embedder:
        def embed(self, text: str) -> list[float]:
            return [1.0, 0.0, 0.0] if "alpha" in text else [0.9, 0.1, 0.0]

    import trw_memory.embeddings as emb

    monkeypatch.setattr(emb, "get_local_embedder", lambda: _Embedder())
    flags = find_duplicate_skills({"a": "alpha topic", "b": "alpha topic too"}, threshold=0.99)
    assert len(flags) == 1
    assert flags[0]["similarity"] == pytest.approx(1.0)


def test_duplicate_max_skills_cap_bounds_pairs() -> None:
    # max_skills=1 truncates to a single skill -> no pairs possible.
    flags = find_duplicate_skills({"a": "same desc", "b": "same desc"}, threshold=0.5, max_skills=1)
    assert flags == []


def test_below_threshold_not_flagged() -> None:
    flags = find_duplicate_skills(
        {
            "a": "review changed python code",
            "b": "generate release notes from git history entirely different topic",
        },
        threshold=0.85,
    )
    assert flags == []


# ---------------------------------------------------------------------------
# NFR01 -- default-off byte-identical regression pin
# ---------------------------------------------------------------------------


def test_all_defaults_discovery_byte_identical(tmp_path: Path) -> None:
    # With all flags at default (tracking OFF, active_cap None), discovery is
    # byte-for-byte identical to a call that passes no lifecycle args at all.
    paths = _five_skills(tmp_path / "skills")
    baseline = discover_meta_skills(paths, query="review python")
    defaulted = discover_meta_skills(paths, query="review python", active_cap=None, session_id="")
    assert defaulted.model_dump(mode="json") == baseline.model_dump(mode="json")
    # And the default config flag is OFF.
    assert TRWConfig().skill_surface_tracking_enabled is False


def test_tracking_disabled_writes_no_events(tmp_path: Path) -> None:
    paths = _five_skills(tmp_path / "skills")
    trw_dir = tmp_path / ".trw"
    discover_meta_skills(paths, query="review python")  # default: tracking OFF
    assert read_skill_surface_events(trw_dir) == []


# ---------------------------------------------------------------------------
# NFR05 -- honest signal (no causal overclaim)
# ---------------------------------------------------------------------------


def test_contribution_docstring_disclaims_causal_signal() -> None:
    import trw_mcp.scoring._skill_contribution as mod

    text = (mod.__doc__ or "") + (compute_skill_contribution.__doc__ or "")
    lowered = text.lower()
    assert "invoked-after-surface" in lowered
    assert "not a causal" in lowered or "not be presented" in lowered


# ---------------------------------------------------------------------------
# PRD-CORE-218-FR07 -- evidence-backed reversible skill lifecycle (state machine)
#
# FR07 consolidates the QUAL-111 evidence stream and adds a finite, reversible
# lifecycle state machine (active -> deprecated -> hidden -> retired -> removed)
# with a per-transition removal contract, plus a deterministic step-based
# contribution signal. The authority lives in ``tools/_skill_lifecycle.py``;
# ``skill_discovery`` consumes its advertising filter.
# ---------------------------------------------------------------------------

import dataclasses

from trw_mcp.tools._skill_lifecycle import (
    DuplicateFlag,
    LifecycleTransitionError,
    SkillEvidenceKind,
    SkillEvidenceLedger,
    SkillEvidenceRecord,
    SkillLifecycleRecord,
    SkillLifecycleState,
    advance,
    apply_active_cap,
    contribution_signal,
    flag_near_duplicates,
    is_advertisable,
    rank_by_contribution,
    restore,
)


def _fr07_no_causal_field(cls: type) -> None:
    """No field in the record schema may imply causal task success."""
    banned = {"success", "succeeded", "caused", "causal", "outcome", "won", "passed"}
    names = {f.name.lower() for f in dataclasses.fields(cls)}
    assert names.isdisjoint(banned), f"{cls.__name__} leaks a causal-success field: {names & banned}"


def _fr07_advance_full(record: SkillLifecycleRecord, to_state: SkillLifecycleState) -> SkillLifecycleRecord:
    return advance(
        record,
        to_state,
        owner="platform",
        evidence_window="2026-07-01..2026-07-11",
        expiry="2026-08-01",
        replacement="trw_new_skill",
        rollback_snapshot="snap-1",
    )


def test_prd_core_218_fr07(tmp_path: Path) -> None:
    # --- schema truthfulness: no fabricated causal-success field --------------
    _fr07_no_causal_field(SkillEvidenceRecord)

    # --- surfaced / invoked evidence + deterministic contribution -------------
    ledger = SkillEvidenceLedger(max_records=100)
    ledger.record("alpha", SkillEvidenceKind.SURFACED, at_step=0)
    ledger.record("alpha", SkillEvidenceKind.INVOKED, at_step=8)
    ledger.record("beta", SkillEvidenceKind.SURFACED, at_step=1)

    alpha = contribution_signal(ledger, "alpha", now_step=10)
    beta = contribution_signal(ledger, "beta", now_step=10)
    assert alpha > beta  # recent invoked evidence outranks a stale surface
    assert contribution_signal(ledger, "alpha", now_step=10) == alpha  # deterministic
    assert contribution_signal(ledger, "unknown", now_step=10) == 0.0

    ranked = rank_by_contribution(ledger, ["alpha", "beta"], now_step=10)
    assert ranked == ("alpha", "beta")

    # --- bounded append-only ledger evicts oldest -----------------------------
    small = SkillEvidenceLedger(max_records=2)
    for step in range(5):
        small.record("gamma", SkillEvidenceKind.SURFACED, at_step=step)
    assert len(small.records) == 2
    assert [r.at_step for r in small.records] == [3, 4]

    # --- active cap applies AFTER ranking, None is a no-op --------------------
    assert apply_active_cap(ranked, None) == ranked
    assert apply_active_cap(ranked, 1) == ("alpha",)
    assert apply_active_cap(ranked, 0) == ()

    # --- near-duplicate descriptions flagged, never merged --------------------
    flags = flag_near_duplicates(
        {
            "search_one": "search the code index for a symbol",
            "search_two": "search the code index for a symbol name",
            "unrelated": "render a marketing video caption",
        }
    )
    assert flags and all(isinstance(f, DuplicateFlag) for f in flags)
    flagged_pairs = {(f.skill_a, f.skill_b) for f in flags}
    assert ("search_one", "search_two") in flagged_pairs
    # Flagging never merges/removes an unrelated skill.
    assert "unrelated" not in {name for pair in flagged_pairs for name in pair}

    # --- lifecycle: forward requires all fields; skipping a state is refused ---
    record = SkillLifecycleRecord("trw_old_skill")
    assert record.state is SkillLifecycleState.ACTIVE

    with pytest.raises(LifecycleTransitionError):
        advance(  # missing required fields
            record,
            SkillLifecycleState.DEPRECATED,
            owner="",
            evidence_window="",
            expiry="",
            replacement="",
            rollback_snapshot="",
        )
    with pytest.raises(LifecycleTransitionError):
        _fr07_advance_full(record, SkillLifecycleState.RETIRED)  # non-adjacent skip

    deprecated = _fr07_advance_full(record, SkillLifecycleState.DEPRECATED)
    hidden = _fr07_advance_full(deprecated, SkillLifecycleState.HIDDEN)
    retired = _fr07_advance_full(hidden, SkillLifecycleState.RETIRED)
    assert retired.state is SkillLifecycleState.RETIRED

    # --- retirement is reversible BEFORE removal ------------------------------
    restored = restore(retired, owner="platform", reason="rollback")
    assert restored.state is SkillLifecycleState.HIDDEN  # reverts to the prior state
    with pytest.raises(LifecycleTransitionError):
        restore(retired, owner="", reason="")  # missing fields refused

    removed = _fr07_advance_full(retired, SkillLifecycleState.REMOVED)
    assert removed.state is SkillLifecycleState.REMOVED
    with pytest.raises(LifecycleTransitionError):
        restore(removed, owner="platform", reason="too-late")  # removal is terminal

    # --- discovery stops advertising retired skills ---------------------------
    assert is_advertisable(SkillLifecycleState.ACTIVE) is True
    assert is_advertisable(SkillLifecycleState.DEPRECATED) is True
    assert is_advertisable(SkillLifecycleState.RETIRED) is False
    assert is_advertisable(SkillLifecycleState.REMOVED) is False

    src = tmp_path / "fr07_skills"
    src.mkdir()
    _write_skill(src, "live_skill", "index the repository code")
    _write_skill(src, "retired_skill", "index the repository code")
    paths = [src / "live_skill" / "SKILL.md", src / "retired_skill" / "SKILL.md"]
    result = discover_meta_skills(
        paths,
        query="index code",
        lifecycle_states={"retired_skill": SkillLifecycleState.RETIRED},
    )
    surfaced = {candidate.name for candidate in result.candidates}
    assert "live_skill" in surfaced
    assert "retired_skill" not in surfaced

    # Without the lifecycle map, prior behavior is preserved (both surface).
    baseline = discover_meta_skills(paths, query="index code")
    assert {c.name for c in baseline.candidates} == {"live_skill", "retired_skill"}


# ---------------------------------------------------------------------------
# PRD-CORE-218-FR07 P0-3 -- persisted lifecycle store + PRODUCTION wiring
#
# The state-machine authority in ``tools/_skill_lifecycle.py`` was never
# consulted by production because no persisted state existed and the registered
# ``trw_skill_discovery`` tool passed no ``lifecycle_states``. These tests drive
# the NEW ``state/skill_lifecycle_store.py`` persistence layer AND assert the
# REGISTERED tool now withholds retired skills.
# ---------------------------------------------------------------------------

import structlog

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.state.skill_lifecycle_store import (
    load_lifecycle_records,
    load_lifecycle_states,
    restore_skill,
    skill_lifecycle_store_path,
    transition_skill,
)

_RETIRE_FIELDS: dict[str, str] = {
    "owner": "platform",
    "evidence_window": "2026-07-01..2026-07-11",
    "expiry": "2026-08-01",
    "replacement": "trw_new_skill",
    "rollback_snapshot": "snap-1",
}


def _point_trw_dir(monkeypatch: pytest.MonkeyPatch, trw_dir: Path) -> None:
    # The store resolves its path lazily via _paths.resolve_trw_dir at call time.
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)


def _retire_in_store(skill_name: str) -> SkillLifecycleRecord:
    """Walk a skill active -> deprecated -> hidden -> retired in the store."""
    transition_skill(skill_name, SkillLifecycleState.DEPRECATED, **_RETIRE_FIELDS)
    transition_skill(skill_name, SkillLifecycleState.HIDDEN, **_RETIRE_FIELDS)
    return transition_skill(skill_name, SkillLifecycleState.RETIRED, **_RETIRE_FIELDS)


def _discovery_tool() -> object:
    return extract_tool_fn(make_test_server("skill_discovery"), "trw_skill_discovery")


def test_store_roundtrip_persists_state_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_trw_dir(monkeypatch, tmp_path / ".trw")

    record = _retire_in_store("deadskill")
    assert record.state is SkillLifecycleState.RETIRED

    # Persisted to .trw/skills/lifecycle.json and reloadable across a fresh read.
    assert skill_lifecycle_store_path() == tmp_path / ".trw" / "skills" / "lifecycle.json"
    assert skill_lifecycle_store_path().exists()
    assert load_lifecycle_states() == {"deadskill": SkillLifecycleState.RETIRED}
    reloaded = load_lifecycle_records()["deadskill"]
    assert reloaded.state is SkillLifecycleState.RETIRED
    assert reloaded.history[-1].to_state is SkillLifecycleState.RETIRED
    assert reloaded.history[-1].owner == "platform"


def test_store_transition_contract_refusal_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_trw_dir(monkeypatch, tmp_path / ".trw")

    # Non-adjacent forward move is refused by the delegated advance() contract.
    with pytest.raises(LifecycleTransitionError):
        transition_skill("x", SkillLifecycleState.RETIRED, **_RETIRE_FIELDS)
    assert not skill_lifecycle_store_path().exists()

    # Missing required removal-contract fields are refused; still no file.
    with pytest.raises(LifecycleTransitionError):
        transition_skill(
            "x",
            SkillLifecycleState.DEPRECATED,
            owner="",
            evidence_window="",
            expiry="",
            replacement="",
            rollback_snapshot="",
        )
    assert not skill_lifecycle_store_path().exists()


def test_registered_discovery_withholds_retired_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_trw_dir(monkeypatch, tmp_path / ".trw")
    src = tmp_path / "skills"
    src.mkdir()
    live = _write_skill(src, "live_skill", "index the repository code")
    retired = _write_skill(src, "retired_skill", "index the repository code")
    _retire_in_store("retired_skill")

    result = _discovery_tool()([str(live), str(retired)], "index code")  # type: ignore[operator]

    names = {candidate["name"] for candidate in result["candidates"]}
    assert "live_skill" in names
    assert "retired_skill" not in names


def test_registered_discovery_restore_brings_skill_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # ``_skill_lifecycle.restore`` is a single-step undo of the most recent
    # transition. Move the skill into HIDDEN (a withheld state) so ONE restore
    # returns it to DEPRECATED (advertisable) -- proving the reversible path
    # through the persisted store re-advertises a withheld skill.
    _point_trw_dir(monkeypatch, tmp_path / ".trw")
    src = tmp_path / "skills"
    src.mkdir()
    skill = _write_skill(src, "comeback_skill", "index the repository code")
    transition_skill("comeback_skill", SkillLifecycleState.DEPRECATED, **_RETIRE_FIELDS)
    transition_skill("comeback_skill", SkillLifecycleState.HIDDEN, **_RETIRE_FIELDS)
    tool = _discovery_tool()

    withheld = {c["name"] for c in tool([str(skill)], "index code")["candidates"]}  # type: ignore[operator]
    assert "comeback_skill" not in withheld

    restored_record = restore_skill("comeback_skill", owner="platform", reason="rollback")
    assert restored_record.state is SkillLifecycleState.DEPRECATED
    assert load_lifecycle_states()["comeback_skill"] is SkillLifecycleState.DEPRECATED

    restored = {c["name"] for c in tool([str(skill)], "index code")["candidates"]}  # type: ignore[operator]
    assert "comeback_skill" in restored


def test_registered_discovery_missing_store_advertises_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _point_trw_dir(monkeypatch, tmp_path / ".trw")  # .trw never created
    src = tmp_path / "skills"
    src.mkdir()
    a = _write_skill(src, "one", "index the repository code")
    b = _write_skill(src, "two", "index the repository code")

    result = _discovery_tool()([str(a), str(b)], "index code")  # type: ignore[operator]

    assert {c["name"] for c in result["candidates"]} == {"one", "two"}
    assert not skill_lifecycle_store_path().exists()


def test_registered_discovery_corrupt_store_advertises_all_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from structlog.testing import capture_logs

    _point_trw_dir(monkeypatch, tmp_path / ".trw")
    src = tmp_path / "skills"
    src.mkdir()
    a = _write_skill(src, "one", "index the repository code")
    b = _write_skill(src, "two", "index the repository code")

    # A torn / hand-corrupted store must NOT silently withhold skills.
    store_path = skill_lifecycle_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("{ this is not valid json", encoding="utf-8")

    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    tool = _discovery_tool()
    with capture_logs() as logs:
        result = tool([str(a), str(b)], "index code")  # type: ignore[operator]

    # Fail-open: corrupt store => everything is still advertised.
    assert {c["name"] for c in result["candidates"]} == {"one", "two"}
    # But NOT silent: a WARN naming the malformed store was emitted.
    warnings = [e for e in logs if e.get("event") == "skill_lifecycle_store_malformed_fallback"]
    assert warnings, f"expected malformed-fallback warning, got {[e.get('event') for e in logs]}"
    assert warnings[0]["log_level"] == "warning"


def test_load_records_corrupt_returns_empty_with_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from structlog.testing import capture_logs

    _point_trw_dir(monkeypatch, tmp_path / ".trw")
    store_path = skill_lifecycle_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    # Valid JSON but a bad per-skill entry poisons the whole store (fail-open).
    store_path.write_text('{"version": 1, "records": {"x": {"state": "banana"}}}', encoding="utf-8")

    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    with capture_logs() as logs:
        records = load_lifecycle_records()

    assert records == {}
    assert any(e.get("event") == "skill_lifecycle_store_malformed_fallback" for e in logs)
