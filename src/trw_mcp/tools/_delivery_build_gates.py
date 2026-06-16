"""Build-evidence delivery gates for :mod:`trw_mcp.tools._delivery_helpers`."""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from trw_mcp.state.persistence import FileStateReader

logger = structlog.get_logger(__name__)


def _truthy(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _build_event_payload(ev: dict[str, object]) -> dict[str, object]:
    """Return the build-check payload for nested or flat event records."""
    data = ev.get("data")
    return data if isinstance(data, dict) else ev


def _build_passed(ev: dict[str, object]) -> bool:
    if str(ev.get("event", "")) != "build_check_complete":
        return False
    data = _build_event_payload(ev)
    if not _truthy(data.get("tests_passed")):
        return False
    if "static_checks_clean" in data:
        return _truthy(data.get("static_checks_clean"))
    if "mypy_clean" in data:
        return _truthy(data.get("mypy_clean"))
    return True


def _event_ts(ev: dict[str, object]) -> str:
    """Return the event's ISO timestamp string, or '' when absent."""
    return str(ev.get("ts", ""))


def _latest_ts_for(events: list[dict[str, object]], predicate: object) -> str:
    """Max ISO ``ts`` among events matching ``predicate`` (callable); '' if none.

    ISO-8601 UTC timestamps from ``FileEventLogger.log_event`` are
    lexicographically orderable, so ``max`` over the string form is a correct
    chronological comparison without parsing.
    """
    from collections.abc import Callable
    from typing import cast

    pred = cast("Callable[[dict[str, object]], bool]", predicate)
    stamps = [_event_ts(ev) for ev in events if pred(ev) and _event_ts(ev)]
    return max(stamps) if stamps else ""


def _build_evidence_is_stale(events: list[dict[str, object]]) -> bool:
    """True when a ``file_modified`` postdates the latest PASSING build check.

    FRAMEWORK.md §"Build evidence MUST postdate the last change it claims to
    cover: edit after the check -> re-run the check. Stale evidence is no
    evidence." (codex cross-model review). The deliver build gate previously
    accepted ANY passing ``build_check_complete`` in run history regardless of
    whether a file was edited AFTER it, so a pass-then-edit sequence slipped the
    gate with stale evidence.

    Compares the max ``ts`` of passing build events against the max ``ts`` of
    ``file_modified`` events. Returns True only when BOTH exist and the latest
    edit strictly postdates the latest passing build. No passing build (handled
    elsewhere) or no edits -> not stale here. Equal timestamps are NOT stale
    (the edit did not happen strictly after the build).
    """
    latest_build_ts = _latest_ts_for(events, _build_passed)
    if not latest_build_ts:
        return False
    latest_edit_ts = _latest_ts_for(events, lambda ev: str(ev.get("event", "")) == "file_modified")
    if not latest_edit_ts:
        return False
    return latest_edit_ts > latest_build_ts


def _check_build_and_work_events(
    events: list[dict[str, object]],
) -> tuple[str | None, str | None]:
    """Check build gate and work events, return (build_warning, premature_warning).

    Uses pre-read ``events`` list (shared with other gate checks).
    """
    build_warning: str | None = None
    premature_warning: str | None = None

    # When build-check is intentionally disabled (``config.build_check_enabled``
    # is False), ``trw_build_check`` returns early without ever logging a
    # ``build_check_complete`` event. Without this guard the delivery gate would
    # then fire ``build_gate_warning`` for the missing event — both SKIPPING the
    # build check AND blocking delivery on its absence. Mirror the
    # ``phase_gates_build.py`` convention: a disabled build-check means no build
    # gate. The premature-delivery (work-events) guard below still applies.
    #
    # codex cross-model review (REFUTE/DOCUMENT): ``build_check_enabled=False`` is
    # the FRAMEWORK's SANCTIONED config-level gate override, not a loophole —
    # FRAMEWORK.md §"Quality gates" lets a project opt out of the build gate
    # explicitly via config. Skipping it here honors that sanctioned opt-out.
    try:
        from trw_mcp.models.config import get_config

        build_check_disabled = not get_config().build_check_enabled
    except Exception:  # justified: fail-open, never let config read block delivery
        logger.warning("build_gate_config_read_failed", exc_info=True)
        build_check_disabled = False

    try:
        if build_check_disabled:
            # Skip the build gate entirely; still evaluate the work-events guard.
            pass
        elif not events:
            # A-P1-07: empty/truncated events.jsonl = NO build evidence. Treat it
            # like "events present but no passing build" (symmetry) so the delivery
            # gate requires evidence — the allow_unverified override still applies,
            # so this is not a hard lockout. Pre-fix this returned (None, None), so a
            # pinned run with an empty events.jsonl slipped the build gate silently.
            return (
                "No events found before delivery — cannot verify a build check was passed. "
                "Run project-native validation and record tests_passed/static_checks_clean with trw_build_check().",
                None,
            )

        # Build gate (RC-003 + RC-006) — skipped when build-check is disabled.
        if not build_check_disabled and not any(_build_passed(e) for e in events):
            build_warning = (
                "No successful build check found before delivery. "
                "Run project-native validation and record tests_passed/static_checks_clean with trw_build_check()."
            )
        # Stale-evidence gate (codex cross-model review; FRAMEWORK.md §"Build
        # evidence MUST postdate the last change it claims to cover"). A passing
        # build exists, but a file was edited AFTER it -> the evidence no longer
        # covers the current tree. Treated identically to a missing build:
        # build_warning is set, so the caller's _apply_deliver_gate_mode promotes
        # it to a hard delivery_blocked under block_* modes and leaves it a
        # warning under advisory mode. The allow_unverified override still applies.
        elif not build_check_disabled and _build_evidence_is_stale(events):
            build_warning = (
                "Stale build evidence: a file was modified AFTER the last passing trw_build_check. "
                "The recorded build no longer covers the current changes — re-run project-native "
                "validation and record it with trw_build_check() before delivering."
            )

        # Premature delivery guard.
        # NOTE: "session_start" is the ceremony bootstrap event actually emitted
        # by step_log_session_event (EventType.SESSION_START). It MUST be excluded
        # here — every run logs it, so without this entry work_events is always
        # non-empty and the premature-delivery guard can never fire. (The legacy
        # "trw_session_start_complete" name below is never emitted; retained as a
        # harmless alias rather than removed.)
        _CEREMONY_ONLY_EVENTS: frozenset[str] = frozenset(
            {
                "run_init",
                "session_start",
                "checkpoint",
                "reflection_complete",
                "trw_reflect_complete",
                "trw_deliver_complete",
                "trw_session_start_complete",
            }
        )
        work_events = [e for e in events if str(e.get("event", "")) not in _CEREMONY_ONLY_EVENTS]
        if not work_events:
            premature_warning = (
                "Premature delivery — no work events found beyond ceremony. "
                "This run has only init/checkpoint events. Proceeding anyway, "
                "but consider whether work was actually completed."
            )
            logger.warning(
                "premature_delivery",
                total_events=len(events),
                work_events=0,
            )
    except Exception:  # justified: fail-open, build gate check must not block delivery
        logger.warning("maintenance_build_gate_failed", exc_info=True)

    return build_warning, premature_warning


def _check_no_active_run_build_gate(trw_dir: Path | None, reader: FileStateReader) -> str | None:
    """Require build-check evidence for deliver when no run pin exists.

    Eval containers commonly run without ``trw_init``/``trw_adopt_run``. In
    that state there is no run ``events.jsonl`` for the normal delivery gate,
    but the local ceremony state still records session_start/build_check
    progress. Without this fallback, ``trw_deliver`` can silently mark
    ``deliver_called=True`` after a failed task.
    """
    if trw_dir is None:
        return None

    state_path = trw_dir / "context" / "ceremony-state.json"
    try:
        # trw:intentional fail-open for unpinned sessions without ceremony state.
        # No ceremony-state.json (or no ``session_started``) means no TRW session
        # ever began in this project (e.g. a brand-new project or a quick-task
        # flow that never ran trw_session_start). There is no session-local
        # evidence to gate against, and the gate's job is specifically to catch a
        # STARTED session that recorded no passing build — NOT to force ceremony
        # onto delivery paths that never opted in. Returning a warning here would
        # over-block legitimate new-project/quick-task delivery. The gate only
        # fires below when session_started=True and the recorded build did NOT
        # pass; the allow_unverified override still applies on top of that.
        #
        # PATH-3 layered defense (codex cross-model review): "no run + no ceremony
        # state -> no build gate fires" was flagged as a bypass. It is by-design,
        # and defended in DEPTH, not left open:
        #   (1) Upstream, CeremonyMiddleware.on_call_tool BLOCKS every trw_* tool
        #       (including trw_deliver) with a ``session_start_required`` error
        #       whenever a post-compaction recovery marker is pending — so a
        #       deliver after a dropped/compacted session cannot reach here.
        #   (2) The deliver_gate_mode task-type taxonomy classifies a delivery
        #       with no run.yaml as task_type=unknown, which
        #       _BUILD_ARTIFACT_TASK_TYPES intentionally EXCLUDES (unknown never
        #       hard-blocks). A no-run/no-ceremony delivery is therefore an
        #       unknown-typed delivery the framework deliberately treats as
        #       advisory — blocking it would over-block legitimate quick-tasks.
        # The fail-closed posture is reserved for STARTED sessions (below), where
        # there IS evidence that a build was expected but did not pass.
        if not reader.exists(state_path):
            return None
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not state.get("session_started"):
            return None

        build_result = state.get("build_check_result")
        build_passed = build_result is True or (
            isinstance(build_result, str) and build_result.lower() in {"pass", "passed", "success", "true"}
        )
        if build_passed:
            return None
        return (
            "No successful build check found before delivery in this unpinned session. "
            "Run project-native validation and record tests_passed/static_checks_clean with trw_build_check(), "
            "or call trw_init()/trw_adopt_run() so run-scoped evidence can be checked."
        )
    except Exception:  # justified: fail-open, build gate check must not block delivery on read errors
        logger.warning("no_active_run_build_gate_failed", exc_info=True)
        return None
