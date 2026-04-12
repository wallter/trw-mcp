"""Bandit-based learning selection policy for the nudge system.

PRD-CORE-105 FR03/FR04/FR06: Tiered withholding, phase-transition bursts,
and micro-randomized withholding at phase boundaries.

Engineering workflow policy — sits on top of trw-memory bandit primitives.
Local-first behavior per Vision Principle 6: no backend connection needed.
"""

from __future__ import annotations

import contextlib
import json
import os
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

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
_CONTEXTUAL_STATE_MAX_ARMS = 12
_DETECTOR_STATE_KEYS: tuple[str, ...] = (
    "n",
    "sum",
    "h",
    "m",
    "h_down",
    "m_down",
)
_DETECTOR_PARAM_KEYS: tuple[str, ...] = (
    "delta",
    "alarm_threshold",
    "forgetting_factor",
    "min_observations",
    "reward_scale",
)
_DETECTOR_DEFAULT_STATE: dict[str, int | float | None] = PageHinkleyDetector().to_dict()


def _default_state_value(key: str) -> int | float | None:
    """Return the default detector value for a compact-state key."""
    return _DETECTOR_DEFAULT_STATE[key]


def _matches_default(value: object, default: object) -> bool:
    """Best-effort equality check for compaction of numeric defaults."""
    if value is None or default is None:
        return value is default
    try:
        return float(value) == float(default)
    except (TypeError, ValueError):
        return value == default


def _trim_trailing_default_keys(
    keys: tuple[str, ...],
    values: list[int | float | None],
) -> list[int | float | None]:
    """Trim trailing detector fields that are still at default values."""
    trimmed = list(values)
    while trimmed:
        key = keys[len(trimmed) - 1]
        if not _matches_default(trimmed[-1], _default_state_value(key)):
            break
        trimmed.pop()
    return trimmed


# ---------------------------------------------------------------------------
# Public API: resolve_client_class
# ---------------------------------------------------------------------------


def resolve_client_class(client_profile: str) -> str:
    """Map a client profile name to a client class.

    Returns "full_mode" or "light_mode". Unknown profiles default to "full_mode".
    """
    return _CLIENT_CLASS_MAP.get(client_profile, "full_mode")


# ---------------------------------------------------------------------------
# Bandit state envelope load/save (C-5 Model Generation Preparedness)
# ---------------------------------------------------------------------------


def _compute_heuristic_reward(learning: dict[str, object]) -> float:
    """Compute a heuristic reward from a learning's evidence quality (P0 fix).

    Prefers the ``impact`` field (0.0-1.0) so higher-impact learnings
    converge faster.  Falls back to ``score`` / ``utility_score``, then
    neutral 0.5 so Thompson sampling still learns without impact data.

    Using a deterministic heuristic tied to evidence quality gives the
    bandit a better signal than a constant 0.7 reward -- better learnings
    will therefore converge to higher selection probability over time.
    """
    for key in ("impact", "score", "utility_score"):
        raw = learning.get(key)
        if raw is not None:
            try:
                r = float(str(raw))
                return max(0.0, min(1.0, r))
            except (ValueError, TypeError):
                pass
    return 0.5  # Neutral fallback when no evidence quality signal available


def load_bandit_state(
    trw_dir: Path,
    client_profile: str = "full_mode",
    model_family: str = "",
) -> BanditSelector:
    """Load bandit state from the C-5 spec-compliant envelope.

    Handles three cases automatically:
    1. Missing file → returns fresh BanditSelector with Beta(2,1) priors.
    2. Legacy raw state (``arms`` at top level, no ``bandit_state`` key) →
       migrates in-memory to the envelope format.
    3. New ``model_family`` that doesn't match the stored key → quarantines
       existing posteriors under ``quarantined[old_model_family]`` and
       returns a fresh BanditSelector for the new model family (C-5).

    Args:
        trw_dir: Path to the ``.trw`` directory.
        client_profile: Current client class (e.g. ``"full_mode"``).
        model_family: Current model family string (e.g. ``"claude-sonnet-4"``).
            Empty string disables quarantine check.

    Returns:
        A BanditSelector with restored arm posteriors, or a fresh one.
    """
    bandit_state_path = trw_dir / "meta" / "bandit_state.json"
    if not bandit_state_path.exists():
        return BanditSelector()

    try:
        raw_text = bandit_state_path.read_text(encoding="utf-8")
        data: object = json.loads(raw_text)
        if not isinstance(data, dict):
            _logger.warning("bandit_state_bad_format", path=str(bandit_state_path))
            return BanditSelector()

        # Migration: legacy raw state has ``arms`` at top level without envelope
        if "arms" in data and "bandit_state" not in data:
            _logger.info(
                "bandit_state_migrate_legacy",
                arm_count=len(data.get("arms", {})),  # type: ignore[union-attr]
            )
            data = {
                "client_profile": client_profile,
                "model_family": model_family,
                "bandit_state": dict(data),  # type: ignore[arg-type]
                "quarantined": {},
            }

        # C-5: Quarantine on model_family mismatch
        stored_model_family: str = str(data.get("model_family", ""))
        if (
            model_family
            and stored_model_family
            and model_family != stored_model_family
        ):
            old_bandit_raw = data.get("bandit_state", {})
            _logger.info(
                "bandit_model_family_quarantine",
                old_model_family=stored_model_family,
                new_model_family=model_family,
                quarantined_arm_count=len(
                    old_bandit_raw.get("arms", {})  # type: ignore[union-attr]
                    if isinstance(old_bandit_raw, dict) else {}
                ),
            )
            # Return fresh selector; save_bandit_state will persist quarantine
            return BanditSelector()

        bandit_raw = data.get("bandit_state", {})
        if isinstance(bandit_raw, dict) and bandit_raw:
            return BanditSelector.from_json(json.dumps(bandit_raw))
        return BanditSelector()

    except Exception:  # justified: fail-open, corrupt state → fresh start
        _logger.warning("bandit_state_load_failed", exc_info=True)
        return BanditSelector()


def load_contextual_bandit_state(
    trw_dir: Path,
    *,
    model_family: str = "",
    feature_dim: int = ENGINEERING_CONTEXT_DIM,
) -> "object | None":
    """Load compact contextual selector state from the shared envelope."""
    try:
        from trw_memory.bandit import ContextualBanditSelector
    except Exception:  # justified: optional dependency path must fail-open
        return None

    bandit_state_path = trw_dir / "meta" / "bandit_state.json"
    if not bandit_state_path.exists():
        return ContextualBanditSelector(feature_dim=feature_dim)

    try:
        data: object = json.loads(bandit_state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ContextualBanditSelector(feature_dim=feature_dim)

        stored_model_family = str(data.get("model_family", ""))
        if model_family and stored_model_family and model_family != stored_model_family:
            return ContextualBanditSelector(feature_dim=feature_dim)

        contextual_raw = data.get("contextual_state")
        if isinstance(contextual_raw, dict) and contextual_raw:
            return ContextualBanditSelector.from_compact_dict(contextual_raw)
    except Exception:  # justified: fail-open
        _logger.warning("contextual_state_load_failed", exc_info=True)

    return ContextualBanditSelector(feature_dim=feature_dim)


def load_bandit_state_and_policy(
    trw_dir: Path,
    client_class: str = "full_mode",
    model_family: str = "",
) -> "tuple[BanditSelector, WithholdingPolicy]":
    """Load bandit state and a pre-populated :class:`WithholdingPolicy`.

    Reads the C-5 envelope once for the BanditSelector and, when the
    ``model_family`` matches the stored value, also restores the per-arm
    Page-Hinkley detector states into the policy.  This makes forced
    trigger #4 (FR05) reachable in production across sessions instead of
    always starting from a blank detector.

    On *any* mismatch (missing file, corrupt JSON, model-family change) the
    function returns a fresh selector and a fresh policy — consistent with
    the fail-open contract throughout the nudge system.

    Args:
        trw_dir: Path to the ``.trw`` directory.
        client_class: Current client class (e.g. ``"full_mode"``).
        model_family: Current model family tag; used for quarantine check.

    Returns:
        ``(bandit, policy)`` tuple ready for use in the live nudge path.
    """
    # Delegate BanditSelector restoration (includes quarantine logic)
    bandit = load_bandit_state(trw_dir, client_class, model_family)

    bandit_state_path = trw_dir / "meta" / "bandit_state.json"
    if not bandit_state_path.exists():
        return bandit, _WithholdingPolicyFactory(client_class)

    try:
        data: object = json.loads(bandit_state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return bandit, _WithholdingPolicyFactory(client_class)

        # Only restore detector states when model_family matches —
        # a changed model may have different arm semantics.
        stored_mf = str(data.get("model_family", ""))
        if model_family and stored_mf and model_family != stored_mf:
            _logger.debug(
                "detector_states_skipped_model_mismatch",
                stored=stored_mf,
                current=model_family,
            )
            return bandit, _WithholdingPolicyFactory(client_class)

        policy_inst = _WithholdingPolicyFactory(client_class)
        detector_states = data.get("detector_states")
        if isinstance(detector_states, dict) and detector_states:
            policy_inst.load_detector_states(detector_states)
            _logger.debug(
                "detector_states_restored",
                arm_count=len(detector_states),
                model_family=model_family,
            )
        policy_inst.load_pending_alarm_ids(data.get("pending_alarm_ids", []))
        policy_inst.load_anchor_validity_state(data.get("anchor_validity_state", {}))
        return bandit, policy_inst

    except Exception:  # justified: fail-open
        _logger.warning("detector_states_load_failed", exc_info=True)
        return bandit, _WithholdingPolicyFactory(client_class)


def _WithholdingPolicyFactory(client_class: str) -> "WithholdingPolicy":
    """Return a fresh WithholdingPolicy; deferred to avoid forward-reference."""
    return WithholdingPolicy(client_class=client_class)


def _compact_detector_state(
    state: dict[str, int | float | None],
) -> list[int | float | None] | dict[str, list[int | float | None]]:
    """Persist detector state in a compact envelope-friendly shape."""
    state_values = _trim_trailing_default_keys(
        _DETECTOR_STATE_KEYS,
        [state.get(key, _DETECTOR_DEFAULT_STATE[key]) for key in _DETECTOR_STATE_KEYS],
    )
    param_values = _trim_trailing_default_keys(
        _DETECTOR_PARAM_KEYS,
        [state.get(key, _DETECTOR_DEFAULT_STATE[key]) for key in _DETECTOR_PARAM_KEYS],
    )
    uses_default_params = all(
        param == _DETECTOR_DEFAULT_STATE[key]
        for key, param in zip(_DETECTOR_PARAM_KEYS, param_values, strict=False)
    )
    if uses_default_params:
        return state_values
    return {
        "s": state_values,
        "p": param_values,
    }


def _expand_detector_state(raw: object) -> dict[str, int | float | None]:
    """Restore compact detector state shapes to Page-Hinkley dict format."""
    if isinstance(raw, list):
        restored = dict(_DETECTOR_DEFAULT_STATE)
        for key, value in zip(_DETECTOR_STATE_KEYS, raw, strict=False):
            restored[key] = value
        return restored

    if isinstance(raw, dict) and ("s" in raw or "p" in raw):
        restored = dict(_DETECTOR_DEFAULT_STATE)
        state_values = raw.get("s")
        if isinstance(state_values, list):
            for key, value in zip(_DETECTOR_STATE_KEYS, state_values, strict=False):
                restored[key] = value
        param_values = raw.get("p")
        if isinstance(param_values, list):
            for key, value in zip(_DETECTOR_PARAM_KEYS, param_values, strict=False):
                restored[key] = value
        return restored

    if isinstance(raw, dict):
        return raw

    return dict(_DETECTOR_DEFAULT_STATE)


def save_bandit_state(
    trw_dir: Path,
    bandit: BanditSelector,
    client_profile: str = "full_mode",
    model_family: str = "",
    policy: "WithholdingPolicy | None" = None,
    contextual_bandit: "object | None" = None,
) -> None:
    """Persist bandit state with C-5 spec-compliant envelope using atomic write.

    Envelope format::

        {
          "client_profile": "full_mode",
          "model_family": "claude-sonnet-4",
          "bandit_state": { ...raw BanditSelector JSON... },
          "quarantined": { "old-model-family": { ...old state... } },
          "detector_states": { "arm-id": { ...PageHinkleyDetector.to_dict()... } },
          "pending_alarm_ids": ["arm-id"],
          "contextual_state": { ...compact ContextualBanditSelector state... }
        }

    Uses the temp-file + ``os.rename`` pattern for atomic writes.

    When the stored ``model_family`` differs from the current one, the old
    ``bandit_state`` is automatically moved to ``quarantined`` so no arm
    data is lost (C-5 requirement).

    Args:
        trw_dir: Path to the ``.trw`` directory.
        bandit: BanditSelector whose state to persist.
        client_profile: Current client class tag.
        model_family: Current model family tag.
        policy: Optional :class:`WithholdingPolicy` whose per-arm
            Page-Hinkley detector states are serialised into the envelope
            under ``detector_states``.  When ``None`` no detector states
            are written (backwards-compatible).
    """
    bandit_state_path = trw_dir / "meta" / "bandit_state.json"
    tmp_path = bandit_state_path.with_suffix(f".tmp.{os.getpid()}")

    # Preserve existing quarantined data; handle model_family migration on save
    existing_quarantined: dict[str, object] = {}
    existing_contextual_state: object = {}
    model_mismatch_on_save = False
    if bandit_state_path.exists():
        try:
            existing_data: object = json.loads(
                bandit_state_path.read_text(encoding="utf-8")
            )
            if isinstance(existing_data, dict):
                raw_q = existing_data.get("quarantined", {})
                if isinstance(raw_q, dict):
                    existing_quarantined = raw_q
                raw_contextual = existing_data.get("contextual_state", {})
                if isinstance(raw_contextual, dict):
                    existing_contextual_state = raw_contextual

                # C-5: If saving under a new model_family, quarantine old state
                stored_mf = str(existing_data.get("model_family", ""))
                if model_family and stored_mf and model_family != stored_mf:
                    model_mismatch_on_save = True
                    old_bandit_raw = existing_data.get("bandit_state", {})
                    old_contextual_raw = existing_data.get("contextual_state", {})
                    if old_bandit_raw and isinstance(old_bandit_raw, dict):
                        existing_quarantined = dict(existing_quarantined)
                        quarantined_payload: object = old_bandit_raw
                        if isinstance(old_contextual_raw, dict) and old_contextual_raw:
                            quarantined_payload = {
                                "bandit_state": old_bandit_raw,
                                "contextual_state": old_contextual_raw,
                            }
                        existing_quarantined[stored_mf] = quarantined_payload
                        _logger.debug(
                            "bandit_state_quarantine_on_save",
                            old_model_family=stored_mf,
                            new_model_family=model_family,
                        )
        except Exception:  # justified: fail-open on read
            pass

    bandit_raw: object = json.loads(bandit.to_json())
    envelope: dict[str, object] = {
        "client_profile": client_profile,
        "model_family": model_family,
        "bandit_state": bandit_raw,
        "quarantined": existing_quarantined,
        "detector_states": policy.get_detector_states() if policy is not None else {},
        "pending_alarm_ids": policy.get_pending_alarm_ids() if policy is not None else [],
        "anchor_validity_state": (
            policy.get_anchor_validity_state() if policy is not None else {}
        ),
        "contextual_state": (
            contextual_bandit.to_compact_dict(max_arms=_CONTEXTUAL_STATE_MAX_ARMS)
            if contextual_bandit is not None and hasattr(contextual_bandit, "to_compact_dict")
            else ({} if model_mismatch_on_save else existing_contextual_state)
        ),
    }

    try:
        bandit_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(envelope, separators=(",", ":")),
            encoding="utf-8",
        )
        os.rename(tmp_path, bandit_state_path)
    except Exception:  # justified: persist failure must not block nudge
        _logger.debug("bandit_state_save_failed", exc_info=True)
        with contextlib.suppress(Exception):
            tmp_path.unlink()



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
        self._pending_alarm_ids: set[str] = set()
        self._anchor_validity_priors: dict[str, float] = {}

    def update_reward(self, arm_id: str, reward: float) -> bool:
        """Update the Page-Hinkley detector for an arm; returns True on alarm.

        When the detector fires, the policy notes this for the next
        should_withhold() call via forced re-evaluation trigger #4.
        """
        if arm_id not in self._detectors:
            self._detectors[arm_id] = PageHinkleyDetector()
        fired = self._detectors[arm_id].update(reward)
        if fired:
            self._pending_alarm_ids.add(arm_id)
        return fired

    def get_detector_states(self) -> dict[str, object]:
        """Serialize non-empty Page-Hinkley states in a compact JSON shape."""
        persisted: dict[str, object] = {}
        for arm_id, det in self._detectors.items():
            state = det.to_dict()
            observations = state.get("n")
            if observations is None or int(observations) <= 0:
                continue
            persisted[arm_id] = _compact_detector_state(state)
        return persisted

    def load_detector_states(self, states: dict[str, object]) -> None:
        """Restore per-arm Page-Hinkley detector states from serialized dicts.

        Silently skips malformed entries so the policy stays fail-open. Both
        legacy verbose dicts and compact envelope shapes are accepted.
        """
        if not isinstance(states, dict):
            return
        for arm_id, raw in states.items():
            if isinstance(raw, (dict, list)):
                with contextlib.suppress(Exception):
                    self._detectors[str(arm_id)] = PageHinkleyDetector.from_dict(
                        _expand_detector_state(raw)
                    )

    def get_pending_alarm_ids(self) -> list[str]:
        """Return pending FR05 alarms that must be consumed on next selection."""
        return sorted(self._pending_alarm_ids)

    def load_pending_alarm_ids(self, pending_alarm_ids: object) -> None:
        """Restore pending FR05 alarms from persisted state."""
        if not isinstance(pending_alarm_ids, list):
            return
        self._pending_alarm_ids = {
            str(arm_id) for arm_id in pending_alarm_ids if str(arm_id)
        }

    def get_anchor_validity_state(self) -> dict[str, float]:
        """Serialize prior anchor-validity observations for trigger #1."""
        return {
            arm_id: round(validity, 4)
            for arm_id, validity in self._anchor_validity_priors.items()
        }

    def load_anchor_validity_state(self, state: object) -> None:
        """Restore persisted anchor-validity priors."""
        if not isinstance(state, dict):
            return
        restored: dict[str, float] = {}
        for arm_id, value in state.items():
            with contextlib.suppress(TypeError, ValueError):
                restored[str(arm_id)] = max(0.0, min(1.0, float(value)))
        self._anchor_validity_priors = restored

    def consume_pending_alarm(self, arm_id: str) -> bool:
        """Consume a one-shot FR05 forced re-evaluation alarm for *arm_id*."""
        if arm_id in self._pending_alarm_ids:
            self._pending_alarm_ids.remove(arm_id)
            return True
        return False

    def page_hinkley_alarm(self, arm_id: str) -> bool:
        """Return True if the Page-Hinkley detector for *arm_id* has fired.

        Note: the detector self-resets after firing, so this is a one-shot
        signal per detection event. Callers should check this after
        ``update_reward``.
        """
        return arm_id in self._pending_alarm_ids

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
        learning_id = str(learning.get("id", ""))
        metadata = learning.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        if "anchor_validity" in learning and "anchor_validity" not in metadata:
            metadata = dict(metadata)
            metadata["anchor_validity"] = learning.get("anchor_validity")
        current_anchor_validity = self._extract_anchor_validity(metadata)
        previous_anchor_validity = metadata.get("prev_anchor_validity")
        if previous_anchor_validity is None and learning_id:
            previous_anchor_validity = self._anchor_validity_priors.get(learning_id)

        # Check forced re-evaluation triggers
        pending_alarm = self.consume_pending_alarm(learning_id) if learning_id else False
        forced = self._check_forced_triggers(
            tier,
            metadata,
            previous_anchor_validity=previous_anchor_validity,
            current_anchor_validity=current_anchor_validity,
            page_hinkley_fired=(page_hinkley_fired or pending_alarm),
        )
        if learning_id and current_anchor_validity is not None:
            self._anchor_validity_priors[learning_id] = current_anchor_validity
        if forced:
            # Override to normal-tier rate regardless of actual tier
            tier = "normal"
            _logger.debug(
                "withholding_forced_trigger",
                learning_id=learning_id,
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
        previous_anchor_validity: object = None,
        current_anchor_validity: float | None = None,
        page_hinkley_fired: bool = False,
    ) -> bool:
        """Check if any forced re-evaluation trigger fires.

        Triggers (FR03):
        1. anchor_validity dropped by >0.3 since the prior evaluation
        2. Shown in >force_trial_threshold consecutive sessions
        3. Workaround type past expires date
        4. Page-Hinkley detector fired for this learning (FR05)
        """
        # Trigger 1: anchor validity drop
        if previous_anchor_validity is not None and current_anchor_validity is not None:
            try:
                previous = float(str(previous_anchor_validity))
                if (previous - current_anchor_validity) > 0.3:
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

    @staticmethod
    def _extract_anchor_validity(metadata: dict[str, object]) -> float | None:
        """Best-effort parse of the current anchor-validity score."""
        for key in ("anchor_validity", "current_anchor_validity"):
            raw_value = metadata.get(key)
            if raw_value is None:
                continue
            with contextlib.suppress(TypeError, ValueError):
                return max(0.0, min(1.0, float(str(raw_value))))
        return None


# ---------------------------------------------------------------------------
# select_nudge_learning_bandit (FR04)
# ---------------------------------------------------------------------------


class WithheldEvent(TypedDict):
    """Metadata for a learning withheld at phase-transition (FR06/P1-D).

    Written to propensity.jsonl with ``withheld=True`` so treatment vs
    control can be distinguished in downstream causal analysis.
    """

    learning_id: str
    selection_probability: float
    runner_up_id: str
    exploration: bool
    slot: int
    phase: str


def _select_live_decision(
    eligible_ids: list[str],
    *,
    bandit: BanditSelector,
    contextual_selector: object | None = None,
    context_vector: list[float] | None = None,
) -> BanditDecision:
    """Select the next learning using contextual state when live context exists."""
    if contextual_selector is not None and context_vector:
        select_decision = getattr(contextual_selector, "select_decision", None)
        if callable(select_decision):
            return select_decision(eligible_ids, context_vector=context_vector)
    return bandit.select(eligible_ids)


def select_nudge_learning_bandit(
    candidates: list[dict[str, object]],
    bandit: BanditSelector,
    policy: WithholdingPolicy,
    phase: str,
    previous_phase: str,
    phase_transition_withhold_rate: float = 0.10,
    decisions_out: list[BanditDecision] | None = None,
    withheld_events_out: list[WithheldEvent] | None = None,
    contextual_selector: object | None = None,
    context_vector: list[float] | None = None,
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
        decisions_out: Optional list to receive one ``BanditDecision`` per
            shown learning. Runner-up substitutions are normalized so callers
            can log the actual surfaced item, not just the primary raw pick.
        withheld_events_out: Optional list to receive ``WithheldEvent`` entries
            for every candidate withheld via FR06 micro-randomised withholding.
            Callers log these to propensity.jsonl with ``withheld=True`` (P1-D).

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

        # Use contextual selection when live context is available.
        try:
            decision = _select_live_decision(
                eligible_ids,
                bandit=bandit,
                contextual_selector=contextual_selector,
                context_vector=context_vector,
            )
        except ValueError:
            break

        # Check withholding for selected candidate
        candidate = candidate_map.get(decision.selected_id)
        if candidate is None:
            break

        # FR06: At phase transitions apply extra micro-randomized withholding
        # for non-critical learnings (independent Bernoulli trial).
        # Applies to ALL slots including slot 0 (primary burst slot) — P1-B fix.
        if is_transition:
            tier = str(candidate.get("protection_tier", "normal"))
            if tier not in ("critical", "protected", "permanent"):
                if random.random() < phase_transition_withhold_rate:
                    _logger.debug(
                        "phase_transition_withholding",
                        learning_id=decision.selected_id,
                        slot=slot,
                        phase=phase,
                        exploration=True,
                    )
                    # Record withheld event for propensity logging (P1-D)
                    if withheld_events_out is not None:
                        withheld_events_out.append(
                            WithheldEvent(
                                learning_id=decision.selected_id,
                                selection_probability=decision.selection_probability,
                                runner_up_id=decision.runner_up_id or "",
                                exploration=True,
                                slot=slot,
                                phase=phase,
                            )
                        )
                    # Replace withheld slot with runner-up if available
                    if decision.runner_up_id and decision.runner_up_id not in selected_ids:
                        runner_up = candidate_map.get(decision.runner_up_id)
                        if runner_up is not None:
                            selected.append(runner_up)
                            selected_ids.add(decision.runner_up_id)
                            if decisions_out is not None:
                                decisions_out.append(
                                    BanditDecision(
                                        selected_id=decision.runner_up_id,
                                        selection_probability=decision.runner_up_probability or 0.0,
                                        runner_up_id=decision.selected_id,
                                        runner_up_probability=decision.selection_probability,
                                        exploration=True,
                                    )
                                )
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
                    if decisions_out is not None:
                        decisions_out.append(
                            BanditDecision(
                                selected_id=decision.runner_up_id,
                                selection_probability=decision.runner_up_probability or 0.0,
                                runner_up_id=decision.selected_id,
                                runner_up_probability=decision.selection_probability,
                                exploration=True,
                            )
                        )
            # If runner-up also withheld or missing, skip this slot
        else:
            selected.append(candidate)
            selected_ids.add(decision.selected_id)
            if decisions_out is not None:
                decisions_out.append(decision)

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
     - [6]     Domain similarity (0.0 to 1.0)
     - [7:11]  Agent type one-hot encoding (4 types)
     - [11:17] Task type one-hot encoding (6 types)
     - [17]    Files count normalized (0.0 to 1.0)
     - [18:21] Session progress one-hot (early, mid, late)
    """
    vec: list[float] = []

    # Phase one-hot (6 dims)
    for p in _PHASES:
        vec.append(1.0 if phase == p else 0.0)

    # Domain similarity (clamped)
    vec.append(max(0.0, min(1.0, domain_similarity)))

    # Agent type one-hot (4 dims) — resolve aliases first
    resolved_agent = _AGENT_ALIASES.get(agent_type, agent_type)
    for at in _AGENT_TYPES:
        vec.append(1.0 if resolved_agent == at else 0.0)

    # Task type one-hot (6 dims)
    for tt in _TASK_TYPES:
        vec.append(1.0 if task_type == tt else 0.0)

    # Files count normalized (clamped)
    normalized_files = min(float(files_count) / _FILES_COUNT_MAX, 1.0)
    vec.append(max(0.0, normalized_files))

    # Session progress one-hot (early, mid, late)
    clamped_progress = max(0.0, min(1.0, session_progress))
    if clamped_progress < (1.0 / 3.0):
        vec.extend([1.0, 0.0, 0.0])
    elif clamped_progress < (2.0 / 3.0):
        vec.extend([0.0, 1.0, 0.0])
    else:
        vec.extend([0.0, 0.0, 1.0])

    return vec
