"""Ceremony status helpers for live MCP tool responses."""
# ruff: noqa: I001 - facade re-export imports stay grouped for module-size ratchet.

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._ceremony_progress_state import NudgeContext
from trw_mcp.state._paths import resolve_trw_dir
from trw_mcp.state.ceremony_progress import CeremonyState, read_ceremony_state
from trw_mcp.tools._ceremony_status_helpers import (
    _cached_bandit_weight as _cached_bandit_weight,
    _candidate_domains as _candidate_domains,
    _coerce_float as _coerce_float,
    _emit_nudge_surface_event as _emit_nudge_surface_event,
    _contextualize_candidates as _contextualize_candidates,
    _ContextualSelector as _ContextualSelector,
    _deterministic_fallback_text as _deterministic_fallback_text,
    _domain_match_score as _domain_match_score,
    _has_cached_learning_weights as _has_cached_learning_weights,
    _matches_inferred_domains as _matches_inferred_domains,
    _normalize_inferred_domains as _normalize_inferred_domains,
    _normalized_modified_files as _normalized_modified_files,
    _phase_match_score as _phase_match_score,
    _select_cached_or_deterministic_learning as _select_cached_or_deterministic_learning,
    _select_deterministic_fallback_learning as _select_deterministic_fallback_learning,
    _synthetic_nudge_learning_id as _synthetic_nudge_learning_id,
)
from trw_mcp.tools._ceremony_status_nudge import _try_learning_nudge_content as _try_learning_nudge_content
from trw_mcp.tools._ceremony_status_pool import (
    dispatch_contextual_messenger as dispatch_contextual_messenger,
)
from trw_mcp.tools._ceremony_status_pool import (
    resolve_pool_content as resolve_pool_content,
)
from trw_mcp.tools._ceremony_status_pool import (
    select_pool as select_pool,
)

logger = structlog.get_logger(__name__)
_WORKSPACE_CONFIG_CACHE: dict[tuple[str, int, tuple[str | None, ...]], TRWConfig] = {}

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.memory_pressure import WriterCensus


def _load_config_for_trw_dir(trw_dir: Path) -> TRWConfig:
    """Load config.yaml from the active workspace instead of the global singleton."""

    import os

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader

    config_path = trw_dir / "config.yaml"
    if not config_path.exists():
        return TRWConfig.model_validate({"trw_dir": str(trw_dir)})

    env_fingerprint = tuple(
        os.environ.get(key)
        for key in (
            "TRW_NUDGE_ENABLED",
            "TRW_NUDGE_MESSENGER",
            "TRW_NUDGE_DENSITY",
            "TRW_TARGET_PLATFORMS",
        )
    )
    try:
        cache_key = (str(config_path.resolve()), config_path.stat().st_mtime_ns, env_fingerprint)
        cached = _WORKSPACE_CONFIG_CACHE.get(cache_key)
        if cached is not None:
            return cached
    except OSError:
        cache_key = None

    try:
        overrides = FileStateReader().read_yaml(config_path)
        if not isinstance(overrides, dict):
            return TRWConfig.model_validate({"trw_dir": str(trw_dir)})
        filtered = {
            str(key): value
            for key, value in overrides.items()
            if value is not None and f"TRW_{str(key).upper()}" not in os.environ
        }
        filtered["trw_dir"] = str(trw_dir)
        loaded = TRWConfig(**filtered)  # type: ignore[arg-type]
        if cache_key is not None:
            if len(_WORKSPACE_CONFIG_CACHE) >= 16:
                _WORKSPACE_CONFIG_CACHE.clear()
            _WORKSPACE_CONFIG_CACHE[cache_key] = loaded
        return loaded
    except Exception:  # justified: fail-open, config read failure falls back to defaults
        logger.debug("workspace_config_load_failed", config_path=str(config_path), exc_info=True)
        return TRWConfig.model_validate({"trw_dir": str(trw_dir)})


def build_ceremony_status_line(state: CeremonyState) -> str:
    """Render project-wide progress, explicitly separate from run evidence."""
    parts = [
        "scope=project_aggregate (not current-run evidence)",
        "session_started" if state.session_started else "session_start_pending",
        f"phase={state.phase}",
        f"checkpoints={state.checkpoint_count}",
        f"learnings={state.learnings_this_session}",
    ]
    if state.build_check_result:
        parts.append(f"build={state.build_check_result}")
    if state.review_called:
        # A substantive review with no verdict must not render as a completed one.
        # `or 'recorded'` turned an empty verdict into the calmest possible word,
        # and it printed beside a live p0 count — alarming number, reassuring label.
        review_part = f"review={state.review_verdict or 'verdict_unrecorded'}"
        if state.review_p0_count:
            review_part = f"{review_part} p0={state.review_p0_count}"
        parts.append(review_part)
    if state.deliver_called:
        parts.append("deliver_called")
    return "; ".join(parts)


def _close_stale_nudge_streak(effective_dir: Path) -> None:
    """Close an open ``nudges`` deferral streak once pressure has cleared.

    Reads the ledger unconditionally (cheap: a small local JSON read) but
    only WRITES when there is actually an open streak to close, so a healthy
    machine that never deferred nudges pays a read, never a write.
    """
    from trw_mcp.state.deferral_ledger import read_ledger, record_completion

    entries, _state = read_ledger(effective_dir)
    entry = entries.get("nudges")
    if entry is not None and entry.deferred_since_ts is not None:
        record_completion(effective_dir, "nudges")


def _apply_nudge_pressure(
    response: dict[str, object],
    effective_dir: Path,
    cfg: TRWConfig,
    census: WriterCensus | None,
) -> tuple[bool, Callable[[], None] | None]:
    """Attach the nudge deferral advisory; return (suppressed, pending_completion).

    ``nudges`` is a PRD-CORE-257-FR03 covered step, so a suppression streak that
    reaches ``session_start_max_deferral_hours`` emits the nudge anyway. The
    ledger is consulted only while pressure is actually detected, keeping the
    per-tool-call hot path free of ledger I/O on a healthy machine.

    Audit row 7: an expired streak used to call ``record_completion``
    IMMEDIATELY here, before pool selection, content generation, phase dedup
    or messenger dispatch had a chance to run — so bounded EVALUATION did not
    guarantee bounded EMISSION; a streak could reset with no nudge ever
    reaching the caller. The second return value is a callback the caller
    invokes ONLY after confirming ``nudge_content`` actually landed on
    *response*; the claim this decision already won (via
    ``step_deferral_decision``/``claim_forced_run``) stays held until then, so
    a losing race elsewhere cannot double-run this step in the meantime.
    """
    from trw_mcp.state.deferral_ledger import record_completion, step_deferral_decision
    from trw_mcp.state.memory_pressure import take_writer_census, writer_pressure_details

    measured = census or take_writer_census(
        effective_dir,
        threshold=cfg.session_start_writer_pressure_threshold,
        pin_ttl_hours=cfg.pin_ttl_hours,
    )
    if not measured.under_pressure:
        # Peer-review finding: a "nudges" streak OPENED while pressure was
        # present is only ever closed by ``step_deferral_decision`` being
        # called again under pressure (via the ``expired`` branch below) —
        # but this function short-circuits BEFORE that call whenever pressure
        # has cleared, so a streak that started under pressure and then saw a
        # healthy call next would never close, and its stale
        # ``deferred_since_ts`` would understate age forever. The deferral
        # CAUSE (pressure) is gone here independent of whether a nudge fires
        # this call, so the streak closes unconditionally — not gated on
        # nudge emission, which is the row-7 rule for the EXPIRED-but-still-
        # under-pressure branch only.
        _close_stale_nudge_streak(effective_dir)
        return False, None
    decision = step_deferral_decision(
        effective_dir,
        "nudges",
        under_pressure=True,
        max_deferral_hours=cfg.session_start_max_deferral_hours,
    )
    if decision.expired:
        logger.warning(
            "ceremony_nudge_deferral_expired",
            age_hours=decision.age_hours,
            max_deferral_hours=cfg.session_start_max_deferral_hours,
        )

        def _finalize() -> None:
            record_completion(effective_dir, "nudges")

        return False, _finalize
    response["nudge_deferred"] = writer_pressure_details(measured, decision)
    logger.warning(
        "ceremony_nudge_deferred",
        reason="writer_pressure",
        writer_count=measured.writer_count,
        peer_writer_count=measured.peer_writer_count,
        threshold=measured.threshold,
        deferral_age_hours=decision.age_hours,
    )
    return True, None


def append_ceremony_status(
    response: dict[str, object],
    trw_dir: Path | None = None,
    context: NudgeContext | None = None,
    *,
    census: WriterCensus | None = None,
) -> dict[str, object]:
    """Attach a live ceremony progress summary and nudge content to a tool response.

    Sets ``ceremony_status`` (always) and ``nudge_content`` (when a nudge pool
    is selected and produces content).

    ``census`` lets a caller that already measured writer pressure thread its
    result in rather than taking a second census (FR01). This function runs for
    EVERY tool, not only session_start, so it falls back to measuring its own.

    Fail-open: if the state cannot be read, the original response is returned.
    """
    try:
        effective_dir = trw_dir if trw_dir is not None else resolve_trw_dir()
        cfg = _load_config_for_trw_dir(effective_dir)
        state = read_ceremony_state(effective_dir)
        response["ceremony_status"] = build_ceremony_status_line(state)

        # Disabled nudges are a status-only path. Avoid process census,
        # counter writes, and pool imports when no nudge can be emitted; this
        # keeps the advertised hot-path latency bound under loaded worktrees.
        if not cfg.effective_nudge_enabled:
            return response

        # PRD-CORE-257-FR06: pressure sets a LOCAL FLAG. The early return that
        # used to live here sat before increment_tool_call_counter and
        # attach_reversion_prompt, so under the steady-state pressure measured on
        # both reporting platforms the nudge cooldown counter never advanced and
        # the phase-reversion prompt never reached a response — two ceremony
        # mechanisms disabled by a check meant only to suppress nudge text.
        nudge_suppressed = False
        finalize_nudge_completion: Callable[[], None] | None = None
        if cfg.session_start_defer_under_writer_pressure:
            try:
                nudge_suppressed, finalize_nudge_completion = _apply_nudge_pressure(
                    response, effective_dir, cfg, census
                )
            except Exception:  # justified: pressure detection is advisory and fail-open
                logger.debug("ceremony_nudge_pressure_check_failed", exc_info=True)

        from trw_mcp.state._ceremony_progress_state import increment_tool_call_counter
        from trw_mcp.tools._ceremony_nudge_emission import attach_reversion_prompt

        # Increment tool call counter for cooldown tracking (PRD-CORE-134)
        try:
            increment_tool_call_counter(effective_dir)
            state.tool_call_counter += 1
        except Exception:  # justified: fail-open, cooldown tracking must not block ceremony status rendering
            logger.debug("ceremony_status_tool_counter_skipped", exc_info=True)

        # Ledger UF-041: phase-reversion prompting reaches a response. Placed
        # before messenger dispatch because two of the three messenger branches
        # below return early — a per-branch write would be reachable on one path.
        attach_reversion_prompt(response, context=context, state=state)

        # Only the pool selection, messenger dispatch and nudge_content emission
        # below are skipped under pressure (FR06); everything above ran.
        if nudge_suppressed:
            return response

        # Audit row 7: everything from here on is wrapped so the caller can
        # confirm nudge_content ACTUALLY landed before completing an expired
        # ``nudges`` streak. ``finalize_nudge_completion`` is only non-None when
        # this call just won a forced-run claim on that streak (see
        # ``_apply_nudge_pressure``); every code path below either sets
        # ``nudge_content`` or returns without it, so a single check in
        # ``finally`` covers every ``return response`` in this block.
        try:
            return _dispatch_nudge_content(response, state, cfg, context, effective_dir)
        finally:
            if finalize_nudge_completion is not None and "nudge_content" in response:
                with suppress(Exception):  # justified: fail-open, completion bookkeeping must not break the response
                    finalize_nudge_completion()

    except Exception:  # justified: status decoration must never break tool responses
        logger.debug("append_ceremony_status_failed", exc_info=True)
    return response


def _dispatch_nudge_content(
    response: dict[str, object],
    state: CeremonyState,
    cfg: TRWConfig,
    context: NudgeContext | None,
    effective_dir: Path,
) -> dict[str, object]:
    """Select and dispatch nudge content; may set ``response["nudge_content"]``.

    Split out of :func:`append_ceremony_status` so its many return points can
    all be covered by one ``finally`` in the caller (audit row 7).
    """
    from trw_mcp.state._ceremony_progress_state import (
        is_nudge_eligible,
        record_pool_ignore,
    )
    from trw_mcp.state.ceremony_nudge import compute_nudge_minimal
    from trw_mcp.tools._ceremony_nudge_emission import (
        account_nudge_emission,
        record_emitted_nudge,
    )

    messenger = cfg.effective_nudge_messenger
    client_id = str(getattr(cfg.client_profile, "client_id", ""))

    def _record_emitted_nudge(
        *,
        messenger_name: str,
        pool_name: str,
        learning_id: str | None,
        target_file: str | None = None,
    ) -> str:
        """Account + log one emission (ledger UF-023 attribution, UF-024 telemetry)."""
        return record_emitted_nudge(
            effective_dir,
            state=state,
            cfg=cfg,
            messenger=messenger_name,
            pool=pool_name,
            client_id=client_id,
            learning_id=learning_id,
            target_file=target_file,
            context=context,
        )

    if messenger == "minimal":
        try:
            # The minimal messenger skips pool-based dispatch entirely — it
            # produces a compressed single-line nudge from its OWN two-branch
            # ladder (session_start -> deliver -> status line only), NOT from
            # _highest_priority_pending_step; ``resolve_nudge_target_step``
            # mirrors that ladder so attribution matches the rendered text.
            # available_learnings is cosmetic here (only used when the ladder
            # lands on session_start) so pass 0 and let it render its default.
            minimal_content = compute_nudge_minimal(state, available_learnings=0)
            if minimal_content:
                response["nudge_content"] = minimal_content
                logger.debug(
                    "nudge_messenger_selected",
                    messenger="minimal",
                    content_chars=len(minimal_content),
                )
                _record_emitted_nudge(
                    messenger_name="minimal",
                    pool_name="minimal",
                    learning_id=None,
                )
        except Exception:  # justified: fail-open, never break ceremony status
            logger.debug("minimal_messenger_failed", exc_info=True)
        return response

    # PRD-CORE-241-FR07: the "learning_injection" messenger branch was
    # removed here. It is now rejected by config validation; "contextual"
    # renders the same candidate plus the NEXT action line it dropped.
    if messenger in {
        "contextual",
        "contextual_action",
        "contextual_distress",
        "silent_flow",
        "stepback",
        "anchor",
        "cod",
        "negative",
        "governance",
    }:
        try:
            contentual_content, learning_id, target_file = dispatch_contextual_messenger(
                messenger, state, effective_dir, context
            )

            if learning_id and not is_nudge_eligible(state, learning_id, state.phase):
                with suppress(Exception):  # justified: fail-open per NFR02
                    structlog.get_logger(__name__).debug(
                        "nudge_skipped",
                        reason="phase_dedup",
                        pool="contextual",
                        learning_id=learning_id,
                        client_id=str(getattr(cfg.client_profile, "client_id", "")),
                    )
                contentual_content = None

            if contentual_content:
                response["nudge_content"] = contentual_content
                _record_emitted_nudge(
                    messenger_name=messenger,
                    pool_name="context",
                    learning_id=learning_id,
                    target_file=target_file,
                )
                logger.debug(
                    "nudge_messenger_selected",
                    messenger=messenger,
                    content_chars=len(contentual_content),
                    has_learning=bool(learning_id),
                )
            else:
                minimal_content = compute_nudge_minimal(state, available_learnings=0)
                if minimal_content:
                    response["nudge_content"] = minimal_content
                    _record_emitted_nudge(
                        messenger_name=messenger,
                        pool_name="minimal",
                        learning_id=None,
                    )
        except Exception:  # justified: fail-open, never break ceremony status
            logger.debug("contextual_messenger_failed", exc_info=True)
        return response

    # 1. Select nudge pool (weighted random + cooldown + learning-cache override)
    pool = select_pool(state, cfg, context, effective_dir)
    if pool is None:
        return response

    # 2. Dispatch to pool-specific content generators
    nudge_content = resolve_pool_content(pool, state, cfg, context, effective_dir)

    # 3. Apply nudge content and update state
    if nudge_content:
        response["nudge_content"] = nudge_content
        if pool == "learnings":
            # ``_try_learning_nudge_content`` already recorded the impression,
            # the nudge_shown log, and the surface event against the REAL
            # learning id — only the two counters are still owed here.
            account_nudge_emission(effective_dir, state=state, pool=pool, context=context)
        else:
            _record_emitted_nudge(
                messenger_name="standard",
                pool_name=pool,
                learning_id=None,
            )
    else:
        # If a pool was selected but failed to produce content, record as ignore
        # so it enters cooldown and we try a different pool next time.
        record_pool_ignore(effective_dir, pool)

    return response
