# Parent facade: tools/_delivery_helpers.py
"""PRD ``status -> implemented`` transition detection + coherence gate (FR04/FR05).

Sibling of ``_prd_transition_gate``'s consumers; re-exported through
``_delivery_helpers.py``. Invoked from the deliver-gate dispatch
(``_deliver_gate_dispatch.py``) ONLY when ``deliver_gate_mode`` resolves to a
block posture for the run's ``task_type`` AND ``prd_transition_gate`` is set.

Detection is a path-limited ``git diff -- docs/requirements-aare-f/prds/`` so the
cost is bounded by the PRD directory, not the whole tree (NFR03). No transition
detected => no new gating (pure additive, brownfield-safe). Every resolution
error degrades to pre-existing behavior — never a spurious hard block (NFR02).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.gate_decision import EffectiveCompletionDecision
    from trw_mcp.models.requirements import ActivationGate

import structlog

from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)

# The PRD directory the path-limited diff is scoped to (FR04 assertion anchor).
PRDS_DIR = "docs/requirements-aare-f/prds"

# An ADDED frontmatter line moving status into the implemented family. The
# implemented-family aliases mirror the truthfulness-ratchet set.
_ADDED_STATUS_RE = re.compile(
    r"^\+\s*status:\s*(implemented|done|delivered|complete)\b",
    re.IGNORECASE,
)
# Unified-diff new-file header: ``+++ b/<path>`` (or ``+++ <path>``).
_PLUS_FILE_RE = re.compile(r"^\+\+\+\s+(?:b/)?(.+?)\s*$")
_PRD_ID_RE = re.compile(r"^PRD-[A-Z]+-\d+")

# Missing-item tokens (stable — consumed by the deliver-gate message + FIX-112).
MISSING_FUNCTIONALITY_LEVEL = "functionality_level_incoherent"
MISSING_WIRING = "wiring_or_behavioral_evidence"
MISSING_BUILD = "build_evidence"
MISSING_REVIEW_RECEIPT = "independent_review_receipt"
# PRD-QUAL-119-FR03: rollout vocabulary is never completion.
ROLLOUT_NOT_DEFAULT = "rollout_not_default"
# PRD-QUAL-119-FR05: a live claim needs a content-bound default-path receipt
# plus a superseded-path removal assertion; unit/substrate tests alone fail.
MISSING_DEFAULT_PATH_PROOF = "default_path_proof_missing"
# Advisory-only tokens (surfaced as warnings, NEVER hard-block — NFR02).
ADVISORY_UNKNOWN_RECEIPT = "review_receipt_provenance_unknown"
ADVISORY_ASSERTED_RECEIPT = "review_receipt_asserted_not_verifiable"

# Rollout states that are NOT normal default activation (PRD-QUAL-119-FR03).
_NON_DEFAULT_ROLLOUT_STATES = frozenset({"observe", "warn", "shadow", "canary", "canary-only", "disabled"})


def rollout_blocking(frontmatter: dict[str, object]) -> list[str]:
    """FR03: an observe/warn/shadow/canary-only/disabled default is incomplete.

    Rollout and completion are orthogonal — a behavior whose default posture is
    anything but normal activation returns ``rollout_not_default`` until the
    default is flipped. Absent/``default`` rollout state adds nothing.
    """
    rollout = str(frontmatter.get("rollout_state", "")).strip().lower()
    return [ROLLOUT_NOT_DEFAULT] if rollout in _NON_DEFAULT_ROLLOUT_STATES else []


def default_path_proof_blocking(frontmatter: dict[str, object], level: str) -> list[str]:
    """FR05: a ``live`` claim requires a content-bound default-path receipt.

    The ``default_path_proof`` frontmatter block must carry a non-empty
    ``receipt``, a ``source_digest`` binding the proof to current content
    (sha256:…), and a ``removal_assertion`` naming the superseded-path absence
    proof. Unit or substrate tests alone never satisfy this.
    """
    if level != "live":
        return []
    proof = frontmatter.get("default_path_proof")
    if not isinstance(proof, dict):
        return [MISSING_DEFAULT_PATH_PROOF]
    receipt = str(proof.get("receipt", "")).strip()
    digest = str(proof.get("source_digest", "")).strip()
    removal = str(proof.get("removal_assertion", "")).strip()
    if receipt and removal and digest.startswith("sha256:"):
        return []
    return [MISSING_DEFAULT_PATH_PROOF]


@dataclass(frozen=True)
class CoherenceReport:
    """Per-PRD coherence split into hard-blocking vs advisory-only findings."""

    blocking: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TransitionGateOutcome:
    """Result of the acceptance-integrity transition gate for one deliver call."""

    should_block: bool
    prd_ids: list[str] = field(default_factory=list)
    missing_by_prd: dict[str, list[str]] = field(default_factory=dict)
    advisory_by_prd: dict[str, list[str]] = field(default_factory=dict)
    message: str = ""
    warning: str = ""
    mode: str = "warn"
    # PRD-QUAL-119-FR06 (audit F5): the universal typed completion outcome per
    # detected PRD — the synchronous deliver dispatch consumes THIS vocabulary,
    # keeping scoped proof, external blockage, rollback, and repo-health
    # failures distinct instead of one coarse blocking list.
    decision_outcomes: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FR04 — transition detection
# ---------------------------------------------------------------------------


def detect_status_transitions(diff: str) -> list[str]:
    """Return the PRD IDs whose frontmatter gained a ``status: implemented`` line.

    Parses a unified git diff, tracking the current file from ``+++`` headers and
    matching ADDED (``+``) status lines within PRD files under :data:`PRDS_DIR`.
    Files outside the PRD directory are ignored; a diff that only edits prose
    yields ``[]``. Order-preserving + de-duplicated.
    """
    prd_ids: list[str] = []
    current_file: str | None = None
    for line in diff.splitlines():
        header = _PLUS_FILE_RE.match(line)
        if header:
            current_file = header.group(1).strip()
            continue
        if line.startswith(("diff --git", "--- ")):
            current_file = None
            continue
        if current_file is None or PRDS_DIR not in current_file:
            continue
        if _ADDED_STATUS_RE.match(line):
            prd_id = Path(current_file).stem
            if _PRD_ID_RE.match(prd_id) and prd_id not in prd_ids:
                prd_ids.append(prd_id)
    return prd_ids


def _prd_status_diff(base: str | None = None) -> str:
    """Path-limited ``git diff`` over the PRD directory (fail-open to '')."""
    from trw_mcp.tools import _review_helpers as _helpers

    return _helpers._get_git_diff(paths=[PRDS_DIR], base=base)


# ---------------------------------------------------------------------------
# FR05 — coherence requirements for a certified transition
# ---------------------------------------------------------------------------


def _has_build_evidence(run_path: Path, reader: FileStateReader) -> bool:
    """True when this run recorded a PASSING ``trw_build_check`` (same signal the
    build gate reads)."""
    from trw_mcp.tools._delivery_build_gates import _build_passed

    events_path = run_path / "meta" / "events.jsonl"
    if not events_path.exists():
        return False
    try:
        events = reader.read_jsonl(events_path)
    except Exception:  # justified: unreadable events -> no build evidence (fail-open)
        logger.debug("acceptance_integrity_build_events_unreadable", run=str(run_path), exc_info=True)
        return False
    return any(_build_passed(ev) for ev in events)


_WIRING_PRESENCE_RE = re.compile(
    r"(wiring_test:|consumer:|seams:|behavioral assertion|production[- ]caller|wiring/behavioral)",
    re.IGNORECASE,
)


def _wiring_block_present(content: str) -> bool:
    """Degraded presence check: does the PRD carry a wiring/behavioral block?"""
    return _WIRING_PRESENCE_RE.search(content) is not None


def _has_wiring_evidence(content: str, frontmatter: dict[str, object], project_root: Path) -> bool:
    """Wiring/behavioral evidence for a ``live`` claim (FR05 item 2).

    Uses PRD-CORE-190's wiring gate in block mode: no ``WIRING_GATE_FAIL``
    failures => wiring evidence present. If the wiring gate is unreachable at
    runtime, degrade to a PRESENCE check for a wiring/behavioral block and log
    ``acceptance_integrity_wiring_degraded`` (fail-open, NFR02 — never a spurious
    hard block on unavailability).
    """
    try:
        from trw_mcp.state.validation._prd_scoring_wiring import check_wiring_gate

        _warnings, failures = check_wiring_gate(content, frontmatter, mode="block", project_root=project_root)
        return not failures
    except Exception:  # justified: wiring gate unreachable -> degrade to presence check (NFR02)
        logger.warning(
            "acceptance_integrity_wiring_degraded",
            reason="wiring gate unreachable at runtime; degraded to presence check",
            exc_info=True,
        )
        return _wiring_block_present(content)


def evaluate_prd_coherence(
    prd_id: str,
    run_path: Path,
    reader: FileStateReader,
    *,
    gate_mode: str = "block",
    target_status: str | None = None,
) -> CoherenceReport:
    """Split a ``->implemented`` PRD's coherence into blocking vs advisory findings.

    Requirements (FR05); items 1-3 are always hard-blocking when unmet:
      1. functionality_level coherence (FPI #7 reused).
      2. wiring/behavioral evidence when ``functionality_level: live``.
      3. a passing build check recorded for the run.
      4. independent reviewer receipt for P0/P1 (FR03). Its severity depends on
         provenance class (OQ-001 / NFR02):
           - ``self_same_session`` -> HARD block (the incident class).
           - ``asserted_independent`` -> block under ``block`` mode; advisory in
             ``warn`` mode.
           - ``unknown`` -> ADVISORY only, never hard-blocks even in block mode.
           - ``independent`` -> satisfied.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation.prd_integrity import _check_functionality_level_matches_status
    from trw_mcp.tools._review_provenance import classify_review_independence, load_delivering_run_identity

    blocking: list[str] = []
    advisory: list[str] = []
    config = get_config()
    project_root = resolve_project_root()
    prd_path = project_root / config.prds_relative_path / f"{prd_id}.md"
    content = prd_path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(content)
    if target_status:
        # PRD-QUAL-119-FR06 (re-audit F1): a PROMOTION evaluates coherence as
        # if the PRD already held the target status — otherwise a still-
        # `approved` planned PRD sails past the FPI #7 implemented-family
        # checks and the guard certifies the exact L-EQwV incident.
        frontmatter = {**frontmatter, "status": target_status}

    priority = str(frontmatter.get("priority", "")).strip().upper()
    level = str(frontmatter.get("functionality_level", "")).strip().lower()

    # 1. functionality_level coherence (FPI #7).
    if _check_functionality_level_matches_status(frontmatter):
        blocking.append(MISSING_FUNCTIONALITY_LEVEL)

    # 2. wiring/behavioral evidence for live claims.
    if level == "live" and not _has_wiring_evidence(content, frontmatter, project_root):
        blocking.append(MISSING_WIRING)

    # 3. build evidence.
    if not _has_build_evidence(run_path, reader):
        blocking.append(MISSING_BUILD)

    # 4. independent review receipt (P0/P1 only), severity by provenance class.
    if priority in ("P0", "P1"):
        review_data = _read_review_data(run_path, reader)
        delivering = load_delivering_run_identity(run_path, reader)
        classification = classify_review_independence(review_data, delivering)
        if classification == "self_same_session":
            blocking.append(MISSING_REVIEW_RECEIPT)
        elif classification == "asserted_independent":
            if gate_mode == "block":
                blocking.append(MISSING_REVIEW_RECEIPT)
            else:
                advisory.append(ADVISORY_ASSERTED_RECEIPT)
        elif classification == "unknown":
            # NFR02 governing: an unknown-only shortfall warns, never hard-blocks.
            advisory.append(ADVISORY_UNKNOWN_RECEIPT)

    # 5. rollout state is not completion (PRD-QUAL-119-FR03).
    blocking.extend(rollout_blocking(frontmatter))

    # 6. vertical default-path proof for live claims (PRD-QUAL-119-FR05).
    blocking.extend(default_path_proof_blocking(frontmatter, level))

    # 7. typed activation gates (PRD-QUAL-119-FR02): a repository-controllable
    # open gate is a hard shortfall the repo must close before completion;
    # a malformed gate entry is a shortfall too (fail-closed, re-audit F4).
    gates, malformed_gates = _activation_gates(frontmatter)
    blocking.extend(
        f"activation_gate_open: {gate.gate_id}" for gate in gates if gate.completion_effect() == "incomplete"
    )
    blocking.extend("activation_gate_malformed" for _ in range(malformed_gates))

    return CoherenceReport(blocking=blocking, advisory=advisory)


def _activation_gates(frontmatter: dict[str, object]) -> tuple[list[ActivationGate], int]:
    """Parse typed activation gates; returns (gates, malformed_count).

    Fail-closed (re-audit F4): a malformed entry is COUNTED, not dropped —
    consumers turn every malformed entry into a blocking shortfall so a broken
    gate can never silently read as closed.
    """
    from trw_mcp.models.requirements import ActivationGate

    raw = frontmatter.get("activation_gates")
    if not isinstance(raw, list):
        return [], 0
    gates: list[ActivationGate] = []
    malformed = 0
    for item in raw:
        try:
            gates.append(ActivationGate.model_validate(item, strict=False))
        except Exception:  # justified: counted as a blocking shortfall by consumers
            malformed += 1
            logger.warning("activation_gate_unparseable", entry=str(item)[:120])
    return gates, malformed


def derive_transition_decision(
    prd_id: str,
    report: CoherenceReport,
    frontmatter: dict[str, object],
    content: str,
) -> EffectiveCompletionDecision:
    """Bridge the coherence report into the universal FR01 decision.

    Maps hard shortfalls to ABSENT components, an asserted receipt to
    CALLER_ASSERTED, unknown provenance to STALE, and typed external gates to
    :class:`ExternalGateEvidence` — then derives the single
    :class:`EffectiveCompletionDecision` all priorities share. The decision is
    content-bound to the PRD bytes via ``source_digest``.
    """
    import hashlib

    from trw_mcp.models.gate_decision import (
        CompletionComponent,
        CompletionComponentState,
        ExternalGateEvidence,
        derive_effective_completion,
    )

    components: list[CompletionComponent] = [
        CompletionComponent(component_id=token, state=CompletionComponentState.ABSENT) for token in report.blocking
    ]
    for token in report.advisory:
        state = (
            CompletionComponentState.CALLER_ASSERTED
            if token == ADVISORY_ASSERTED_RECEIPT
            else CompletionComponentState.STALE
        )
        components.append(CompletionComponent(component_id=token, state=state))
    if not report.blocking and not report.advisory:
        components.append(
            CompletionComponent(component_id="transition_coherence", state=CompletionComponentState.CURRENT)
        )

    external: list[ExternalGateEvidence] = []
    gates, malformed_gates = _activation_gates(frontmatter)
    for gate in gates:
        effect = gate.completion_effect()
        if effect == "externally_blocked":
            external.append(ExternalGateEvidence(gate_id=gate.gate_id, evidenced=True))
        elif effect == "unknown":
            external.append(ExternalGateEvidence(gate_id=gate.gate_id, evidenced=False))
    components.extend(
        CompletionComponent(component_id="activation_gate_malformed", state=CompletionComponentState.INVALID)
        for _ in range(malformed_gates)
    )

    return derive_effective_completion(
        prd_id,
        priority=str(frontmatter.get("priority", "")),
        components=tuple(components),
        external_gates=tuple(external),
        source_digest="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def check_transition_coherence(
    prd_id: str,
    run_path: Path,
    reader: FileStateReader,
    *,
    gate_mode: str = "block",
) -> list[str]:
    """Return the HARD-BLOCKING unmet coherence requirements (FR05 back-compat).

    Empty list => no blocking shortfall. Advisory-only findings (unknown/asserted
    provenance under warn) are surfaced via :func:`evaluate_prd_coherence`.
    """
    return evaluate_prd_coherence(prd_id, run_path, reader, gate_mode=gate_mode).blocking


def _read_review_data(run_path: Path, reader: FileStateReader) -> dict[str, object]:
    review_path = run_path / "meta" / "review.yaml"
    if not review_path.exists():
        return {}
    try:
        data = reader.read_yaml(review_path)
    except Exception:  # justified: unreadable review.yaml -> treated as no receipt (fail-open)
        logger.debug("acceptance_integrity_review_unreadable", run=str(run_path), exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Orchestration — invoked from _deliver_gate_dispatch.py
# ---------------------------------------------------------------------------


def _read_run_yaml(run_path: Path, reader: FileStateReader) -> dict[str, object]:
    run_yaml = run_path / "meta" / "run.yaml"
    if not run_yaml.exists():
        return {}
    try:
        data = reader.read_yaml(run_yaml)
    except Exception:  # justified: unreadable run.yaml -> no gating (fail-open)
        return {}
    return data if isinstance(data, dict) else {}


def _gate_mode_blocks_task(config: object, task_type: str) -> bool:
    """True when deliver_gate_mode resolves to a block posture for this task_type.

    Reuses the ``_BUILD_ARTIFACT_TASK_TYPES`` classification (coding/rca/eval) and
    the per-task-type override map so the acceptance-integrity gate is scoped
    identically to the build gate — docs/research/planning/unknown never block.
    """
    from trw_mcp.tools._deliver_gate_mode import _BUILD_ARTIFACT_TASK_TYPES

    overrides = getattr(config, "deliver_gate_task_type_overrides", None) or {}
    mode = str(overrides.get(task_type, getattr(config, "deliver_gate_mode", "advisory")))
    return mode in {"block_coding", "block_all"} and task_type in _BUILD_ARTIFACT_TASK_TYPES


def _run_base_ref(run_data: dict[str, object]) -> str | None:
    """Best-effort recorded base ref for a scoped diff; None => ``git diff HEAD``.

    TODO(PRD-CORE-213 OQ-002 / follow-up FR08): the run-creation writer
    (``tools/orchestration.py`` — dirty/other-workstream-owned at audit time, so
    NOT edited here) records no ``base_commit`` today. Under this repo's
    commit-frequently policy a session usually COMMITS the ``status: implemented``
    edit before ``trw_deliver``, so the uncommitted ``git diff HEAD`` fallback
    MISSES the majority case. Closing this needs the writer to persist
    ``git rev-parse HEAD`` at ``trw_init`` (fail-open). RISK-005 re-rated to
    High-probability/Medium-impact pending that follow-up. This reads an optional
    ``base_ref``/``base_commit`` key so the fix is drop-in once the writer lands.
    """
    for key in ("base_ref", "base_commit"):
        value = run_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _scope_detected_prds(prd_ids: list[str], run_data: dict[str, object]) -> list[str]:
    """Limit shared-worktree transition checks to an explicit run PRD scope.

    An absent or empty scope preserves the legacy all-detected behavior. A
    populated scope is an ownership contract: unrelated concurrent PRD edits
    must not block this run's delivery or be certified by its receipts.
    """
    raw_scope = run_data.get("prd_scope")
    if not isinstance(raw_scope, list) or not raw_scope:
        return prd_ids
    scope = {str(item).strip() for item in raw_scope if str(item).strip()}
    return [prd_id for prd_id in prd_ids if prd_id in scope]


def _format_block_message(missing_by_prd: dict[str, list[str]]) -> str:
    parts = [f"{prd_id} missing: [{', '.join(missing)}]" for prd_id, missing in missing_by_prd.items()]
    return (
        "Acceptance-integrity gate: a PRD status->implemented transition in this session is not "
        f"certifiable. {'; '.join(parts)}. Satisfy the missing requirement(s), or—only for a documented "
        "acceptable failure—retry with allow_unverified=true and a structured acceptable-failure record "
        "(failed_command, residual_risk, owner, expiry_iso)."
    )


def _format_warning_message(findings_by_prd: dict[str, list[str]]) -> str:
    parts = [f"{prd_id}: [{', '.join(items)}]" for prd_id, items in findings_by_prd.items()]
    return (
        "Acceptance-integrity advisory: a PRD status->implemented transition in this session has "
        f"non-certifying findings that did not hard-block. {'; '.join(parts)}. Address them or promote "
        "prd_transition_gate=block to enforce."
    )


def evaluate_transition_gate(run_path: Path) -> TransitionGateOutcome:
    """Detect ``->implemented`` transitions and evaluate their coherence.

    ``should_block=True`` only when: the deliver-gate mode blocks this run's
    task_type, ``prd_transition_gate=block``, a transition is detected, and at
    least one detected PRD has a HARD-blocking shortfall (items 1-3, or a
    ``self_same_session``/``asserted_independent`` receipt under block mode). A
    non-blocking finding (warn mode, or an ``unknown``/``asserted`` advisory)
    populates ``warning`` so the delivering agent still SEES it (NFR02 observable
    degradation, no dormant warn path). Any resolution error degrades to no-block.
    """
    from trw_mcp.models.config import get_config

    reader = FileStateReader()
    try:
        config = get_config()
        gate_mode = str(getattr(config, "prd_transition_gate", "warn"))
        run_data = _read_run_yaml(run_path, reader)
        task_type = str(run_data.get("task_type", "unknown")) or "unknown"
        if not _gate_mode_blocks_task(config, task_type):
            return TransitionGateOutcome(should_block=False, mode=gate_mode)

        diff = _prd_status_diff(_run_base_ref(run_data))
        prd_ids = _scope_detected_prds(detect_status_transitions(diff), run_data)
        if not prd_ids:
            return TransitionGateOutcome(should_block=False, mode=gate_mode)

        blocking_by_prd: dict[str, list[str]] = {}
        advisory_by_prd: dict[str, list[str]] = {}
        decision_outcomes: dict[str, str] = {}
        for prd_id in prd_ids:
            try:
                report = evaluate_prd_coherence(prd_id, run_path, reader, gate_mode=gate_mode)
            except Exception:  # justified: per-PRD coherence failure degrades to no-finding (NFR02)
                logger.warning("acceptance_integrity_coherence_degraded", prd_id=prd_id, exc_info=True)
                continue
            if report.blocking:
                blocking_by_prd[prd_id] = report.blocking
            if report.advisory:
                advisory_by_prd[prd_id] = report.advisory
            # PRD-QUAL-119-FR06: derive the universal typed decision for the
            # already-edited transition (frontmatter carries the new status, so
            # no target override is needed here). Consumed by the dispatch.
            try:
                from trw_mcp.models.config import get_config as _get_config
                from trw_mcp.state._paths import resolve_project_root
                from trw_mcp.state.prd_utils import parse_frontmatter

                prd_path = resolve_project_root() / _get_config().prds_relative_path / f"{prd_id}.md"
                content = prd_path.read_text(encoding="utf-8")
                decision = derive_transition_decision(prd_id, report, parse_frontmatter(content), content)
                decision_outcomes[prd_id] = decision.outcome.value
            except Exception:  # justified: decision derivation failure -> unknown (fail-closed vocabulary)
                decision_outcomes[prd_id] = "unknown"

        # Hard block only under block mode with a hard-blocking shortfall.
        if gate_mode == "block" and blocking_by_prd:
            return TransitionGateOutcome(
                should_block=True,
                prd_ids=prd_ids,
                missing_by_prd=blocking_by_prd,
                advisory_by_prd=advisory_by_prd,
                message=_format_block_message(blocking_by_prd),
                mode=gate_mode,
                decision_outcomes=decision_outcomes,
            )

        # No hard block: fold every finding (warn-mode blocking items are
        # downgraded to advisory) into an OBSERVABLE warning.
        findings_by_prd: dict[str, list[str]] = {}
        for prd_id in prd_ids:
            merged = list(blocking_by_prd.get(prd_id, [])) + list(advisory_by_prd.get(prd_id, []))
            if merged:
                findings_by_prd[prd_id] = merged
        if not findings_by_prd:
            return TransitionGateOutcome(
                should_block=False, prd_ids=prd_ids, mode=gate_mode, decision_outcomes=decision_outcomes
            )

        logger.warning("acceptance_integrity_warn", mode=gate_mode, prds=list(findings_by_prd))
        return TransitionGateOutcome(
            should_block=False,
            prd_ids=prd_ids,
            missing_by_prd=blocking_by_prd,
            advisory_by_prd=advisory_by_prd,
            warning=_format_warning_message(findings_by_prd),
            mode=gate_mode,
            decision_outcomes=decision_outcomes,
        )
    except Exception:  # justified: any resolution failure degrades to no-block (NFR02, fail-open)
        logger.warning("acceptance_integrity_gate_degraded", run=str(run_path), exc_info=True)
        return TransitionGateOutcome(should_block=False)
