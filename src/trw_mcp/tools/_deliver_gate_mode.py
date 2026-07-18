"""Task-type-aware deliver gate mode — PRD-CORE-184-FR03.

Belongs to the ``_delivery_helpers.py`` facade. Re-exported there for
back-compat and a single import point.

Implements the ``deliver_gate_mode`` (advisory | block_coding | block_all)
dispatch that conditions a missing-build-check block on the run's
``task_type``. Default is ``block_coding`` (flipped from ``advisory``
2026-06-10): coding/rca/eval runs with work events and no recorded build
check are blocked; docs/research/planning/unknown never block. All logic
here is pure/fail-open on unknown modes; the structured override path
(``allow_unverified`` + a schema-valid ``unverified_reason``) always remains open at the
``trw_deliver`` call site.
"""

from __future__ import annotations

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import DeliveryGatesDict

logger = structlog.get_logger(__name__)

# Task types that produce a build artifact and therefore should be gated when
# ``deliver_gate_mode`` is block_coding / block_all. docs/research/planning have
# no build artifact -> always advisory. ``unknown`` is conservative (advisory)
# — never block an uncertain classification.
_BUILD_ARTIFACT_TASK_TYPES: frozenset[str] = frozenset({"coding", "rca", "eval"})


def resolve_deliver_gate_decision(
    *,
    mode: str,
    task_type: str,
    build_check_missing: bool,
) -> bool:
    """Return True when delivery should be BLOCKED for a missing build check.

    Pure dispatch (no I/O):
      - ``advisory``     -> never block (current behavior).
      - ``block_coding`` -> block when ``task_type`` expects a build artifact
                            (coding/rca/eval) and the build check is missing.
      - ``block_all``    -> same artifact-typed set as block_coding (docs /
                            research / planning are excluded by design).
    Any unrecognised mode fails open (no block).
    """
    if not build_check_missing:
        return False
    if mode == "advisory":
        return False
    if mode in {"block_coding", "block_all"}:
        return task_type in _BUILD_ARTIFACT_TASK_TYPES
    # Unknown mode: fail-open (NFR02).
    return False


def apply_deliver_gate_mode(
    result: DeliveryGatesDict,
    run_data: dict[str, object],
) -> None:
    """Set ``delivery_blocked``/``missing_gate`` per ``deliver_gate_mode``.

    Only called when the build gate already warned (build check missing). Reads
    the configured mode + per-task-type override, resolves the run's task_type
    from run.yaml, and asks :func:`resolve_deliver_gate_decision`. Fail-open.
    """
    try:
        config = get_config()
        task_type = str(run_data.get("task_type", "unknown")) or "unknown"
        overrides = config.deliver_gate_task_type_overrides or {}
        mode = str(overrides.get(task_type, config.deliver_gate_mode))
        if resolve_deliver_gate_decision(mode=mode, task_type=task_type, build_check_missing=True):
            result["delivery_blocked"] = (
                f"Delivery blocked: no passing trw_build_check for task_type={task_type} "
                f"under deliver_gate_mode={mode}. Run project-native validation and record it "
                "with trw_build_check(), or override with allow_unverified=true + an unexpired "
                "acceptable-failure record (failed_command, residual_risk, owner, expiry_iso)."
            )
            result["missing_gate"] = "build_check"
            result["blocked_task_type"] = task_type
            logger.info(
                "deliver_gate_blocked",
                task_type=task_type,
                deliver_gate_mode=mode,
            )
    except Exception:  # justified: fail-open, gate-mode dispatch must not wedge delivery
        logger.warning("deliver_gate_mode_check_failed", exc_info=True)
