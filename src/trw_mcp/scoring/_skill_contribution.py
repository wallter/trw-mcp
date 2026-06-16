"""Per-skill contribution scoring, retirement, and duplicate-flag reports.

PRD-QUAL-111 (FR02/FR04/FR05). Internal module -- public names are re-exported
from ``trw_mcp.scoring``.

HONEST SIGNAL (PRD-QUAL-111-NFR05): the contribution score is an
**invoked-after-surface + recency** signal -- a recency-weighted rate of
skill-surface events whose ``invoked_after_surface`` flag is true. It is NOT a
causal skill->task-success signal and MUST NOT be presented as evidence that a
skill *caused* a better outcome. The causal outcome-correlation term is an
enumerated stub (see PRD ``stubs[]``, activation gate: a per-invocation
skill->downstream-task-verdict signal is captured); it is out of scope here.

All readers FAIL OPEN (NFR02) to a neutral result (cold-start score / empty
report) on a corrupt or absent log -- never raising into callers. Retirement is
a reversible STATUS, never a deletion (NG1); duplicate detection FLAGS pairs,
never auto-merges (NG2). No ``data/skills/`` file is mutated by this module.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from trw_mcp.scoring._utils import _LN2, TRWConfig, _clamp01, get_config, logger
from trw_mcp.state.skill_surface_tracking import (
    SkillSurfaceEvent,
    read_skill_surface_events,
)

_LIFECYCLE_FILE = "skill_lifecycle.jsonl"
_LOG_DIR = "logs"

__all__ = [
    "DuplicateSkillFlag",
    "SkillLifecycleRecord",
    "compute_skill_contribution",
    "compute_skill_lifecycle_report",
    "find_duplicate_skills",
]


# ---------------------------------------------------------------------------
# FR02: per-skill contribution score with recency decay
# ---------------------------------------------------------------------------


def _recency_weight(days_since: float, half_life_days: float) -> float:
    """Ebbinghaus exponential weight, mirroring ``apply_impact_decay``'s shape.

    ``weight = exp(-ln(2) * days_since / half_life)`` -- the SAME exponential
    decay shape (``_decay.apply_impact_decay``) reused for the skill surface,
    not re-derived. A weight at exactly one half-life is 0.5.
    """
    return math.exp(-_LN2 * max(0.0, days_since) / max(half_life_days, 1.0))


def compute_skill_contribution(
    events: Sequence[SkillSurfaceEvent],
    *,
    now: datetime | None = None,
    half_life_days: int | None = None,
    cold_start: float | None = None,
) -> float:
    """Return a per-skill ``float`` contribution score in ``[0.0, 1.0]``.

    The score is the **recency-weighted invoked-after-surface rate**: each
    surface event contributes a recency weight (Ebbinghaus, FR02) toward the
    denominator, and only events whose ``invoked_after_surface`` is true
    contribute that same weight toward the numerator. Recent events therefore
    dominate older ones (recency decay is monotonic in age).

    A skill with **zero** recorded surface events returns the configured
    cold-start score (default ``skill_contribution_cold_start`` = 0.5, above the
    retirement floor) so a never-surfaced skill is not immediately retired.

    Fail-open (NFR02): malformed individual events are skipped; no raise.
    """
    cfg: TRWConfig = get_config()
    hl = half_life_days if half_life_days is not None else cfg.skill_contribution_half_life_days
    cs = cold_start if cold_start is not None else cfg.skill_contribution_cold_start
    ref = now if now is not None else datetime.now(timezone.utc)

    weighted_total = 0.0
    weighted_invoked = 0.0

    for event in events:
        surfaced_at = str(event.get("surfaced_at", ""))
        if not surfaced_at:
            continue
        try:
            ts = datetime.fromisoformat(surfaced_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days_since = max(0.0, (ref - ts).total_seconds() / 86400.0)
        weight = _recency_weight(days_since, float(hl))
        weighted_total += weight
        if bool(event.get("invoked_after_surface", False)):
            weighted_invoked += weight

    if weighted_total <= 0.0:
        # No usable events -> cold-start (never-surfaced skill protection).
        return _clamp01(cs)

    return _clamp01(weighted_invoked / weighted_total)


# ---------------------------------------------------------------------------
# FR04: outcome-driven retirement (reversible status, audited)
# ---------------------------------------------------------------------------


class SkillLifecycleRecord(TypedDict):
    """Per-skill lifecycle classification (PRD-QUAL-111-FR04).

    ``status`` closed set: ``active`` | ``retired``.
    ``reason`` closed set: ``below_floor`` (when retired) | ``""`` (when active).
    """

    skill_name: str
    status: str
    contribution_score: float
    windows_below_floor: int
    reason: str
    computed_at: str


def _read_prior_windows(trw_dir: Path) -> dict[str, int]:
    """Return the most recent ``windows_below_floor`` count per skill.

    Reads the append-only ``skill_lifecycle.jsonl`` window log and keeps the
    last record seen per skill (file order is chronological). Fail-open: an
    absent/corrupt log yields an empty mapping (NFR02).
    """
    from trw_mcp.state._helpers import read_jsonl_resilient, safe_int

    log_path = trw_dir / _LOG_DIR / _LIFECYCLE_FILE
    counts: dict[str, int] = {}
    for rec in read_jsonl_resilient(log_path):
        name = str(rec.get("skill_name", ""))
        if not name:
            continue
        counts[name] = safe_int(rec, "windows_below_floor", 0)
    return counts


def _append_lifecycle_records(trw_dir: Path, records: Sequence[SkillLifecycleRecord]) -> None:
    """Append one window record per skill to ``skill_lifecycle.jsonl``.

    Fail-open: never raises. The ``.trw/logs/`` dir is created ``0700``.
    """
    import json

    try:
        from trw_mcp.state._helpers import rotate_jsonl
        from trw_mcp.state._paths_permissions import harden_dir_mode

        log_dir = trw_dir / _LOG_DIR
        harden_dir_mode(trw_dir, create=True)
        harden_dir_mode(log_dir, create=True)
        log_path = log_dir / _LIFECYCLE_FILE
        rotate_jsonl(log_path)
        with log_path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # trw:intentional fail-open-lifecycle-logging
        logger.debug("skill_lifecycle_log_failed", exc_info=True)


def compute_skill_lifecycle_report(
    trw_dir: Path,
    skill_names: Sequence[str],
    *,
    now: datetime | None = None,
    persist: bool = True,
) -> list[SkillLifecycleRecord]:
    """Classify each skill as ``active`` or ``retired`` for one window (FR04).

    A skill is ``retired`` when its FR02 contribution score stays **strictly
    below** ``skill_retirement_floor`` for ``skill_retirement_windows``
    CONSECUTIVE windows. Boundary semantics: a score exactly equal to the floor
    is NOT below floor and resets the counter.

    Reversible (NG1): when a previously-retired skill's score returns to >=
    floor, ``windows_below_floor`` resets to 0 and status returns to ``active``.
    NO SKILL.md file is moved or deleted -- retirement is a STATUS only.

    Each computation is one "window": it reads the prior window counts from
    ``skill_lifecycle.jsonl``, computes the new counts, and (when ``persist``)
    appends the new window records. Fail-open (NFR02): a corrupt/absent log
    degrades to an all-active report.
    """
    cfg: TRWConfig = get_config()
    floor = cfg.skill_retirement_floor
    windows_needed = cfg.skill_retirement_windows
    ref = now if now is not None else datetime.now(timezone.utc)

    prior = _read_prior_windows(trw_dir)
    all_events = read_skill_surface_events(trw_dir)
    by_skill: dict[str, list[SkillSurfaceEvent]] = {}
    for ev in all_events:
        name = str(ev.get("skill_name", ""))
        if name:
            by_skill.setdefault(name, []).append(ev)

    records: list[SkillLifecycleRecord] = []
    for name in skill_names:
        score = compute_skill_contribution(by_skill.get(name, []), now=ref)
        # Strict less-than (FR04 boundary): score == floor is NOT below floor.
        below = score < floor
        windows = (prior.get(name, 0) + 1) if below else 0
        retired = below and windows >= windows_needed
        records.append(
            {
                "skill_name": name,
                "status": "retired" if retired else "active",
                "contribution_score": round(score, 6),
                "windows_below_floor": windows,
                "reason": "below_floor" if retired else "",
                "computed_at": ref.isoformat(),
            }
        )

    if persist:
        _append_lifecycle_records(trw_dir, records)
    return records


# ---------------------------------------------------------------------------
# FR05: duplicate/overlap consolidation (FLAG-only, never auto-merge)
# ---------------------------------------------------------------------------


class DuplicateSkillFlag(TypedDict):
    """A flagged near-duplicate skill pair (PRD-QUAL-111-FR05)."""

    name_a: str
    name_b: str
    similarity: float


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (na * nb)


def _token_jaccard(a: str, b: str) -> float:
    """Token-Jaccard similarity fallback when embeddings are unavailable."""
    import re

    tok = re.compile(r"[A-Za-z0-9_]+")
    sa = {t.lower() for t in tok.findall(a)}
    sb = {t.lower() for t in tok.findall(b)}
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def find_duplicate_skills(
    descriptions: Mapping[str, str],
    *,
    threshold: float | None = None,
    max_skills: int | None = None,
) -> list[DuplicateSkillFlag]:
    """Flag near-duplicate skills by SKILL.md description similarity (FR05).

    Embeds each ``SkillManifest.description`` via the trw-memory local embedder
    (cosine similarity); degrades gracefully to a token-Jaccard fallback when
    ``sqlite-vec``/embeddings are unavailable. Every PAIR whose similarity is at
    or above ``skill_duplicate_similarity_threshold`` (default 0.85) is flagged.

    FLAG-ONLY (NG2): no SKILL.md is read-write opened, merged, moved, or
    deleted. The persisted record carries only skill names + a numeric
    similarity -- never embedded vectors or raw description bodies (NFR04).

    Pairwise comparison is capped at ``skill_duplicate_max_skills`` (NFR03) to
    bound the O(n^2) cost for pathological library growth.
    """
    cfg: TRWConfig = get_config()
    thr = threshold if threshold is not None else cfg.skill_duplicate_similarity_threshold
    cap = max_skills if max_skills is not None else cfg.skill_duplicate_max_skills

    items = list(descriptions.items())[: max(0, cap)]
    if len(items) < 2:
        return []

    # Try embeddings; on any failure, fall back to token-Jaccard (degrade).
    vectors: dict[str, list[float] | None] = {}
    try:
        from trw_memory.embeddings import get_local_embedder

        embedder = get_local_embedder()
        if embedder is not None:
            for name, desc in items:
                vectors[name] = embedder.embed(desc)
    except Exception:  # trw:intentional fail-open: embeddings optional, fall back to Jaccard
        logger.debug("skill_duplicate_embed_failed", exc_info=True)
        vectors = {}

    flags: list[DuplicateSkillFlag] = []
    for i in range(len(items)):
        name_a, desc_a = items[i]
        for j in range(i + 1, len(items)):
            name_b, desc_b = items[j]
            va, vb = vectors.get(name_a), vectors.get(name_b)
            if va is not None and vb is not None and len(va) == len(vb):
                sim = _cosine(va, vb)
            else:
                sim = _token_jaccard(desc_a, desc_b)
            if sim >= thr:
                flags.append({"name_a": name_a, "name_b": name_b, "similarity": round(sim, 6)})
    return flags
