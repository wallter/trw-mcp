"""Declarative step-table driver for ``trw_session_start``.

Belongs to the ``ceremony.py`` facade. The ``_ss_*`` adapters and
``SESSION_START_STEPS`` are re-exported there so the driver can resolve each
step via ``getattr(ceremony, attr)`` at CALL TIME — that call-time lookup is
what preserves the ~190 test monkeypatch seams (``ceremony.resolve_trw_dir``,
``ceremony.step_resolve_profile``, ``ceremony.step_first_session_marker`` …).

Before this module, ``trw_session_start`` was a single ~250-line body with 16
inline ``_record_step`` timing blocks and 9 ad-hoc ``except Exception`` fail-open
swallows. Those are folded here into ONE uniform driver (:func:`run_steps`) plus
a small adapter per step. Behaviour is preserved exactly:

- ``critical=True`` steps fail the payload verdict (PRD-CORE-263-FR01: the
  runner records the typed reason and appends to ``errors``, and never lets the
  exception escape the mandated first tool call); the rest are fail-open
  (recorded as a degradation, then skipped) exactly like the old inline
  ``except`` blocks.
- ``timed=False`` reproduces the two steps the old code never recorded a
  duration for (``first_session_marker`` and ``graph_health``) — so
  ``step_durations_ms`` keeps the same key set.
"""

from __future__ import annotations

import time
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import SessionStartResultDict
from trw_mcp.state._paths import TRWCallContext
from trw_mcp.tools._ceremony_degradations import (
    DegradationCollector,
)
from trw_mcp.tools._ceremony_degradations import (
    SessionStartStepError as SessionStartStepError,
)
from trw_mcp.tools._ceremony_reconcile_step import step_reconcile_local_writes
from trw_mcp.tools._ceremony_session_start_steps import (
    step_assertion_health,
    step_auto_recall_orchestrated,
    step_graph_health,
    step_pipeline_health_advisory,
    step_recall_learnings,
    step_run_resolve,
    step_surface_stamp,
)

if TYPE_CHECKING:
    from types import ModuleType

    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Step:
    """One entry in the session-start step table.

    ``attr`` is the name of the ``_ss_*`` adapter re-exported on the ceremony
    facade; the driver resolves it by name at call time. A ``critical`` step's
    failure makes the payload verdict false and carries a typed reason naming
    the step; the rest are fail-open. ``timed`` steps record an entry in
    ``step_durations_ms``.
    """

    key: str
    attr: str
    critical: bool = False
    timed: bool = True


@dataclass
class SessionStartContext:
    """Mutable state threaded through the session-start steps.

    ``run_dir`` / ``call_ctx`` are populated by the ``run_resolve`` step and
    read by every later step, mirroring the local variables the old inline body
    passed between blocks.
    """

    query: str
    config: TRWConfig
    ctx: object | None
    is_focused: bool
    results: SessionStartResultDict
    errors: list[str]
    step_durations_ms: dict[str, float] = field(default_factory=dict)
    run_dir: Path | None = None
    call_ctx: TRWCallContext | None = None
    # mcp-x-failopen: typed fail-open degradation collector for this call. The
    # driver records every non-critical step failure here (instead of a silent
    # debug log) and the result-bearing step adapters thread it through, so the
    # previously-invisible swallows become one enumerable ``degradations`` array
    # in the session_start payload. Never flips ``success``.
    degradations: DegradationCollector = field(default_factory=DegradationCollector)


#: The one wording for a critical-step failure, so the payload reason, the log
#: line and the test that asserts it cannot drift apart (PRD-CORE-263-NFR03).
CRITICAL_STEP_REASON_PREFIX = "critical step"


def critical_step_reason(step_key: str, cause: BaseException) -> str:
    """The typed reason a critical step failure contributes to ``errors``.

    Names the step (so an agent reading ``success: false`` knows WHICH step
    failed) and the error class (so it knows what kind of failure it was).
    """
    return f"{CRITICAL_STEP_REASON_PREFIX} {step_key} failed: {type(cause).__name__}: {cause}"


def run_steps(steps: Sequence[Step], sctx: SessionStartContext, facade: ModuleType) -> None:
    """Run each step in order, resolving its adapter via ``getattr(facade, attr)``.

    The call-time ``getattr`` (never a captured import-time reference) is the
    invariant that keeps every ``ceremony.<name>`` monkeypatch propagating.
    Per-step wall time is recorded into ``sctx.step_durations_ms`` for ``timed``
    steps.

    **This is the only place that decides what a step failure means**
    (PRD-CORE-263-FR01):

    - ``critical`` — the failure is recorded as a degradation AND appended to
      ``sctx.errors``, which is what makes the payload verdict false. It is
      NEVER re-raised. DR-001: ``trw_session_start`` is the mandated first
      action of every session, and an exception escaping it removes the agent's
      only path to run state, recall and the framework reminder — trading a
      truthfulness defect for an availability one is not an improvement, so the
      runner degrades the payload instead. (Before PRD-CORE-263 this branch
      re-raised, which is what its docstring said; it was also unreachable,
      because all five critical step bodies swallowed first.)
    - non-critical — a degradation entry, verdict untouched.
    """
    for step in steps:
        started_at = time.monotonic()
        try:
            fn = getattr(facade, step.attr)
            fn(sctx)
        except Exception as exc:  # justified: fail-open, one step must not block session start
            # Report the ORIGINAL error class: a step that refused to classify
            # its own failure hands it over wrapped, and an ``error_class`` of
            # ``SessionStartStepError`` on every critical entry would erase the
            # field an operator triages by. DEF-03: unwrap in a LOOP, not just
            # once — a step that calls another already-wrapping step function
            # (e.g. ``phase_recall`` calling ``step_phase_auto_recall``) could
            # otherwise nest two layers deep and leave ``cause`` as the INNER
            # ``SessionStartStepError`` instead of the real failure. The call
            # sites are also fixed to never double-wrap; this loop is the
            # runner's own belt to that brace.
            cause: BaseException = exc
            while isinstance(cause, SessionStartStepError):
                cause = cause.cause
            # Was a silent ``logger.debug`` — now recorded as a typed, counted
            # degradation so the swallow is observable in the payload. For a
            # non-critical step this still does NOT flip ``success``.
            sctx.degradations.record(step.key, cause)
            if step.critical:
                sctx.errors.append(critical_step_reason(step.key, cause))
        finally:
            if step.timed:
                sctx.step_durations_ms[step.key] = round((time.monotonic() - started_at) * 1000.0, 2)
    sctx.degradations.into(cast("MutableMapping[str, object]", sctx.results))


# ── Per-step adapters ──────────────────────────────────────────────────
# Each takes the SessionStartContext and performs exactly the work the matching
# inline block did in the old trw_session_start body. Facade-looked-up helpers
# (resolve_trw_dir, step_resolve_profile, step_first_session_marker) go through
# ``ceremony`` so test monkeypatches propagate.


def _ss_recall(sctx: SessionStartContext) -> None:
    step_recall_learnings(sctx.query, sctx.config, sctx.results, sctx.errors)


def _ss_run_resolve(sctx: SessionStartContext) -> None:
    run_dir, call_ctx = step_run_resolve(sctx.ctx, sctx.results, sctx.errors)
    sctx.run_dir = run_dir
    sctx.call_ctx = call_ctx


def _ss_surface_stamp(sctx: SessionStartContext) -> None:
    session_id = str(sctx.call_ctx.session_id) if sctx.call_ctx is not None else ""
    # PRD-CORE-263-NFR04: the key is seeded BEFORE the call so a consumer that
    # read ``surface_snapshot_id`` on the pre-263 fail-open path still finds a
    # string. What changed is the verdict, not the key set: the step now raises
    # (FR01) and the runner records the failure, so an empty id no longer passes
    # for a stamp that happened.
    sctx.results["surface_snapshot_id"] = ""
    sctx.results["surface_snapshot_id"] = step_surface_stamp(sctx.run_dir, session_id, sctx.degradations)


def _ss_profile_resolve(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    _ceremony.step_resolve_profile(sctx.config, sctx.run_dir, sctx.results)


def _ss_log_event(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_log_session_event

    session_id = str(sctx.call_ctx.session_id) if sctx.call_ctx is not None else ""
    step_log_session_event(
        sctx.run_dir,
        cast("dict[str, object]", sctx.results),
        sctx.query,
        sctx.is_focused,
        session_id,
    )


def _ss_telemetry(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_telemetry_startup

    step_telemetry_startup(cast("dict[str, object]", sctx.results), sctx.run_dir)


def _ss_first_session_marker(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    sctx.results["first_session_emitted"] = _ceremony.step_first_session_marker()


def _ss_counter(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_increment_session_counter

    step_increment_session_counter()


#: Every key ``run_auto_maintenance()`` can produce that reaches the payload.
#:
#: PRD-CORE-263-FR04. This list used to name 11 of the 14 keys
#: ``AutoMaintenanceDict`` declared, and the three it omitted —
#: ``wal_checkpoint``, ``embeddings_coverage_ratio`` and
#: ``embedder_warmup_scheduled`` — were computed on the hot path of every
#: session and then dropped here. The WAL checkpoint in particular is called
#: unconditionally, so the work was paid for on every session start and its
#: outcome was unobservable.
MAINTENANCE_PROPAGATED_KEYS: tuple[str, ...] = (
    "update_advisory",
    "auto_upgrade",
    "auto_upgrade_check_deferred",
    "stale_runs_closed",
    "stale_runs_deferred",
    "embeddings_advisory",
    "embeddings_backfill",
    "embeddings_backfill_scheduled",
    "embeddings_backfill_deferred",
    "embeddings_backfill_not_performed",  # PRD-CORE-263 DEF-11
    # PRD-CORE-263-FR04: the three that were computed and dropped.
    "embedder_warmup_scheduled",
    "embeddings_coverage_ratio",
    "wal_checkpoint",
    "pending_learns_replayed",
    "pending_learns_deferred",
    # PRD-CORE-257: the expired-bound list and the per-step outcome map are
    # top-level response keys, not log-only diagnostics — FR03 requires the
    # response to name the step whose bound fired.
    "deferral_expired_ran",
    "step_outcomes",
)

#: Keys the maintenance sweep produces that are DELIBERATELY not propagated.
#:
#: Empty today, and that is the honest answer rather than an oversight: nothing
#: the sweep computes is currently internal-only. It exists as the other half of
#: the totality assertion in ``tests/test_ceremony_helpers_auto_maintenance.py``
#: (``test_every_maintenance_key_is_propagated_or_declared_internal``):
#: propagated ∪ internal must equal the declared ``AutoMaintenanceDict`` keys,
#: so a NEW key classified in neither fails that test BY NAME instead of being
#: silently dropped the way the three above were.
MAINTENANCE_INTERNAL_KEYS: frozenset[str] = frozenset()


def _ss_sanitize_maintain(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_sanitize_and_maintain

    maintenance = step_sanitize_and_maintain()
    results = cast("dict[str, object]", sctx.results)
    # AutoMaintenanceDict is total=False and every key is optional, so the read
    # must be a ``.get`` rather than a subscript: a maintenance step that
    # produced no result leaves its key absent, and indexing it would be a
    # KeyError on the mandated first action.
    for key in MAINTENANCE_PROPAGATED_KEYS:
        value = maintenance.get(key)
        if value is not None:
            results[key] = value


def _ss_phase_recall(sctx: SessionStartContext) -> None:
    step_auto_recall_orchestrated(sctx.query, sctx.config, sctx.run_dir, sctx.results)


def _ss_embed_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_embed_health

    sctx.results["embed_health"] = step_embed_health()


def _ss_sync_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_helpers import step_sync_health

    sctx.results["sync_health"] = step_sync_health(_ceremony.resolve_trw_dir(), sctx.config, sctx.degradations)


def _ss_assertion_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    ah = step_assertion_health(_ceremony.resolve_trw_dir(), sctx.degradations, sctx.config)
    if ah is not None:
        sctx.results["assertion_health"] = ah


def _ss_graph_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    gh = step_graph_health(_ceremony.resolve_trw_dir(), sctx.degradations)
    if gh is not None:
        sctx.results["graph_health"] = gh


def _ss_reconcile_local_writes(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    sctx.results["reconciled_local_writes"] = step_reconcile_local_writes(
        _ceremony.resolve_trw_dir(),
        sctx.degradations,
    )


def _ss_handoff_readback(sctx: SessionStartContext) -> None:
    """PRD-CORE-249-FR03 — project-scoped open handoff rows.

    Placed after ``run_resolve`` so the project root is settled. Non-critical:
    an unfinished-work advisory must never take down the mandated first action.
    """
    from trw_mcp.tools._project_handoff_readback import step_handoff_readback

    sctx.results["open_handoff"] = step_handoff_readback()


def _ss_moved_checkout(sctx: SessionStartContext) -> None:
    """PRD-CORE-253-FR01 — a checkout whose rows are one rename away.

    The key is omitted only for ``status="absent"``, so a normal session carries
    no extra payload and the observation is salient when it does appear. A
    ``not_measured`` readback DOES set the key: an omitted key used to mean both
    "no move" and "the probe failed", which is the ambiguity the step removes.
    """
    from trw_mcp.tools._moved_checkout_readback import step_moved_checkout

    observed = step_moved_checkout()
    if observed.get("status") != "absent":
        sctx.results["moved_checkout"] = observed


def _ss_pipeline_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    step_pipeline_health_advisory(_ceremony.resolve_trw_dir(), cast("dict[str, object]", sctx.results), sctx.config)


# ── The table (order is load-bearing — matches the old inline sequence) ──
SESSION_START_STEPS: tuple[Step, ...] = (
    Step("recall", "_ss_recall", critical=True),
    Step("run_resolve", "_ss_run_resolve", critical=True),
    Step("surface_stamp", "_ss_surface_stamp", critical=True),
    Step("profile_resolve", "_ss_profile_resolve", critical=True),
    Step("log_event", "_ss_log_event"),
    Step("telemetry", "_ss_telemetry"),
    Step("first_session_marker", "_ss_first_session_marker", timed=False),
    Step("counter", "_ss_counter"),
    Step("sanitize_maintain", "_ss_sanitize_maintain"),
    Step("phase_recall", "_ss_phase_recall", critical=True),
    Step("embed_health", "_ss_embed_health"),
    Step("sync_health", "_ss_sync_health"),
    Step("assertion_health", "_ss_assertion_health"),
    Step("graph_health", "_ss_graph_health", timed=False),
    Step("pipeline_health", "_ss_pipeline_health"),
    # PRD-CORE-247-FR05. Non-critical: a reconciliation report is diagnostic, so
    # its failure records a degradation and leaves success: true.
    Step("reconcile_local_writes", "_ss_reconcile_local_writes"),
    # PRD-CORE-249-FR03. Non-critical + timed: the readback is an advisory about
    # work earlier runs deferred, and its failure must never block session start.
    Step("handoff_readback", "_ss_handoff_readback"),
    # PRD-CORE-253-FR01. Non-critical: a possible-rename advisory is diagnostic
    # and must never take down the mandated first action. Last, so it cannot
    # delay anything an agent needs to start work.
    Step("moved_checkout", "_ss_moved_checkout"),
)
