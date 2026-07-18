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

- ``critical=True`` steps re-raise on failure (the pre-refactor code left them
  un-``try``-wrapped, so a raise propagated); the rest are fail-open (logged at
  debug, then skipped) exactly like the old inline ``except`` blocks.
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
from trw_mcp.tools._ceremony_degradations import DegradationCollector
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
    facade; the driver resolves it by name at call time. ``critical`` steps
    re-raise on failure (matching the pre-refactor un-``try``-wrapped steps);
    the rest are fail-open. ``timed`` steps record an entry in
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


def run_steps(steps: Sequence[Step], sctx: SessionStartContext, facade: ModuleType) -> None:
    """Run each step in order, resolving its adapter via ``getattr(facade, attr)``.

    The call-time ``getattr`` (never a captured import-time reference) is the
    invariant that keeps every ``ceremony.<name>`` monkeypatch propagating.
    Fail-open for non-critical steps; critical steps re-raise. Per-step wall
    time is recorded into ``sctx.step_durations_ms`` for ``timed`` steps.
    """
    for step in steps:
        started_at = time.monotonic()
        try:
            fn = getattr(facade, step.attr)
            fn(sctx)
        except Exception as exc:  # justified: fail-open, one step must not block session start
            if step.critical:
                raise
            # Was a silent ``logger.debug`` — now recorded as a typed, counted
            # degradation so the swallow is observable in the payload. Behaviour
            # is unchanged: a non-critical step failure still does NOT flip
            # ``success`` (only ``errors`` does).
            sctx.degradations.record(step.key, exc)
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


def _ss_sanitize_maintain(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_sanitize_and_maintain

    maintenance = step_sanitize_and_maintain()
    results = cast("dict[str, object]", sctx.results)
    for key in (
        "update_advisory",
        "auto_upgrade",
        "auto_upgrade_check_deferred",
        "stale_runs_closed",
        "stale_runs_deferred",
        "embeddings_advisory",
        "embeddings_backfill",
        "embeddings_backfill_scheduled",
        "embeddings_backfill_deferred",
        "wal_checkpoint_deferred",
    ):
        if key in maintenance:
            results[key] = maintenance[key]


def _ss_phase_recall(sctx: SessionStartContext) -> None:
    step_auto_recall_orchestrated(sctx.query, sctx.config, sctx.run_dir, sctx.results)


def _ss_embed_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools._ceremony_helpers import step_embed_health

    sctx.results["embed_health"] = step_embed_health()


def _ss_sync_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony
    from trw_mcp.tools._ceremony_helpers import step_sync_health

    sctx.results["sync_health"] = step_sync_health(_ceremony.resolve_trw_dir(), sctx.config)


def _ss_assertion_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    ah = step_assertion_health(_ceremony.resolve_trw_dir(), sctx.degradations)
    if ah is not None:
        sctx.results["assertion_health"] = ah


def _ss_graph_health(sctx: SessionStartContext) -> None:
    from trw_mcp.tools import ceremony as _ceremony

    gh = step_graph_health(_ceremony.resolve_trw_dir(), sctx.degradations)
    if gh is not None:
        sctx.results["graph_health"] = gh


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
)
