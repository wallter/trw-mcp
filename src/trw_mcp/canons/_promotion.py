"""Fail-closed promotion gate + comprehension evidence contract (FR08/FR09).

Belongs to the ``trw_mcp.canons.registry`` facade. Re-exported there.

The compiler and parity gates are deterministic and automated. Promotion of a
compact generation to *current* (new version defaults + compact runtime
pointers) is additionally gated on:

* deterministic structural gates (inventory, parity, runtime, byte budget);
* a **critical-scenario** structural check -- every critical lifecycle /
  evidence / review / delivery / exception / shared-worktree decision must be
  answerable from the compact core alone (deterministic proxy, FR08 automated
  portion); and
* a **comprehension receipt** whose contract this module validates (the
  stochastic paired non-inferiority evaluation + independent audit are the
  human-gated FR08 evidence -- see PRD Appendix B).

``evaluate_promotion_gates`` is fail-closed: any missing/false gate blocks and
the prior generation stays current (FR09). This module computes the gate decision; coordinated deployment performs the pointer change only after every gate is green.

Standard-library only (NFR02).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Critical decision scenarios: each requires an answer phrase present in the
# compact core so the mandatory decision is makeable from the core alone (FR08).
CRITICAL_SCENARIOS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "framework": (
        ("lifecycle-start", "First action of a session?", "trw_session_start"),
        ("delivery-gate", "May I deliver without a passing build check?", "Deliver gate (no fourth path)"),
        ("acceptable-failure", "How is an unverified delivery authorized?", "acceptable-failure record"),
        (
            "override-truthfulness",
            "Does an override make work verified?",
            "never turns unverified work into verified work",
        ),
        ("review-independence", "Who reviews STANDARD+ work?", "REVIEW is mandatory at STANDARD+"),
        ("evidence-freshness", "Can I reuse a build check after editing?", "Build evidence MUST postdate"),
        ("shared-worktree", "How do I commit in a shared worktree?", "Commit each coherent, focused, green milestone"),
        (
            "destructive-git",
            "May I run git reset/clean/stash freely?",
            "command-specific operator authorization and exclusive ownership",
        ),
        ("compaction", "What do I do after context compaction?", "After compaction"),
        ("autonomous-brakes", "When does an unattended cycle stop?", "Outcome-gated closure"),
    ),
    "aaref": (
        ("readiness-vs-closure", "Is a high validator score proof of done?", "drafting aid"),
        ("verified-closure", "What makes a requirement verified?", "Verified-closure"),
        ("status-truthfulness", "When may I mark status: implemented?", "status: implemented"),
        ("review-independence", "Can the author review their own work?", "self-certify"),
        ("verification-method", "What verification methods exist?", "Demonstration"),
        ("override-semantics", "Can an override ship known risk?", "override may deliver known risk"),
    ),
}

# PRD Appendix B: fields a signed comprehension receipt MUST carry.
REQUIRED_RECEIPT_FIELDS: tuple[str, ...] = (
    "source_digests",
    "core_digest",
    "reference_digest",
    "combined_digest",
    "prompt_digest",
    "corpus_digest",
    "preregistration_timestamp",
    "strata",
    "raw_outcomes",
    "critical_accuracy_by_arm",
    "critical_accuracy_by_stratum",
    "negative_control_outcomes",
    "negative_control_accuracy",
    "parser_failures",
    "operational_cost_by_arm",
    "paired_effect",
    "bootstrap_ci_lower",
    "independent_reviewer",
    "claim_scope",
)

# Every deterministic + behavioral + human gate that must be green to promote.
PROMOTION_GATES: tuple[str, ...] = (
    "inventory_complete",
    "deterministic_compilation",
    "source_parity",
    "runtime_integrity",
    "byte_budget",
    "critical_comprehension",
    "overall_non_inferiority",
    "no_failing_stratum",
    "independent_audit",
    "build_evidence",
    "backward_compatibility",
)

# Non-inferiority floor: compact-minus-combined 95% CI lower bound (FR08/NFR).
NON_INFERIORITY_FLOOR = -0.05


@dataclass(frozen=True)
class PromotionDecision:
    """Fail-closed promotion verdict; ``promote`` only when nothing blocks."""

    promote: bool
    blocking: tuple[str, ...]


def scenario_failures(canon_id: str, core_text: str) -> tuple[str, ...]:
    """Critical scenarios whose mandatory answer is NOT in the compact core (FR08)."""
    scenarios = CRITICAL_SCENARIOS.get(canon_id)
    if scenarios is None:
        raise KeyError(f"no critical scenarios registered for canon: {canon_id}")
    return tuple(sid for sid, _q, answer in scenarios if answer not in core_text)


def critical_scenarios_pass(canon_id: str, core_text: str) -> bool:
    """True when 100% of critical scenarios are answerable from the core alone."""
    return not scenario_failures(canon_id, core_text)


def validate_comprehension_receipt(receipt: Mapping[str, object]) -> list[str]:
    """Return contract violations for a comprehension receipt (empty == valid).

    Checks required fields, the pre-registered sampling design, discriminating
    negative controls, independent review, 100% critical accuracy in every
    stratum, and the paired non-inferiority bound (FR08). Absence never passes.
    """
    errors: list[str] = [
        f"missing required receipt field: {field}" for field in REQUIRED_RECEIPT_FIELDS if field not in receipt
    ]

    accuracy = receipt.get("critical_accuracy_by_arm")
    if isinstance(accuracy, Mapping):
        if not accuracy:
            errors.append("critical_accuracy_by_arm is empty; every arm must report 100%")
        for arm, value in accuracy.items():
            if not isinstance(value, (int, float)) or float(value) < 1.0:
                errors.append(f"critical accuracy in arm {arm!r} is {value!r}; must be 1.0 (100%)")
    elif "critical_accuracy_by_arm" in receipt:
        errors.append("critical_accuracy_by_arm must be a mapping of arm -> accuracy")

    stratum_accuracy = receipt.get("critical_accuracy_by_stratum")
    if isinstance(stratum_accuracy, Mapping):
        if not stratum_accuracy:
            errors.append("critical_accuracy_by_stratum is empty")
        for stratum, value in stratum_accuracy.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) < 1.0:
                errors.append(f"critical accuracy in stratum {stratum!r} is {value!r}; must be 1.0 (100%)")
    elif "critical_accuracy_by_stratum" in receipt:
        errors.append("critical_accuracy_by_stratum must be a mapping")

    strata = receipt.get("strata")
    adapters: list[str] = []
    repeats = 0
    if isinstance(strata, Mapping):
        raw_adapters = strata.get("adapters")
        if isinstance(raw_adapters, list) and all(isinstance(item, str) and item for item in raw_adapters):
            adapters = list(dict.fromkeys(raw_adapters))
            if len(adapters) < 2:
                errors.append("strata.adapters must contain at least two independent client adapters")
        else:
            errors.append("strata.adapters must be a non-empty string list")
        raw_repeats = strata.get("repeats")
        if isinstance(raw_repeats, int) and not isinstance(raw_repeats, bool):
            repeats = raw_repeats
            if repeats < 3:
                errors.append("strata.repeats must be at least 3 per stratum")
        else:
            errors.append("strata.repeats must be an integer")
        profiles = strata.get("profiles")
        if not isinstance(profiles, Mapping) or not {"frontier", "balanced"} <= {
            value for value in profiles.values() if isinstance(value, str)
        }:
            errors.append("strata.profiles must include frontier and balanced profiles")
        elif set(adapters) != {key for key in profiles if isinstance(key, str)}:
            errors.append("strata.profiles keys must exactly match strata.adapters")
    elif "strata" in receipt:
        errors.append("strata must be a mapping")

    outcomes = receipt.get("raw_outcomes")
    if isinstance(outcomes, list) and outcomes:
        seen_repeats: dict[tuple[str, str, str], set[int]] = {}
        for row in outcomes:
            if not isinstance(row, Mapping):
                errors.append("raw_outcomes entries must be mappings")
                continue
            canon, arm, adapter, repeat = (row.get(name) for name in ("canon", "arm", "adapter", "repeat"))
            if (
                not isinstance(canon, str)
                or not canon
                or not isinstance(arm, str)
                or not arm
                or not isinstance(adapter, str)
                or not adapter
                or not isinstance(repeat, int)
                or isinstance(repeat, bool)
            ):
                errors.append("raw_outcomes entry has invalid canon/arm/adapter/repeat")
                continue
            if not isinstance(row.get("correct"), bool):
                errors.append("raw_outcomes entry correct must be boolean")
            seen_repeats.setdefault((canon, arm, adapter), set()).add(repeat)
        if repeats and any(len(values) < repeats for values in seen_repeats.values()):
            errors.append("raw_outcomes do not contain the declared repeats for every observed stratum")
        expected_pairs = {
            (canon, arm, adapter)
            for canon in ("framework", "aaref")
            for arm in ("compact", "combined")
            for adapter in adapters
        }
        if adapters and set(seen_repeats) != expected_pairs:
            errors.append("raw_outcomes do not cover every canon/arm/adapter stratum")
    elif "raw_outcomes" in receipt:
        errors.append("raw_outcomes must be a non-empty list")

    controls = receipt.get("negative_control_outcomes")
    if (not isinstance(controls, list) or not controls) and "negative_control_outcomes" in receipt:
        errors.append("negative_control_outcomes must be a non-empty list")
    control_accuracy = receipt.get("negative_control_accuracy")
    if isinstance(control_accuracy, (int, float)) and not isinstance(control_accuracy, bool):
        if not 0.0 <= float(control_accuracy) <= 0.25:
            errors.append("negative_control_accuracy must be between 0.0 and 0.25")
    elif "negative_control_accuracy" in receipt:
        errors.append("negative_control_accuracy must be a number")

    parser_failures = receipt.get("parser_failures")
    if isinstance(parser_failures, list):
        if parser_failures:
            errors.append("parser_failures must be empty")
    elif "parser_failures" in receipt:
        errors.append("parser_failures must be a list")

    costs = receipt.get("operational_cost_by_arm")
    if isinstance(costs, Mapping):
        if set(costs) != {"compact", "combined"}:
            errors.append("operational_cost_by_arm must contain compact and combined")
        for arm, value in costs.items():
            if not isinstance(value, Mapping):
                errors.append(f"operational cost in arm {arm!r} must be a mapping")
                continue
            for field in ("request_count", "mean_latency_seconds", "mean_context_bytes"):
                metric = value.get(field)
                if not isinstance(metric, (int, float)) or isinstance(metric, bool) or float(metric) <= 0:
                    errors.append(f"operational cost {arm!r}.{field} must be positive")
            if not isinstance(value.get("token_usage"), str) or not value.get("token_usage"):
                errors.append(f"operational cost {arm!r}.token_usage must disclose availability")
    elif "operational_cost_by_arm" in receipt:
        errors.append("operational_cost_by_arm must be a mapping")

    reviewer = receipt.get("independent_reviewer")
    if isinstance(reviewer, str):
        if not reviewer.strip() or any(marker in reviewer.casefold() for marker in ("pending", "tbd", "self-review")):
            errors.append("independent_reviewer must identify a completed independent audit")
    elif "independent_reviewer" in receipt:
        errors.append("independent_reviewer must be a string")

    lower = receipt.get("bootstrap_ci_lower")
    if isinstance(lower, (int, float)) and not isinstance(lower, bool):
        if float(lower) < NON_INFERIORITY_FLOOR:
            errors.append(f"non-inferiority lower bound {lower} < floor {NON_INFERIORITY_FLOOR}; blocks promotion")
    elif "bootstrap_ci_lower" in receipt:
        errors.append("bootstrap_ci_lower must be a number")
    return errors


def evaluate_promotion_gates(gates: Mapping[str, bool]) -> PromotionDecision:
    """Fail-closed promotion decision: any missing or false gate blocks (FR09).

    The prior generation stays current unless EVERY gate in ``PROMOTION_GATES``
    is explicitly present and true.
    """
    blocking = tuple(name for name in PROMOTION_GATES if not gates.get(name, False))
    return PromotionDecision(promote=not blocking, blocking=blocking)


__all__ = [
    "CRITICAL_SCENARIOS",
    "NON_INFERIORITY_FLOOR",
    "PROMOTION_GATES",
    "REQUIRED_RECEIPT_FIELDS",
    "PromotionDecision",
    "critical_scenarios_pass",
    "evaluate_promotion_gates",
    "scenario_failures",
    "validate_comprehension_receipt",
]
