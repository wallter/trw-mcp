"""Bandit-based learning selection policy for the nudge system.

PRD-CORE-105 FR03/FR04/FR06: Tiered withholding, phase-transition bursts,
and micro-randomized withholding at phase boundaries.

Engineering workflow policy — sits on top of trw-memory bandit primitives.
Local-first behavior per Vision Principle 6: no backend connection needed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from trw_memory.bandit import BanditDecision, BanditSelector
from trw_memory.bandit.change_detection import PageHinkleyDetector

_logger = structlog.get_logger(__name__)

# Engineering context vector dimension (FR02 spec)
ENGINEERING_CONTEXT_DIM = 21

# ---------------------------------------------------------------------------
# Client class mapping
# ---------------------------------------------------------------------------

_CLIENT_CLASS_MAP: dict[str, str] = {
    "claude-code": "full_mode",
    "cursor": "full_mode",
    "opencode": "light_mode",
    "codex": "light_mode",
    "aider": "light_mode",
}

# ---------------------------------------------------------------------------
# Withholding rate ranges by tier and client class (FR03)
# ---------------------------------------------------------------------------

_WITHHOLDING_RATES: dict[str, dict[str, tuple[float, float]]] = {
    "full_mode": {
        "critical": (0.0, 0.0),
        "high": (0.05, 0.05),
        "normal": (0.12, 0.25),
        "low": (0.30, 0.50),
        "protected": (0.0, 0.0),   # Same as critical — human-protected entries
        "permanent": (0.0, 0.0),   # Same as critical — permanent entries
    },
    "light_mode": {
        "critical": (0.0, 0.0),
        "high": (0.05, 0.05),
        "normal": (0.20, 0.35),
        "low": (0.30, 0.50),
        "protected": (0.0, 0.0),
        "permanent": (0.0, 0.0),
    },
}

# Fallback rate for unknown tiers — matches normal-tier behavior
_UNKNOWN_TIER_RATE: tuple[float, float] = (0.12, 0.25)

# Phase one-hot encoding slots (6 phases)
_PHASES: tuple[str, ...] = ("research", "plan", "implement", "validate", "review", "deliver")

# Agent type one-hot encoding slots (4 types per PRD-CORE-105 spec)
_AGENT_TYPES: tuple[str, ...] = ("orchestrator", "implementer", "tester", "reviewer")
_AGENT_ALIASES: dict[str, str] = {"lead": "orchestrator", "auditor": "reviewer"}

# Task type one-hot encoding slots (6 types per PRD-CORE-105 spec)
_TASK_TYPES: tuple[str, ...] = ("feature", "bugfix", "refactor", "infrastructure", "docs", "investigation")

# Files count normalization ceiling
_FILES_COUNT_MAX = 100.0


# ---------------------------------------------------------------------------
# Public API: resolve_client_class
# ---------------------------------------------------------------------------


def resolve_client_class(client_profile: str) -> str:
    """Map a client profile name to a client class.

    Returns "full_mode" or "light_mode". Unknown profiles default to "full_mode".
    """
    return _CLIENT_CLASS_MAP.get(client_profile, "full_mode")


# ---------------------------------------------------------------------------
# WithholdingPolicy (FR03)
# ---------------------------------------------------------------------------


class WithholdingPolicy:
    """Tiered withholding policy for learning nudges (FR03).

    Determines whether a learning should be withheld from display based on
    its protection tier and the client class. Supports forced re-evaluation
    triggers including Page-Hinkley change detection (trigger #4).
    """

    def __init__(
        self,
        client_class: str = "full_mode",
        force_trial_threshold: int = 20,
    ) -> None:
        self._client_class = client_class
        self._force_trial_threshold = force_trial_threshold
        # Per-arm Page-Hinkley detectors (FR05 trigger #4)
        self._detectors: dict[str, PageHinkleyDetector] = {}

    def update_reward(self, arm_id: str, reward: float) -> bool:
        """Update the Page-Hinkley detector for an arm; returns True on alarm.

        When the detector fires, the policy notes this for the next
        should_withhold() call via forced re-evaluation trigger #4.
        """
        if arm_id not in self._detectors:
            self._detectors[arm_id] = PageHinkleyDetector()
        return self._detectors[arm_id].update(reward)

    def page_hinkley_alarm(self, arm_id: str) -> bool:
        """Return True if the Page-Hinkley detector for *arm_id* has fired.

        Note: the detector self-resets after firing, so this is a one-shot
        signal per detection event. Callers should check this after
        ``update_reward``.
        """
        # Current detector state cannot be queried for "did it just fire" --
        # the alarm is returned by update_reward() directly. This helper
        # exists so that should_withhold() can accept the alarm state from
        # the caller via the learning metadata dict.
        detector = self._detectors.get(arm_id)
        if detector is None:
            return False
        return False  # Alarm is communicated via update_reward() return value

    def should_withhold(
        self,
        learning: dict[str, object],
        *,
        page_hinkley_fired: bool = False,
    ) -> bool:
        """Decide whether to withhold a learning from nudge display.

        Steps:
        1. Get protection_tier from learning (default "normal")
        2. Check forced re-evaluation triggers (incl. Page-Hinkley)
        3. For "critical" with no forced trigger: always return False
        4. For others: pick random rate in [floor, ceiling] and compare to random()
        """
        tier = str(learning.get("protection_tier", "normal"))
        metadata = learning.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}

        # Check forced re-evaluation triggers
        forced = self._check_forced_triggers(tier, metadata, page_hinkley_fired=page_hinkley_fired)
        if forced:
            # Override to normal-tier rate regardless of actual tier
            tier = "normal"
            _logger.debug(
                "withholding_forced_trigger",
                learning_id=str(learning.get("id", "")),
                original_tier=str(learning.get("protection_tier", "normal")),
            )

        # Critical with no forced trigger: never withhold
        effective_tier = str(learning.get("protection_tier", "normal")) if not forced else "normal"
        if effective_tier in ("critical", "protected", "permanent") and not forced:
            return False

        # Look up rate range
        client_rates = _WITHHOLDING_RATES.get(self._client_class, _WITHHOLDING_RATES["full_mode"])
        floor, ceiling = client_rates.get(tier, _UNKNOWN_TIER_RATE)

        if floor == ceiling:
            rate = floor
        else:
            rate = random.uniform(floor, ceiling)

        withheld = random.random() < rate

        _logger.debug(
            "withholding_decision",
            learning_id=str(learning.get("id", "")),
            tier=tier,
            client_class=self._client_class,
            rate=round(rate, 4),
            withheld=withheld,
            forced=forced,
        )

        return withheld

    def _check_forced_triggers(
        self,
        tier: str,
        metadata: dict[str, object],
        *,
        page_hinkley_fired: bool = False,
    ) -> bool:
        """Check if any forced re-evaluation trigger fires.

        Triggers (FR03):
        1. anchor_validity dropped by >0.3 (prev_anchor_validity < 0.3)
        2. Shown in >force_trial_threshold consecutive sessions
        3. Workaround type past expires date
        4. Page-Hinkley detector fired for this learning (FR05)
        """
        # Trigger 1: anchor validity drop
        prev_validity = metadata.get("prev_anchor_validity")
        if prev_validity is not None:
            try:
                if float(str(prev_validity)) < 0.3:
                    return True
            except (ValueError, TypeError):
                pass

        # Trigger 2: consecutive shown exceeds threshold
        consecutive = metadata.get("consecutive_shown")
        if consecutive is not None:
            try:
                if int(str(consecutive)) > self._force_trial_threshold:
                    return True
            except (ValueError, TypeError):
                pass

        # Trigger 3: workaround past expires date
        entry_type = metadata.get("type")
        expires = metadata.get("expires")
        if entry_type == "workaround" and isinstance(expires, str) and expires:
            try:
                expires_dt = datetime.fromisoformat(expires).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > expires_dt:
                    return True
            except (ValueError, TypeError):
                pass

        # Trigger 4: Page-Hinkley change detection fired (FR05)
        if page_hinkley_fired:
            return True

        return False


# ---------------------------------------------------------------------------
# select_nudge_learning_bandit (FR04)
# ---------------------------------------------------------------------------


def select_nudge_learning_bandit(
    candidates: list[dict[str, object]],
    bandit: BanditSelector,
    policy: WithholdingPolicy,
    phase: str,
    previous_phase: str,
    phase_transition_withhold_rate: float = 0.10,
) -> tuple[list[dict[str, object]], bool]:
    """Select learning(s) for nudge display using bandit-based selection (FR04/FR06).

    Args:
        candidates: Ranked learning dicts (best first).
        bandit: BanditSelector instance with arm state.
        policy: WithholdingPolicy for tiered withholding.
        phase: Current ceremony phase.
        previous_phase: Previous ceremony phase (empty string if first).
        phase_transition_withhold_rate: Fraction of non-critical learnings to
            withhold at phase boundaries (FR06 configurable, default 0.10).

    Returns:
        Tuple of (selected_learnings, is_transition).
        is_transition is True if a phase transition was detected.
    """
    if not candidates:
        return [], False

    # Detect phase transition (FR06)
    is_transition = bool(phase != previous_phase and previous_phase)

    # Determine selection count: burst (2-3) at transition, else 1
    if is_transition:
        select_count = random.randint(2, 3)
    else:
        select_count = 1

    # Build candidate lookup by ID
    candidate_map: dict[str, dict[str, object]] = {}
    for c in candidates:
        cid = str(c.get("id", ""))
        if cid:
            candidate_map[cid] = c

    if not candidate_map:
        return [], is_transition

    selected: list[dict[str, object]] = []
    selected_ids: set[str] = set()

    for slot in range(select_count):
        # Get eligible IDs (not already selected)
        eligible_ids = [cid for cid in candidate_map if cid not in selected_ids]
        if not eligible_ids:
            break

        # Use bandit to select
        try:
            decision: BanditDecision = bandit.select(eligible_ids)
        except ValueError:
            break

        # Check withholding for selected candidate
        candidate = candidate_map.get(decision.selected_id)
        if candidate is None:
            break

        # FR06: At phase transitions apply extra micro-randomized withholding
        # for non-critical learnings (independent Bernoulli trial)
        if is_transition and slot > 0:
            tier = str(candidate.get("protection_tier", "normal"))
            if tier not in ("critical", "protected", "permanent"):
                if random.random() < phase_transition_withhold_rate:
                    _logger.debug(
                        "phase_transition_withholding",
                        learning_id=decision.selected_id,
                        phase=phase,
                        exploration=True,
                    )
                    # Replace withheld slot with runner-up if available
                    if decision.runner_up_id and decision.runner_up_id not in selected_ids:
                        runner_up = candidate_map.get(decision.runner_up_id)
                        if runner_up is not None:
                            selected.append(runner_up)
                            selected_ids.add(decision.runner_up_id)
                    continue

        if policy.should_withhold(candidate):
            # Try runner-up
            _logger.debug(
                "learning_withheld",
                learning_id=decision.selected_id,
                runner_up=decision.runner_up_id,
            )
            if decision.runner_up_id and decision.runner_up_id not in selected_ids:
                runner_up = candidate_map.get(decision.runner_up_id)
                if runner_up is not None and not policy.should_withhold(runner_up):
                    selected.append(runner_up)
                    selected_ids.add(decision.runner_up_id)
            # If runner-up also withheld or missing, skip this slot
        else:
            selected.append(candidate)
            selected_ids.add(decision.selected_id)

    _logger.info(
        "bandit_nudge_selection",
        phase=phase,
        previous_phase=previous_phase,
        is_transition=is_transition,
        select_count=select_count,
        selected_count=len(selected),
        selected_ids=list(selected_ids),
    )

    return selected, is_transition


# ---------------------------------------------------------------------------
# render_nudge_content (FR04)
# ---------------------------------------------------------------------------


def render_nudge_content(
    learnings: list[dict[str, object]],
    is_transition: bool,
    budget_chars: int = 320,
) -> str:
    """Render selected learnings as nudge content text.

    Uses each learning's ``nudge_line`` field if present, otherwise falls
    back to a truncated ``summary``. Phase-transition bursts get a temporarily
    expanded budget of up to 480 characters (FR04 spec).

    Args:
        learnings: Learnings selected by the bandit.
        is_transition: Whether this is a phase-transition burst.
        budget_chars: Normal per-learning character budget.

    Returns:
        Rendered nudge content string, empty if no learnings.
    """
    if not learnings:
        return ""

    # Phase-transition burst may use up to 480 chars total
    total_budget = 480 if is_transition else budget_chars
    lines: list[str] = []
    used = 0

    for learning in learnings:
        # Prefer nudge_line, fall back to truncated summary
        nudge_line = str(learning.get("nudge_line", "") or "").strip()
        if not nudge_line:
            summary = str(learning.get("summary", "") or "").strip()
            nudge_line = summary[:80] if summary else ""
        if not nudge_line:
            continue

        remaining = total_budget - used
        if remaining <= 0:
            break

        if len(nudge_line) > remaining:
            nudge_line = nudge_line[:remaining - 1] + "…"

        lines.append(nudge_line)
        used += len(nudge_line) + 1  # +1 for newline separator

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# build_context_vector (FR02)
# ---------------------------------------------------------------------------


def build_context_vector(
    phase: str = "",
    agent_type: str = "",
    task_type: str = "",
    session_progress: float = 0.0,
    domain_similarity: float = 0.0,
    files_count: int = 0,
) -> list[float]:
    """Build a 21-dimensional engineering context feature vector for LinUCB.

    Layout (21 dimensions total, per PRD-CORE-105 spec):
    - [0:6]   Phase one-hot encoding (6 phases)
    - [6:10]  Agent type one-hot encoding (4 types)
    - [10:16] Task type one-hot encoding (6 types)
    - [16]    Session progress (0.0 to 1.0)
    - [17]    Domain similarity (0.0 to 1.0)
    - [18]    Files count normalized (0.0 to 1.0)
    - [19]    Reserved (0.0)
    - [20]    Reserved (0.0)
    """
    vec: list[float] = []

    # Phase one-hot (6 dims)
    for p in _PHASES:
        vec.append(1.0 if phase == p else 0.0)

    # Agent type one-hot (4 dims) — resolve aliases first
    resolved_agent = _AGENT_ALIASES.get(agent_type, agent_type)
    for at in _AGENT_TYPES:
        vec.append(1.0 if resolved_agent == at else 0.0)

    # Task type one-hot (6 dims)
    for tt in _TASK_TYPES:
        vec.append(1.0 if task_type == tt else 0.0)

    # Session progress (clamped)
    vec.append(max(0.0, min(1.0, session_progress)))

    # Domain similarity (clamped)
    vec.append(max(0.0, min(1.0, domain_similarity)))

    # Files count normalized (clamped)
    normalized_files = min(float(files_count) / _FILES_COUNT_MAX, 1.0)
    vec.append(max(0.0, normalized_files))

    # Reserved dimensions
    vec.append(0.0)
    vec.append(0.0)

    return vec
