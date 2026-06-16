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


def test_discovery_emits_one_skill_surface_event_per_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    events: list[SkillSurfaceEvent] = [
        {"skill_name": "x", "surfaced_at": naive, "invoked_after_surface": True}
    ]
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


def test_tracking_enabled_with_active_cap_none_surfaces_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_below_floor_n_minus_1_windows_is_still_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Boundary guard: exactly (skill_retirement_windows - 1) consecutive
    # below-floor windows must keep status "active" (windows_below_floor <
    # needed). Catches a >= -> > regression that the N-windows test misses.
    trw_dir = tmp_path / ".trw"
    cfg = get_config().model_copy(update={"skill_retirement_windows": 3})
    monkeypatch.setattr("trw_mcp.scoring._skill_contribution.get_config", lambda: cfg)
    needed = cfg.skill_retirement_windows
    assert needed == 3

    # All surfaces never invoked -> contribution 0.0 < floor.
    _seed_surface_log(
        trw_dir, [_event("edge", days_ago=1, invoked=False) for _ in range(3)]
    )

    last: list = []
    for _ in range(needed - 1):  # exactly N-1 consecutive below-floor windows
        last = compute_skill_lifecycle_report(trw_dir, ["edge"])
    rec = last[0]
    assert rec["status"] == "active"
    assert rec["reason"] == ""
    assert rec["windows_below_floor"] == needed - 1
    assert rec["windows_below_floor"] < needed


def test_retirement_is_reversible_and_no_file_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    flags = find_duplicate_skills(
        {"a": "alpha topic", "b": "alpha topic too"}, threshold=0.99
    )
    assert len(flags) == 1
    assert flags[0]["similarity"] == pytest.approx(1.0)


def test_duplicate_max_skills_cap_bounds_pairs() -> None:
    # max_skills=1 truncates to a single skill -> no pairs possible.
    flags = find_duplicate_skills(
        {"a": "same desc", "b": "same desc"}, threshold=0.5, max_skills=1
    )
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
    defaulted = discover_meta_skills(
        paths, query="review python", active_cap=None, session_id=""
    )
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
