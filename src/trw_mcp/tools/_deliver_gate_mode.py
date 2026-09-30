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

import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.typed_dicts import DeliveryGatesDict

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# Task types that ALWAYS produce a build artifact and are therefore gated
# whenever ``deliver_gate_mode`` is block_coding / block_all, regardless of what
# the session's event stream recorded. Every OTHER task type — docs, research,
# planning, unknown — is gated by the change-evidence clause instead, so the
# gate's strength no longer depends on the task-type heuristic being right
# (PRD-CORE-246-FR03).
_BUILD_ARTIFACT_TASK_TYPES: frozenset[str] = frozenset({"coding", "rca", "eval"})


def gate_mode_blocks_task(config: TRWConfig, task_type: str) -> bool:
    """True when deliver_gate_mode resolves to a block posture for this task_type.

    Runtime callers: ``trw_mcp.tools._prd_transition_gate.evaluate_transition_gate``
    (PRD-CORE-213) and ``trw_mcp.tools._deliver_requirement_drift.drift_blocks_task``
    (PRD-CORE-321 FR05), both reached from ``trw_deliver`` through
    ``evaluate_delivery_gates``. Moved here from ``_prd_transition_gate`` so both
    gates read the one rule next to the ``_BUILD_ARTIFACT_TASK_TYPES`` it uses.

    Soundness scope: proves the configured mode (the per-task-type override map
    first, then ``deliver_gate_mode``) is ``block_coding``/``block_all`` and the
    task type is build-bearing (coding/rca/eval). It is NOT the build gate's
    predicate: PRD-CORE-246-FR03 widened ``resolve_deliver_gate_decision`` to
    also block on recorded file modifications, so the build gate fires for a
    docs | research | planning | unknown run that changed code while this rule
    does not. The narrower scope is deliberate: both callers key on a claim
    (a PRD ``->implemented`` transition, a change to approved requirements) that
    only a build-bearing regime makes (PRD-CORE-246-FR09).

    ``deliver_gate_mode`` is read straight off the config, never through
    ``getattr(config, "deliver_gate_mode", "advisory")``, whose fallback
    contradicted the field's real default of ``block_coding``: a gate must not
    carry a second, weaker copy of a policy default.
    """
    overrides = config.deliver_gate_task_type_overrides or {}
    mode = str(overrides.get(task_type, config.deliver_gate_mode))
    return mode in {"block_coding", "block_all"} and task_type in _BUILD_ARTIFACT_TASK_TYPES


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
            change_evidence_unknown,
        )

        repo_root = _project_root_from_run(run_path)
        if change_evidence_unknown(repo_root):
            logger.warning("deliver_gate_change_count_uncomputable", outcome="fail_closed", reason="jq_unavailable")
            return None
        return _count_file_modified_current_session(events, repo_root, session_id)
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
    reason: str | None = None,
) -> None:
    """Set ``delivery_blocked``/``missing_gate`` per ``deliver_gate_mode``.

    Only called when the build gate already warned (build check missing). Reads
    the configured mode + per-task-type override, resolves the run's task_type
    from run.yaml, and asks :func:`resolve_deliver_gate_decision` with the
    session's change evidence. ``files_changed=None`` blocks (fail-closed).

    Fail-CLOSED throughout: :func:`_meets_change_threshold` (uncomputable
    count), :func:`resolve_gate_mode_with_source` (unreadable mode -> the
    declared default) and, for a genuinely unexpected fault in this dispatch,
    the outer handler, which blocks with the fault's type and site. The deliver
    gate is hard-tier (CONSTITUTION §1.a): a code bug never turns into a pass,
    and "must not wedge delivery" is the structured acceptable-failure override
    the block names, not a silent skip (E2E-GATE-MODE-FAIL-CLOSED).
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
            changed = "uncomputable" if files_changed is None else files_changed
            override = (
                "override with allow_unverified=true + an unexpired "
                "acceptable-failure record (failed_command, residual_risk, owner, expiry_iso)."
            )
            # INC-072: name WHY the build evidence does not count (content-stale, unreadable event log, none recorded)
            # -- the build gate's own warning -- instead of always claiming no passing check exists.
            result["delivery_blocked"] = (
                f"Delivery blocked for task_type={task_type} under deliver_gate_mode={mode} "
                f"(files modified this session: {changed}): {reason.rstrip()} Or {override}"
                if reason
                else f"Delivery blocked: no passing trw_build_check for task_type={task_type} "
                f"under deliver_gate_mode={mode} (files modified this session: {changed}). "
                f"Run project-native validation and record it with trw_build_check(), or {override}"
            )
            result["missing_gate"] = "build_check"
            result["blocked_task_type"] = task_type
            logger.info(
                "deliver_gate_blocked",
                task_type=task_type,
                deliver_gate_mode=mode,
                files_changed=files_changed,
            )
    except Exception as exc:  # justified: fail-CLOSED, a fault in the dispatch blocks with a named reason
        # Block FIRST, from constants only: the diagnostics below are best-effort and must never be able to
        # leave the result unblocked (codex r1). The type and site only -- an exception MESSAGE can carry
        # arbitrary payload (paths, config values).
        fault = type(exc).__name__
        result["delivery_blocked"] = _dispatch_fault_block(fault)
        result["missing_gate"] = "build_check"
        try:
            fault = f"{fault} at {_fault_site(exc)}"
            result["delivery_blocked"] = _dispatch_fault_block(fault)
        except Exception:  # trw-fail-silent-allow: the type-only block set above already stands
            pass
        try:
            logger.warning("deliver_gate_mode_check_failed", outcome="fail_closed", fault=fault, exc_info=True)
        except Exception:  # trw-fail-silent-allow: logging is diagnostics; the block set above already stands
            pass
    # SystemExit / KeyboardInterrupt / CancelledError are deliberately not caught: they abort the whole tool
    # call, so no delivery completes -- a cancelled call is never a pass.


def _dispatch_fault_block(fault: str) -> str:
    return (
        f"Delivery blocked: the deliver gate-mode check failed unexpectedly ({fault}), so a missing "
        "build check could not be judged acceptable. Record a passing trw_build_check(), or override "
        "with allow_unverified=true + an unexpired acceptable-failure record "
        "(failed_command, residual_risk, owner, expiry_iso)."
    )


def _fault_site(exc: BaseException) -> str:
    """``file:line in function`` of the innermost frame that raised, or ``unknown``."""
    frames = traceback.extract_tb(exc.__traceback__)
    if not frames:
        return "unknown"
    innermost = frames[-1]
    return f"{Path(innermost.filename).name}:{innermost.lineno} in {innermost.name}"


def resolve_unpinned_gate_decision(files_changed: int | None = None) -> tuple[bool, str]:
    """``(blocked, mode)`` for a delivery with NO run pin — PRD-FIX-140-FR04.

    ``check_delivery_gates`` returns before :func:`apply_deliver_gate_mode` when
    ``run_path`` is ``None``, so until now an unpinned delivery could only ever be
    WARNED — including one whose own session recorded a build check that FAILED.
    That gap was invisible because the bundled PreToolUse hook blocked the same
    condition from the client side; demoting the hook to a diagnostic
    (PRD-FIX-140-FR01) makes it reachable, so the decision moves here.

    The decision is the SAME predicate the pinned path uses, with the SAME
    change-evidence clause — ``task_type="unknown"`` (there is no run.yaml to
    classify, and PRD-CORE-246-FR03 gates ``unknown`` on change evidence rather
    than on the task-type heuristic) plus ``files_changed`` measured from the
    session-scoped surfaces by
    ``_delivery_event_checks.unpinned_session_changed_files``. So:

    * a session that recorded modifications to at least
      ``deliver_gate_unclassified_change_threshold`` distinct files, with no
      passing build record, BLOCKS — the rule CLAUDE.md and CONSTITUTION §1.a
      state, which admit no pinned/unpinned carve-out;
    * a session with no recorded modifications stays ADVISORY, so the docs-only
      over-block (L-eWzn) does not return;
    * ``files_changed=None`` (uncomputable evidence) BLOCKS, matching the pinned
      path's fail-closed posture. That is the default, so a caller with no count
      at all — the recorded-failure branch — gets the strict answer.

    An EXPLICIT ``advisory`` mode still never blocks, and an unreadable config
    still evaluates the clause. The block is STRUCTURED: ``allow_unverified`` +
    a valid acceptable-failure record still releases it.
    """
    mode, from_fallback = resolve_gate_mode_with_source("unknown")
    blocked = resolve_deliver_gate_decision(
        mode=mode,
        task_type="unknown",
        build_check_missing=True,
        files_changed=files_changed,
        mode_from_fallback=from_fallback,
    )
    return blocked, mode
