"""Safety-critical PRD scope resolution + the blocking adversarial-audit gate.

PRD-CORE-255-FR03/FR04. Belongs to the ``_delivery_helpers.py`` facade, which
calls :func:`safety_critical_gate_result` from ``check_delivery_gates``.

Why this exists: the 2026-06-16 "Potemkin gate" incident passed BOTH mandatory
gates (``trw_build_check`` and ``trw_review``) and was caught only by the
OPTIONAL adversarial pass. Nothing required that pass, and nothing checked it
happened — for any PRD, at any risk level. This module makes the adversarial
pass structurally required for work whose governing PRD declares itself
``safety_critical: true``.

Three resolutions, one of which blocks (operator policy, 2026-09-04 amendment to
PRD-CORE-255-FR03/FR04):

* :data:`NOT_DECLARED` — the scope union is empty. The gate is INERT: a PRD opts
  IN by declaring ``safety_critical: true``, so a run that names no PRD has
  nothing to opt in, and blocking it would contradict the PRD's own Rollout /
  RISK-002 / Open-Questions position that FR04 "never fires until a maintainer
  opts a PRD in". The deliver payload still says so (``safety_critical:
  not_declared`` plus a one-line advisory) rather than staying silent.
* ``True`` / ``False`` — every named PRD resolved; the frontmatter flag decides.
* :data:`UNKNOWN_SCOPE` — the scope names a PRD whose file cannot be read or
  parsed. This is the ONE fail-closed branch (NFR01), and it is the honest one: a
  run claiming a governing PRD it cannot show is the actual misrepresentation,
  and its remedy names the offending id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import structlog

from trw_mcp.models._evidence_plans import RequiredReviewPlan, ReviewVerdict
from trw_mcp.models._evidence_records import ReviewReceipt
from trw_mcp.state.review_signoffs import trw_dir_for_run
from trw_mcp.tools._review_adversarial_source import adversarial_source_is_verified
from trw_mcp.tools._review_receipt_writer import ADVERSARIAL_AUDIT_RUBRIC
from trw_mcp.tools._review_reviewer_family import ReviewerFields

logger = structlog.get_logger(__name__)

#: The scope named a PRD that could not be read or parsed. Treated exactly like
#: ``safety_critical: true`` by FR04 — an unshowable governing PRD is not a safe
#: one. The only fail-closed resolution.
UNKNOWN_SCOPE: Literal["unknown"] = "unknown"

#: The run declared no PRD scope at all. INERT by operator policy: the gate has
#: nothing to opt in, so it reports rather than blocks.
NOT_DECLARED: Literal["not_declared"] = "not_declared"

#: Stable machine reason for the FR04 block.
REASON_ADVERSARIAL_AUDIT_MISSING = "safety_critical_adversarial_audit_missing"

#: Deliver-gate modes under which FR04 blocks rather than merely reporting.
_BLOCKING_MODES: frozenset[str] = frozenset({"block_coding", "block_all"})

#: Severities that count as a real adversarial finding — every normalized level
#: except ``info``. Derived from the SINGLE severity table so the gate cannot
#: drift from what ``trw_review`` accepted (PRD-CORE-255-FR04 condition 4).
_SUBSTANTIVE_SEVERITIES: frozenset[str] = frozenset({"critical", "warning"})

_UNKNOWN_SCOPE_REMEDY = (
    "make the named PRD file readable under docs/requirements-aare-f/prds/ with parseable frontmatter, "
    "or correct the id in the run's prd_scope"
)
_NOT_DECLARED_ADVISORY = (
    "safety_critical: not_declared — this run names no PRD, so the adversarial-audit gate did not "
    "evaluate; scope the run to its PRDs (prd_scope at trw_init, or prd_ids on trw_review) to enable it."
)
_MISSING_AUDIT_REMEDY = (
    "relay an independent adversarial audit through trw_review under the current typed-receipt path "
    "(mode='manual', reviewer_identity={'reviewer_source': 'cross_model', 'reviewer_receipt_id': <sha256 of the "
    "auditor's output file>}, external_receipt_path=<that file>), or record an operator review carrying a "
    "reviewer_receipt_id; /mcp reconnect when the server predates these fields"
)


@dataclass(frozen=True)
class SafetyCriticalOutcome:
    """One evaluation of the FR04 gate. Every field is returned to the caller."""

    should_block: bool = False
    advisory: str = ""
    message: str = ""
    resolution: bool | str = False
    satisfying_receipt_id: str = ""


@dataclass(frozen=True)
class ScopeResolution:
    """The FR03 verdict plus the ids that made it :data:`UNKNOWN_SCOPE`."""

    value: bool | Literal["unknown", "not_declared"]
    unreadable: tuple[str, ...] = field(default=())


def _scope_from_run_yaml(run_path: Path) -> list[str]:
    """``prd_scope`` entries from ``meta/run.yaml``.

    An absent or unreadable run file contributes NO entries rather than a
    fail-closed verdict: it is indistinguishable from a run that declared no
    scope, and a run that declared no scope is inert by policy — so corrupting
    this file buys an evader nothing it could not get by declaring ``[]``.
    """
    from trw_mcp.state.persistence import FileStateReader

    run_yaml = run_path / "meta" / "run.yaml"
    if not run_yaml.is_file():
        return []
    try:
        raw = FileStateReader().read_yaml(run_yaml).get("prd_scope", [])
    except Exception:  # justified: an unreadable run file declares no scope; the gate stays inert, not "safe"
        logger.warning("safety_critical_run_scope_unreadable", run=str(run_path), exc_info=True)
        return []
    return [str(entry) for entry in raw] if isinstance(raw, list) else []


def _scope_from_receipts(run_path: Path) -> list[str]:
    """``prd_ids`` recorded on every typed review receipt.

    A malformed receipt stops the scan (its own ids are unknowable) but keeps the
    ids already collected — those are declarations that really were made.
    """
    directory = run_path / "meta" / "receipts" / "review"
    if not directory.is_dir():
        return []
    entries: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            entries.extend(ReviewReceipt.model_validate_json(path.read_bytes()).prd_ids)
        except Exception:  # justified: a malformed receipt contributes no ids; the ones already read still count
            logger.warning("safety_critical_receipt_scope_unreadable", receipt=str(path), exc_info=True)
            return entries
    return entries


def _resolve_scope(run_path: Path | None) -> ScopeResolution:
    """FR03 resolution plus the unreadable ids behind an :data:`UNKNOWN_SCOPE`."""
    if run_path is None:
        return ScopeResolution(NOT_DECLARED)
    union = sorted({entry for entry in (*_scope_from_run_yaml(run_path), *_scope_from_receipts(run_path)) if entry})
    if not union:
        return ScopeResolution(NOT_DECLARED)
    from trw_mcp.tools._plan_acceptance_gate import _resolve_prd_scope

    try:
        prd_files, unresolved = _resolve_prd_scope(union)
    except Exception:  # justified: fail-CLOSED, a declared scope that cannot be resolved is never a safe scope
        logger.warning("safety_critical_scope_resolution_failed", run=str(run_path), exc_info=True)
        return ScopeResolution(UNKNOWN_SCOPE, tuple(union))
    if unresolved:
        logger.warning("safety_critical_scope_unresolved", run=str(run_path), entries=unresolved)
        return ScopeResolution(UNKNOWN_SCOPE, tuple(unresolved))
    flagged, unreadable = _read_safety_critical_flags(prd_files)
    if unreadable:
        return ScopeResolution(UNKNOWN_SCOPE, unreadable)
    return ScopeResolution(flagged)


def resolve_safety_critical_scope(
    run_path: Path | None, project_root: Path
) -> bool | Literal["unknown", "not_declared"]:
    """PRD-CORE-255-FR03 — is this run's PRD scope safety-critical?

    The input scope is the UNION of the run's declared ``run.yaml`` ``prd_scope``
    and the ``prd_ids`` recorded on its typed :class:`ReviewReceipt`s — the two
    scope sources that actually exist on HEAD (``trw_deliver`` takes no
    ``prd_ids`` argument of its own).

    Returns ``True`` when any named PRD declares ``safety_critical: true``,
    ``False`` when every named PRD resolves and declares otherwise,
    :data:`NOT_DECLARED` when the union is empty (inert — see the module
    docstring), and :data:`UNKNOWN_SCOPE` when a NAMED PRD cannot be resolved,
    read, or parsed. Only :data:`UNKNOWN_SCOPE` fails closed.

    ``project_root`` is part of the FR03 signature; scope entries resolve through
    ``_resolve_prd_scope``, which reads the project root itself.
    """
    return _resolve_scope(run_path).value


def _read_safety_critical_flags(prd_files: list[Path]) -> tuple[bool, tuple[str, ...]]:
    """``(any PRD declares the flag, the stems of the PRDs that could not be read)``."""
    from trw_mcp.state.prd_utils import parse_frontmatter

    flagged = False
    unreadable: list[str] = []
    for prd_file in prd_files:
        try:
            frontmatter = parse_frontmatter(prd_file.read_text(encoding="utf-8"))
        except OSError:
            logger.warning("safety_critical_prd_unreadable", prd=str(prd_file))
            unreadable.append(prd_file.stem)
            continue
        if not frontmatter:
            # An empty parse is indistinguishable from a PRD whose frontmatter is
            # malformed; both are "we could not read the declaration".
            logger.warning("safety_critical_prd_frontmatter_absent", prd=str(prd_file))
            unreadable.append(prd_file.stem)
            continue
        flagged = flagged or frontmatter.get("safety_critical") is True
    return flagged, tuple(unreadable)


def _receipt_is_verified_adversarial(receipt: ReviewReceipt, run_path: Path) -> bool:
    """All five FR04 conditions on ONE receipt."""
    fields = ReviewerFields(
        origin=receipt.reviewer_origin,
        identity=receipt.reviewer_identity,
        family=receipt.reviewer_family,
        external_receipt_digest=receipt.external_receipt_digest,
    )
    # Re-verified HERE, not trusted from the receipt's own stamp: an operator
    # sign-off that has since expired, or that was never bound to this review,
    # must not authorize delivery just because it was accepted at mint time.
    # The refs are the receipt's OWN review id and bound scope digest, so the
    # gate resolves exactly the approval the writer resolved.
    review_refs = tuple(ref for ref in (receipt.review_id, receipt.content_binding.scope_digest) if ref)
    verification = adversarial_source_is_verified(fields, review_refs=review_refs, trw_dir=trw_dir_for_run(run_path))
    if not verification.verified:
        logger.warning(
            "safety_critical_adversarial_source_unverified",
            receipt=receipt.receipt_id,
            reason=verification.reason,
            origin=receipt.reviewer_origin,
        )
        return False
    if ADVERSARIAL_AUDIT_RUBRIC not in receipt.realized_rubric_ids:
        return False
    if receipt.verdict not in (ReviewVerdict.PASS, ReviewVerdict.BLOCK):
        return False
    plan_path = run_path / "meta" / "plans" / "review" / f"{receipt.review_plan_id}.json"
    try:
        plan = RequiredReviewPlan.model_validate_json(plan_path.read_bytes())
    except Exception:  # justified: fail-CLOSED, an unreadable plan cannot prove substance
        logger.warning("safety_critical_plan_unreadable", plan=str(plan_path), exc_info=True)
        return False
    if not receipt.is_structurally_substantive(plan.required_rubric_ids, plan.required_reviewer_roles):
        return False
    findings_present = any(finding.severity in _SUBSTANTIVE_SEVERITIES for finding in receipt.findings)
    return findings_present or receipt.adversarial_pass


def find_satisfying_adversarial_receipt(run_path: Path) -> str | None:
    """The id of the newest receipt satisfying FR04, or ``None``."""
    directory = run_path / "meta" / "receipts" / "review"
    if not directory.is_dir():
        return None
    try:
        candidates = sorted(directory.glob("*.json"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except OSError:  # justified: fail-CLOSED, an unreadable directory yields no evidence
        logger.warning("safety_critical_receipt_dir_unreadable", run=str(run_path), exc_info=True)
        return None
    for path in candidates:
        try:
            receipt = ReviewReceipt.model_validate_json(path.read_bytes())
        except Exception:  # justified: a malformed receipt is not evidence; keep scanning
            logger.warning("safety_critical_receipt_malformed", receipt=str(path), exc_info=True)
            continue
        if _receipt_is_verified_adversarial(receipt, run_path):
            return receipt.receipt_id
    return None


def safety_critical_gate_result(run_path: Path | None) -> SafetyCriticalOutcome:
    """PRD-CORE-255-FR04 — evaluate the blocking adversarial-audit gate.

    Blocks when the deliver-gate mode is ``block_coding``/``block_all``, the FR03
    resolution is ``True`` or :data:`UNKNOWN_SCOPE`, and no receipt in the run
    satisfies every FR04 condition. A :data:`NOT_DECLARED` scope never blocks; it
    returns the one-line advisory the caller sees as ``safety_critical:
    not_declared``.

    ``run_path is None`` is :data:`NOT_DECLARED` with no advisory: there is no
    scope-declaration surface to point the caller at, and a run-less
    ``check_delivery_gates`` returns no gate keys by contract, so the
    non-evaluation is recorded as a structlog event instead. The call site still
    sits BEFORE ``check_delivery_gates``'s no-active-run early return, so dropping
    a run pin does not move the gate.
    """
    from trw_mcp.tools._deliver_gate_mode import resolve_gate_mode

    if run_path is None:
        logger.info("safety_critical_gate_not_evaluable", reason="no_run_pin")
        return SafetyCriticalOutcome(resolution=NOT_DECLARED)

    task_type = _read_task_type(run_path)
    mode = resolve_gate_mode(task_type)
    scope = _resolve_scope(run_path)
    if scope.value is False:
        return SafetyCriticalOutcome(resolution=False)
    if scope.value == NOT_DECLARED:
        logger.info("safety_critical_gate_scope_not_declared", run=str(run_path))
        return SafetyCriticalOutcome(advisory=_NOT_DECLARED_ADVISORY, resolution=NOT_DECLARED)

    satisfying = find_satisfying_adversarial_receipt(run_path)
    if satisfying is not None:
        logger.info(
            "safety_critical_adversarial_satisfied",
            run=str(run_path),
            receipt=satisfying,
            resolution=str(scope.value),
        )
        return SafetyCriticalOutcome(resolution=scope.value, satisfying_receipt_id=satisfying)

    scope_clause = (
        f"the run's PRD scope names {', '.join(scope.unreadable)}, which could not be read or parsed "
        f"({UNKNOWN_SCOPE}) — {_UNKNOWN_SCOPE_REMEDY}"
        if scope.value == UNKNOWN_SCOPE
        else "the run's PRD scope names a PRD declared safety_critical: true"
    )
    detail = (
        f"Delivery blocked ({REASON_ADVERSARIAL_AUDIT_MISSING}): {scope_clause}, and no review receipt "
        f"records a settled, independently-verified adversarial audit. Remedy: {_MISSING_AUDIT_REMEDY}."
    )
    if mode not in _BLOCKING_MODES:
        # Same evidence shortfall, reported not enforced: the operator chose an
        # advisory deliver-gate mode, and this gate does not override that choice.
        logger.warning(
            "safety_critical_adversarial_advisory",
            run=str(run_path),
            deliver_gate_mode=mode,
            resolution=str(scope.value),
        )
        return SafetyCriticalOutcome(
            advisory=detail.replace("Delivery blocked", "Delivery advisory", 1),
            resolution=scope.value,
        )
    logger.warning(
        "safety_critical_adversarial_block",
        run=str(run_path),
        deliver_gate_mode=mode,
        task_type=task_type,
        resolution=str(scope.value),
        reason_code=REASON_ADVERSARIAL_AUDIT_MISSING,
    )
    return SafetyCriticalOutcome(should_block=True, message=detail, resolution=scope.value)


def _read_task_type(run_path: Path) -> str:
    """The run's declared task type; unreadable state yields ``unknown``.

    ``unknown`` is not a weakening here — it selects the DEFAULT deliver-gate
    mode (``block_coding``), i.e. the blocking branch.
    """
    from trw_mcp.state.persistence import FileStateReader

    try:
        run_yaml = run_path / "meta" / "run.yaml"
        if not run_yaml.is_file():
            return "unknown"
        return str(FileStateReader().read_yaml(run_yaml).get("task_type", "unknown")) or "unknown"
    except Exception:  # justified: an unreadable task type selects the default (blocking) mode
        logger.warning("safety_critical_task_type_unreadable", run=str(run_path), exc_info=True)
        return "unknown"


__all__ = [
    "NOT_DECLARED",
    "REASON_ADVERSARIAL_AUDIT_MISSING",
    "UNKNOWN_SCOPE",
    "SafetyCriticalOutcome",
    "find_satisfying_adversarial_receipt",
    "resolve_safety_critical_scope",
    "safety_critical_gate_result",
]
