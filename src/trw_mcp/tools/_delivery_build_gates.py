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


def _check_build_and_work_events(
    events: list[dict[str, object]],
) -> tuple[str | None, str | None]:
    """Check build gate and work events, return (build_warning, premature_warning).

    Uses pre-read ``events`` list (shared with other gate checks).
    """
    build_warning: str | None = None
    premature_warning: str | None = None

    try:
        if not events:
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

        # Build gate (RC-003 + RC-006)
        if not any(_build_passed(e) for e in events):
            build_warning = (
                "No successful build check found before delivery. "
                "Run project-native validation and record tests_passed/static_checks_clean with trw_build_check()."
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
