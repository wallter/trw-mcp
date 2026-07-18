"""Deliver-gate readiness scan for the ``orchestration.py`` facade (PRD-QUAL-105).

Belongs to the ``orchestration.py`` facade. Re-exported there for back-compat so
``trw_status`` can surface ``build_gate_ready`` / ``review_gate_ready`` /
``deliver_gate_summary`` without bloating the facade module past the
350-effective-LOC gate.

The build-gate predicate is the SAME one the delivery gate uses: this module
imports ``_build_passed`` from ``_delivery_build_gates`` rather than re-deriving
it, so the readiness surfaced at status-check time can never diverge from the
gate enforced at deliver time (PRD-QUAL-105-FR01, risk R2).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.typed_dicts import DeliverGateScanDict
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state
from trw_mcp.tools._delivery_build_gates import _build_evidence_is_stale, _build_passed

logger = structlog.get_logger(__name__)

# A review verdict of "block" is the only substantive verdict that keeps the
# review gate from being ready. Acceptable failures belong to the structured
# delivery-override schema; they are not review verdict labels.
_REVIEW_BLOCK_VERDICT = "block"


def _review_gate_would_block(
    run_path: Path | None,
    events: list[dict[str, object]],
) -> bool:
    """True when ``trw_deliver`` would HARD-BLOCK on the review gate (F4 parity).

    The summary previously claimed ``BLOCKED: review required`` whenever no
    review verdict was recorded, but ``trw_deliver`` only hard-blocks review in
    a narrow set of states. This predicate mirrors the EXACT enforcement set the
    deliver path computes (round-2 transport e2e F4), reusing the same gate
    helpers so the preview can never diverge from enforcement:

    - ``_check_review_gate`` → ``review_block`` (verdict=block + critical on a
      STANDARD/COMPREHENSIVE run, OR no review on a STANDARD/COMPREHENSIVE run
      when ``review_gate_mode=block``).
    - ``_check_integration_review_gate`` → ``integration_review_block``.
    - ``_check_review_file_count_gate`` → ``review_scope_block`` (>5 files
      modified with no review).

    The unforced "missing review under warn-mode" case is NOT a block — deliver
    succeeds there — so it must not be reported as BLOCKED. Fail-open: any error
    returns False (no spurious block in the preview), matching the deliver
    gates' own fail-open posture.
    """
    if run_path is None:
        return False
    try:
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._delivery_helpers import (
            _check_integration_review_gate,
            _check_review_file_count_gate,
            _check_review_gate,
        )

        reader = FileStateReader()
        review_block, _warning, _advisory = _check_review_gate(run_path, reader)
        if review_block:
            return True
        int_block, _int_warning = _check_integration_review_gate(run_path, reader)
        if int_block:
            return True
        return bool(_check_review_file_count_gate(run_path, events))
    except Exception:  # justified: fail-open — preview must never raise or spurious-block
        logger.debug("review_gate_block_preview_failed", exc_info=True)
        return False


def _build_gate_ready(events: list[dict[str, object]]) -> bool:
    """True when the deliver-time build gate would be satisfied (FR01).

    Reuses ``_delivery_build_gates._build_passed`` for the event predicate so
    the status-time readiness cannot drift from deliver-time enforcement. It
    ALSO mirrors the deliver gate's ``build_check_enabled`` short-circuit
    (_delivery_build_gates._check_build_and_work_events): when build-check is
    disabled, ``trw_build_check`` never logs a ``build_check_complete`` event and
    the delivery gate skips the build requirement entirely — so deliver would
    pass. Without this guard, status would report ``build_gate_ready=False`` (and
    a "run trw_build_check()" advisory) while deliver actually ALLOWS, producing
    the exact false-signal retry cycle the PRD exists to prevent.
    """
    try:
        from trw_mcp.models.config import get_config

        if not get_config().build_check_enabled:
            # trw:intentional build gate is disabled, so deliver would allow —
            # status must agree (ready) rather than emit a spurious BLOCKED.
            return True
    except Exception:  # justified: fail-open, config read failure falls back to the event check
        logger.debug("build_gate_config_read_failed", exc_info=True)
    # Preview-gate parity (codex cross-model review): the deliver-time build gate
    # now ALSO treats a passing-but-stale build as a warning/block (a file edited
    # AFTER the last passing build — FRAMEWORK.md §"Build evidence MUST postdate
    # the last change"). The status preview must honor the SAME branch or it would
    # report build_gate_ready=True while deliver actually blocks, reintroducing
    # the exact false-signal divergence PRD-QUAL-105 exists to prevent.
    if not any(_build_passed(ev) for ev in events):
        return False
    return not _build_evidence_is_stale(events)


def _review_gate_ready(state: CeremonyState) -> bool:
    """True when review was called and the verdict is not a hard block (FR02).

    Reads ``review_called`` / ``review_verdict`` defensively: a stale or older
    ``ceremony_state.json`` may omit these fields, in which case the parsed
    ``CeremonyState`` defaults to ``review_called=False`` — i.e. not ready,
    never a crash.
    """
    if not getattr(state, "review_called", False):
        return False
    verdict = getattr(state, "review_verdict", None)
    return not (isinstance(verdict, str) and verdict.strip().lower() == _REVIEW_BLOCK_VERDICT)


def _summarize_deliver_gate(
    build_ready: bool,
    review_ready: bool,
    review_would_block: bool,
) -> str:
    """Render the single highest-priority blocking action, mirroring enforcement.

    Build evidence is the higher-priority gate: an agent with no passing build
    cannot meaningfully deliver regardless of review, so a missing build is
    surfaced ahead of a missing review.

    F4 enforcement parity (round-2 transport e2e): the summary must report
    ``BLOCKED: review`` ONLY when ``trw_deliver`` would actually HARD-BLOCK on
    the review gate (``review_would_block`` — verdict=block / scope rule / block
    mode). When a review simply was not recorded but deliver would still SUCCEED
    (warn-mode, sub-STANDARD complexity), the summary must NOT claim BLOCKED —
    that over-claim drove a false deliver-then-retry cycle. Instead it reports
    READY with an advisory mention so the missing review is still visible.

    Note: the summary reflects the gate posture WITHOUT the ``trw_deliver``
    ``allow_unverified=True`` override applied. An agent may still deliver a
    BLOCKED gate by passing ``allow_unverified=true`` plus a valid, unexpired
    structured acceptable-failure record in ``unverified_reason``;
    the summary describes the unforced state, not a hard prohibition.
    """
    if not build_ready:
        return "BLOCKED: no passing build check — run trw_build_check()"
    if review_would_block:
        return "BLOCKED: review required — run trw_review()"
    if not review_ready:
        # Deliver would SUCCEED (review not enforced here), but no review was
        # recorded — surface it as an advisory, not a block.
        return "READY (advisory: no review recorded — run trw_review())"
    return "READY"


def compute_deliver_gate_status(
    events: list[dict[str, object]],
    trw_dir: Path,
    run_path: Path | None = None,
) -> DeliverGateScanDict:
    """Compute deliver-gate readiness from already-read events + ceremony state.

    Reuses the ``events`` list ``trw_status`` already read (no extra I/O beyond
    ``ceremony_state.json``, which ``_apply_ceremony_status`` reads anyway — see
    NFR01). The caller's fail-open wrapper (:func:`apply_deliver_gate_status`)
    catches errors (FR04).

    Note on ceremony-state robustness: ``read_ceremony_state`` itself fails open
    to a default ``CeremonyState`` on a missing OR malformed ``ceremony_state.json``
    (it never raises), so those scenarios yield ``review_gate_ready=False`` rather
    than triggering the caller's except clause. The except clause (FR04) covers
    genuinely unexpected failures elsewhere in the scan (e.g. a broken
    ``resolve_trw_dir``/``_build_passed`` import or a corrupt events record).

    Returns a dict with ``build_gate_ready`` (bool), ``review_gate_ready``
    (bool), and ``deliver_gate_summary`` (str).
    """
    state = read_ceremony_state(trw_dir)
    build_ready = _build_gate_ready(events)
    review_ready = _review_gate_ready(state)
    review_would_block = _review_gate_would_block(run_path, events)
    return {
        "build_gate_ready": build_ready,
        "review_gate_ready": review_ready,
        "deliver_gate_summary": _summarize_deliver_gate(build_ready, review_ready, review_would_block),
    }


def apply_deliver_gate_status(
    result: dict[str, object],
    events: list[dict[str, object]],
    run_path: Path | None = None,
) -> None:
    """Merge deliver-gate readiness fields into a ``trw_status`` result in place.

    Fail-open per FR04: any error in the scan logs ``deliver_gate_scan_failed``
    and leaves ``result`` untouched (the three fields stay absent) rather than
    propagating — ``trw_status`` runs on every resume and must never fail here.
    Omitted entirely for runs with no events yet (the gate is trivially
    not-ready; Non-Goal). Resolves ``trw_dir`` via ``_paths.resolve_trw_dir`` at
    call time so test monkeypatches on that seam propagate.

    ``run_path`` (optional) lets the review-gate preview mirror deliver-time
    enforcement (F4): the would-it-block review decision reads the run's
    ``review.yaml`` / complexity / integration-review / file-count scope. When
    absent, the preview degrades to advisory-only for review (no spurious
    BLOCKED), never a crash.
    """
    if not events:
        return
    try:
        from trw_mcp.state._paths import resolve_trw_dir

        gate_status = compute_deliver_gate_status(events, resolve_trw_dir(), run_path)
        result["build_gate_ready"] = gate_status["build_gate_ready"]
        result["review_gate_ready"] = gate_status["review_gate_ready"]
        result["deliver_gate_summary"] = gate_status["deliver_gate_summary"]
    except Exception:  # justified: fail-open per FR04, gate audit is advisory
        logger.warning("deliver_gate_scan_failed", exc_info=True)
