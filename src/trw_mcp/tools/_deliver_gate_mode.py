"""Evidence-keyed deliver gate mode — PRD-CORE-184-FR03 + PRD-CORE-246-FR03.

Belongs to the ``_delivery_helpers.py`` facade. Re-exported there for
back-compat and a single import point.

Implements the ``deliver_gate_mode`` (advisory | block_coding | block_all)
dispatch for a missing build check. Default is ``block_coding`` (flipped from
``advisory`` 2026-06-10). Under ``block_coding``/``block_all`` a missing build
check blocks when the run's ``task_type`` expects a build artifact
(coding/rca/eval) **OR** when the session recorded modifications to at least
``deliver_gate_unclassified_change_threshold`` distinct files — so an
unclassified or misclassified run that changed code still blocks
(PRD-CORE-246-FR03). The task-type clause is an OR, not a switch: no value of
the threshold restores a never-block-on-unknown posture.

Fail posture (PRD-CORE-246-NFR02): the gate is FAIL-CLOSED on its own evidence.
When the changed-file count cannot be computed
(:func:`count_session_changed_files` returns ``None``) the count is treated as
meeting the threshold and the gate blocks. Likewise, when the configured mode
cannot be READ at all the gate falls back to the field's declared default and
still evaluates the change-evidence clause (WD-05) — a corrupt config file is
not a way to turn the gate off. The structured override path
(``allow_unverified`` + a schema-valid ``unverified_reason``) is untouched and
remains the sanctioned release valve, so a fail-closed gate can only force the
evidence or the record — it can never wedge a session.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import DeliveryGatesDict

logger = structlog.get_logger(__name__)

# Task types that ALWAYS produce a build artifact and are therefore gated
# whenever ``deliver_gate_mode`` is block_coding / block_all, regardless of what
# the session's event stream recorded. Every OTHER task type — docs, research,
# planning, unknown — is gated by the change-evidence clause instead, so the
# gate's strength no longer depends on the task-type heuristic being right
# (PRD-CORE-246-FR03).
_BUILD_ARTIFACT_TASK_TYPES: frozenset[str] = frozenset({"coding", "rca", "eval"})


def count_session_changed_files(
    *,
    events: list[dict[str, object]] | None,
    run_path: Path,
    session_id: str | None,
) -> int | None:
    """Distinct files modified in the CURRENT session, or ``None`` if uncomputable.

    Reuses ``_delivery_event_checks._count_file_modified_current_session`` — the
    same helper the neighbouring review-scope gate trusts — so the framework
    keeps ONE notion of "code changed" rather than two divergent ones.

    ``None`` is the fail-closed signal, not an error: the caller passes it
    straight into :func:`resolve_deliver_gate_decision`, which treats an
    uncomputable count as meeting the threshold (PRD-CORE-246-NFR02).

    ``events=None`` means ``_read_run_events`` could not read the run's event
    log at all (WD-03). That is uncomputable, NOT zero: counting an unreadable
    log as 0 changed files walked straight past this function's own fail-closed
    branch, because no exception was ever raised to catch.
    """
    if events is None:
        logger.warning(
            "deliver_gate_change_count_uncomputable",
            outcome="fail_closed",
            reason="events_unreadable",
            run_path=str(run_path),
        )
        return None
    try:
        from trw_mcp.tools._delivery_event_checks import (
            _count_file_modified_current_session,
            _project_root_from_run,
        )

        return _count_file_modified_current_session(events, _project_root_from_run(run_path), session_id)
    except Exception:  # justified: fail-CLOSED, an uncomputable count blocks (FR03/NFR02)
        logger.warning("deliver_gate_change_count_uncomputable", outcome="fail_closed", exc_info=True)
        return None


def _meets_change_threshold(files_changed: int | None) -> bool:
    """True when the session's change evidence arms the gate.

    ``None`` (uncomputable) is treated as equal to the threshold — the single
    fail-closed component of PRD-CORE-246. The threshold itself is the typed,
    bounded ``deliver_gate_unclassified_change_threshold`` config field
    (``ge=1, le=1000``); a config-read failure also fails closed, because the
    gate must never weaken on an unreadable tunable.
    """
    if files_changed is None:
        return True
    try:
        threshold = int(get_config().deliver_gate_unclassified_change_threshold)
    except Exception:  # justified: fail-CLOSED, an unreadable threshold must not weaken the gate
        logger.warning("deliver_gate_threshold_read_failed", outcome="fail_closed", exc_info=True)
        return True
    return files_changed >= threshold


def _typed_default_gate_mode() -> str:
    """The DECLARED default of ``TRWConfig.deliver_gate_mode``, read from the field.

    Introspected rather than copied, so this can never drift from the shipped
    default the way a hardcoded ``"block_coding"`` string would. If the model
    itself cannot be introspected the framework has no way to name the project's
    policy at all, so the strongest posture is chosen instead of a stale copy —
    ``block_all`` and ``block_coding`` share one predicate in
    :func:`resolve_deliver_gate_decision`, so this is not a stricter RULE, only a
    refusal to guess a weaker one.
    """
    try:
        from trw_mcp.models.config import TRWConfig

        declared = TRWConfig.model_fields["deliver_gate_mode"].default
        if isinstance(declared, str) and declared:
            return declared
    except Exception:  # justified: fail-CLOSED, an unintrospectable model must not weaken the gate
        logger.warning("deliver_gate_mode_default_unreadable", exc_info=True)
    return "block_all"


def resolve_gate_mode_with_source(task_type: str) -> tuple[str, bool]:
    """``(mode, from_fallback)`` — the effective mode and whether config was READ.

    ``from_fallback=True`` means no configured value could be obtained (a
    malformed ``.trw/config.yaml``, a validation error, an unavailable config
    layer). The mode returned in that case is the field's own declared default,
    NOT ``advisory``: mapping every exception to ``advisory`` meant a project
    could disable its own delivery gate by corrupting one YAML file, since
    :func:`resolve_deliver_gate_decision` returns ``False`` for ``advisory``
    before the change-evidence clause is ever evaluated (WD-05). A project that
    explicitly sets ``advisory`` still gets ``advisory`` — that path reads
    config successfully and never sets the flag.
    """
    try:
        config = get_config()
        overrides = config.deliver_gate_task_type_overrides or {}
        return str(overrides.get(task_type, config.deliver_gate_mode)), False
    except Exception:  # justified: fail-CLOSED, an unreadable mode falls back to the declared default
        fallback = _typed_default_gate_mode()
        logger.warning(
            "deliver_gate_mode_unreadable",
            task_type=task_type,
            outcome=fallback,
            source="field_default",
            exc_info=True,
        )
        return fallback, True


def resolve_gate_mode(task_type: str) -> str:
    """The effective deliver-gate mode for ``task_type`` (PRD-CORE-184 policy).

    ``deliver_gate_task_type_overrides`` first, then ``deliver_gate_mode``. Every
    gate that keys on the mode reads it HERE -- the build gate below, the
    PRD-CORE-249 plan-acceptance gate, the safety-critical gate, and the
    trw_status preview -- so the framework keeps one notion of "is this run under
    a blocking policy" rather than several lookups that can drift.

    Callers that must ALSO know whether the value came from config or from the
    fallback use :func:`resolve_gate_mode_with_source`.
    """
    return resolve_gate_mode_with_source(task_type)[0]


def resolve_deliver_gate_decision(
    *,
    mode: str,
    task_type: str,
    build_check_missing: bool,
    files_changed: int | None,
    mode_from_fallback: bool = False,
) -> bool:
    """Return True when delivery should be BLOCKED for a missing build check.

    Pure dispatch apart from the bounded threshold read:
      - ``advisory``     -> never block.
      - ``block_coding`` -> block when the ``task_type`` expects a build artifact
                            (coding/rca/eval) OR the session's distinct
                            modified-file count meets
                            ``deliver_gate_unclassified_change_threshold``.
      - ``block_all``    -> same predicate as ``block_coding``.
    ``files_changed=None`` means the count could not be computed and blocks
    (fail-closed, PRD-CORE-246-NFR02). Any unrecognised mode fails open.

    ``mode_from_fallback=True`` (from :func:`resolve_gate_mode_with_source`)
    means NOBODY could read the configured mode. The change-evidence clause is
    then evaluated whatever the fallback happens to name, so the gate's strength
    can never depend on a config file being parseable — an ``advisory``-looking
    fallback must not retire the clause that measures actual change (WD-05).
    A project's EXPLICIT ``advisory`` is untouched: that value is read from
    config and arrives with the flag clear.
    """
    if not build_check_missing:
        return False
    if mode in {"block_coding", "block_all"}:
        return task_type in _BUILD_ARTIFACT_TASK_TYPES or _meets_change_threshold(files_changed)
    if mode_from_fallback:
        return task_type in _BUILD_ARTIFACT_TASK_TYPES or _meets_change_threshold(files_changed)
    if mode == "advisory":
        return False
    # Unknown mode from a READABLE config: fail-open (NFR02).
    return False


def apply_deliver_gate_mode(
    result: DeliveryGatesDict,
    run_data: dict[str, object],
    files_changed: int | None,
) -> None:
    """Set ``delivery_blocked``/``missing_gate`` per ``deliver_gate_mode``.

    Only called when the build gate already warned (build check missing). Reads
    the configured mode + per-task-type override, resolves the run's task_type
    from run.yaml, and asks :func:`resolve_deliver_gate_decision` with the
    session's change evidence. ``files_changed=None`` blocks (fail-closed).

    The outer handler stays fail-OPEN for genuinely unexpected faults in this
    dispatch (a raise here must not wedge delivery). The fail-CLOSED components
    live where the gate's own evidence is decided:
    :func:`_meets_change_threshold` (uncomputable count) and
    :func:`resolve_gate_mode_with_source` (unreadable mode -> the declared
    default, carried through as ``mode_from_fallback``).
    """
    try:
        task_type = str(run_data.get("task_type", "unknown")) or "unknown"
        mode, mode_from_fallback = resolve_gate_mode_with_source(task_type)
        if resolve_deliver_gate_decision(
            mode=mode,
            task_type=task_type,
            build_check_missing=True,
            files_changed=files_changed,
            mode_from_fallback=mode_from_fallback,
        ):
            result["delivery_blocked"] = (
                f"Delivery blocked: no passing trw_build_check for task_type={task_type} "
                f"under deliver_gate_mode={mode} "
                f"(files modified this session: {'uncomputable' if files_changed is None else files_changed}). "
                "Run project-native validation and record it "
                "with trw_build_check(), or override with allow_unverified=true + an unexpired "
                "acceptable-failure record (failed_command, residual_risk, owner, expiry_iso)."
            )
            result["missing_gate"] = "build_check"
            result["blocked_task_type"] = task_type
            logger.info(
                "deliver_gate_blocked",
                task_type=task_type,
                deliver_gate_mode=mode,
                files_changed=files_changed,
            )
    except Exception:  # justified: fail-open, gate-mode dispatch must not wedge delivery
        logger.warning("deliver_gate_mode_check_failed", exc_info=True)
