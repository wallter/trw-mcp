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

logger = structlog.get_logger(__name__)


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
    STANDARD/COMPREHENSIVE and has no ``meta/review.yaml`` — surfaced regardless
    of ``review_gate_mode`` so even warn-mode agents see it prominently rather
    than buried in a gate warning. Returns None otherwise. Fail-open.
    """
    # Resolve _read_complexity_class via the facade so the helper picks up the
    # canonical (and test-patchable) implementation from _delivery_helpers.
    from trw_mcp.tools import _delivery_helpers as _dh

    try:
        if (run_path / "meta" / "review.yaml").exists():
            return None
        complexity_class = _dh._read_complexity_class(run_path, reader)
        if complexity_class in ("STANDARD", "COMPREHENSIVE"):
            return (
                f"No trw_review recorded for this {complexity_class} run. Run trw_review() now "
                "to satisfy the adversarial review gate before delivering."
            )
    except Exception:  # justified: fail-open — nudge is advisory and must not block delivery
        logger.debug("review_nudge_resolution_failed", exc_info=True)
    return None
