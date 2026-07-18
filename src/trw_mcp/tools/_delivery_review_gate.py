"""PRD-CORE-192 — review_gate_mode escalation + pre-deliver REVIEW nudge helpers.

Belongs to the ``_delivery_helpers.py`` facade. Extracted as a sibling so the
deliver-gate helpers module stays under the 350 effective-LOC gate. Re-exported
from ``_delivery_helpers.py`` for back-compat — callers and tests keep importing
``_review_gate_mode_is_block`` / ``_review_nudge_for_run`` from there.

Test-monkeypatch indirection: both helpers resolve ``get_config`` and
``_read_complexity_class`` THROUGH the ``_delivery_helpers`` facade at call time
(``_dh.get_config()`` / ``_dh._read_complexity_class()``) rather than via a
direct import. Tests patch ``trw_mcp.tools._delivery_helpers.get_config``; the
indirection makes that patch propagate here.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_validation import normalize_review_finding

logger = structlog.get_logger(__name__)


def _review_data_is_substantive(review_data: dict[str, object]) -> bool:
    """Return whether a review artifact carries substantive REVIEW evidence.

    New writers must stamp ``substantive`` with a real boolean. A present but
    malformed stamp fails closed. Unstamped legacy artifacts remain compatible
    only when they contain at least one schema-valid finding; empty mappings and
    empty findings lists are not recognizable REVIEW evidence.
    """
    if "substantive" in review_data:
        explicit = review_data["substantive"]
        return explicit if isinstance(explicit, bool) else False
    if bool(review_data.get("auto_analysis_limited", False)):
        return False

    for findings_key in ("findings", "cross_model_findings"):
        findings = review_data.get(findings_key)
        if isinstance(findings, list) and any(normalize_review_finding(finding) is not None for finding in findings):
            return True
    return False


def _review_artifact_is_substantive(review_path: Path, reader: FileStateReader) -> bool:
    """Read a review artifact's readiness stamp; unreadable data is not evidence."""
    if not review_path.exists():
        return False
    try:
        return _review_data_is_substantive(reader.read_yaml(review_path))
    except Exception:  # justified: malformed evidence must continue through the configured missing-review policy
        logger.warning("review_substance_check_failed", review_path=str(review_path), exc_info=True)
        return False


def _review_gate_mode_is_block(complexity_class: str) -> bool:
    """PRD-CORE-192-FR02/FR03/NFR02 — is review_gate_mode=block for a STANDARD+ run?

    Reads the configured ``review_gate_mode``; returns True only when it is
    ``block``. On ANY exception (config unavailable, attribute missing) returns
    False so the caller keeps the pre-existing soft ``warning`` — the gate must
    never escalate to a hard block on a resolution failure (NFR02). When the
    escalation fires, emits a ``review_gate_mode_blocked`` structlog event so
    the decision is observable (FR03), mirroring ``deliver_gate_mode_blocked``
    (which logs at WARNING — matched here for parity).
    """
    # Resolve get_config via the facade so tests patching
    # trw_mcp.tools._delivery_helpers.get_config propagate to this helper.
    from trw_mcp.tools import _delivery_helpers as _dh

    try:
        mode = str(_dh.get_config().review_gate_mode)
        if mode == "block":
            logger.warning(
                "review_gate_mode_blocked",
                complexity_class=complexity_class,
                review_gate_mode=mode,
            )
            return True
        return False
    except Exception:
        # codex cross-model review: kept fail-open BROAD on purpose — NFR02
        # requires that ANY config-resolution failure (the implementation can
        # raise arbitrary error types) defaults to the soft warning, never a
        # spurious hard block. The review's substance (silent block->warn
        # downgrades are invisible) is addressed by RAISING the log to WARNING
        # with an explicit "enforcement degraded" message + reason, so the
        # operator sees that block-mode escalation could not be evaluated and the
        # gate fell back to soft — rather than the failure being swallowed silently.
        logger.warning(
            "review_gate_mode_enforcement_degraded",
            reason="review_gate_mode config could not be resolved; defaulting to soft warning",
            complexity_class=complexity_class,
            exc_info=True,
        )
        return False


def _review_nudge_for_run(run_path: Path, reader: FileStateReader) -> str | None:
    """PRD-CORE-192-FR04 — pre-deliver REVIEW nudge for a STANDARD+ run with no review.

    Returns a structured prompt referencing ``trw_review`` when the run is
    STANDARD/COMPREHENSIVE and has no substantive ``meta/review.yaml`` — surfaced regardless
    of ``review_gate_mode`` so even warn-mode agents see it prominently rather
    than buried in a gate warning. Returns None otherwise. Fail-open.
    """
    # Resolve _read_complexity_class via the facade so the helper picks up the
    # canonical (and test-patchable) implementation from _delivery_helpers.
    from trw_mcp.tools import _delivery_helpers as _dh

    try:
        review_path = run_path / "meta" / "review.yaml"
        if _dh._review_artifact_is_substantive(review_path, reader):
            return None
        complexity_class = _dh._read_complexity_class(run_path, reader)
        if complexity_class in ("STANDARD", "COMPREHENSIVE"):
            return (
                f"No substantive trw_review recorded for this {complexity_class} run. Run a real review now "
                "to satisfy the adversarial review gate before delivering."
            )
    except Exception:  # justified: fail-open — nudge is advisory and must not block delivery
        logger.debug("review_nudge_resolution_failed", exc_info=True)
    return None


def _check_review_gate(
    run_path: Path,
    reader: FileStateReader,
) -> tuple[str | None, str | None, str | None]:
    """Return the substantive review block, warning, or advisory for a run."""
    from trw_mcp.tools import _delivery_helpers as _dh

    block: str | None = None
    warning: str | None = None
    advisory: str | None = None
    review_path = run_path / "meta" / "review.yaml"
    has_substantive_review = False

    # CORE-205 FR03/FR08: typed evidence is authoritative.  In enforce mode a
    # typed-absent legacy projection is missing evidence; in observe mode only,
    # typed absence may consult the legacy projection.  Typed-present invalid
    # evidence never falls back in either mode.
    from trw_mcp.models._evidence_core import EvidenceMode
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.tools._evidence_gates import read_evidence_mode
    from trw_mcp.tools._review_receipt_writer import load_latest_review_evidence

    try:
        typed_state, typed_receipt = load_latest_review_evidence(run_path, resolve_project_root())
        evidence_mode = read_evidence_mode(_dh.get_config())
    except Exception:  # justified: evidence resolution failure is non-positive
        logger.warning("typed_review_evidence_resolution_failed", run=str(run_path), exc_info=True)
        typed_state, typed_receipt, evidence_mode = None, None, None

    if typed_state is not None and typed_state.typed_present:
        has_substantive_review = typed_state.is_positive and typed_receipt is not None
        if has_substantive_review and typed_receipt is not None:
            verdict = typed_receipt.verdict.value
            critical = sum(1 for finding in typed_receipt.findings if finding.severity == "critical")
            if verdict == "block" and critical > 0:
                complexity = _dh._read_complexity_class(run_path, reader)
                if complexity in ("STANDARD", "COMPREHENSIVE"):
                    block = (
                        f"Review verdict is 'block' with {critical} critical finding(s) "
                        f"(complexity: {complexity}). Delivery blocked. Fix the critical "
                        "review findings before delivering, or—only for a documented acceptable "
                        "failure—retry with allow_unverified=true and a structured acceptable-failure record."
                    )
                else:
                    warning = (
                        f"Review has {critical} critical findings. "
                        "Delivery proceeding but review issues should be addressed."
                    )
    elif evidence_mode is EvidenceMode.OBSERVE and review_path.exists():
        try:
            review_data = reader.read_yaml(review_path)
            has_substantive_review = _review_data_is_substantive(review_data)
            if has_substantive_review:
                verdict = str(review_data.get("verdict", ""))
                critical = int(str(review_data.get("critical_count", 0)))
                if verdict == "block" and critical > 0:
                    complexity = _dh._read_complexity_class(run_path, reader)
                    if complexity in ("STANDARD", "COMPREHENSIVE"):
                        block = (
                            f"Review verdict is 'block' with {critical} critical finding(s) "
                            f"(complexity: {complexity}). Delivery blocked. Fix the critical "
                            "review findings before delivering, or—only for a documented acceptable "
                            "failure—retry with allow_unverified=true and a structured acceptable-failure record."
                        )
                    else:
                        warning = (
                            f"Review has {critical} critical findings. "
                            "Delivery proceeding but review issues should be addressed."
                        )
        except Exception:  # justified: malformed evidence is treated as absent, then normal complexity policy applies
            logger.warning("maintenance_review_gate_failed", exc_info=True)
            has_substantive_review = False

    if has_substantive_review:
        return block, warning, advisory

    complexity = _dh._read_complexity_class(run_path, reader)
    missing_label = "No substantive trw_review was recorded"
    if complexity in ("STANDARD", "COMPREHENSIVE"):
        if _review_gate_mode_is_block(complexity):
            block = (
                f"{missing_label} before delivery (complexity: {complexity}) and "
                "review_gate_mode=block. REVIEW is mandatory for STANDARD+ work. Run a substantive "
                "trw_review or /trw-audit, or—only for a documented acceptable failure—retry with "
                "allow_unverified=true and a structured acceptable-failure record."
            )
        else:
            warning = (
                f"{missing_label} before delivery (complexity: {complexity}). REVIEW is mandatory "
                "for STANDARD+ work; run a substantive trw_review or /trw-audit."
            )
    else:
        advisory = (
            "No substantive trw_review was recorded before delivery. "
            "Consider running a real reviewer or supplying reviewer findings."
        )
    return block, warning, advisory
