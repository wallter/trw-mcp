"""Event readers and complexity-drift checks for delivery gates."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp import PROCESS_STARTED_AT as _PROCESS_STARTED_AT
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import (
    COMPLEXITY_DRIFT_MULTIPLIER,
    REVIEW_SCOPE_FILE_THRESHOLD,
)

logger = structlog.get_logger(__name__)

#: When this server process started (stamped in ``trw_mcp/__init__``, the first
#: import of every process — this module loads lazily at the first deliver call,
#: which is after the edits it must see). An unpinned session whose pin key is
#: the process UUID has no identifier a shell hook can observe, so its change
#: evidence is read as every unpinned record written since this moment.
PROCESS_STARTED_AT = _PROCESS_STARTED_AT


def _read_run_events(run_path: Path, reader: FileStateReader) -> list[dict[str, object]] | None:
    """Read events.jsonl for a run; ``None`` when the log could not be READ.

    Centralised helper — called once by ``check_delivery_gates`` and passed
    to individual gate functions so events.jsonl is read at most once.

    Three outcomes, and the distinction is load-bearing (WD-03):

    - ``[]``   — the log is absent or genuinely empty. An honest zero.
    - ``list`` — the parsed records.
    - ``None`` — the log exists but could not be read or parsed.

    Before this returned ``[]`` for the third case too, so an unreadable
    events.jsonl (I/O error, a path that is not a regular file, a permissions
    fault) was indistinguishable from "this session changed nothing". Every
    downstream gate that COUNTS changed files — ``count_session_changed_files``
    and ``_check_review_file_count_gate`` — then measured 0 and allowed the
    delivery, without ever reaching its own fail-closed branch. ``None`` is the
    uncomputable signal those callers translate into a block.

    Per-line JSON damage is NOT this case: ``FileStateReader.read_jsonl`` is
    deliberately lenient about a torn tail line and still returns the valid
    records, which remains the right behaviour for an append-only log.
    """
    events_path = run_path / "meta" / "events.jsonl"
    try:
        if reader.exists(events_path):
            return reader.read_jsonl(events_path)
    except Exception:  # justified: fail-CLOSED, an unreadable log is uncomputable, not empty
        logger.warning(
            "run_events_read_failed",
            run_path=str(run_path),
            outcome="uncomputable",
            exc_info=True,
        )
        return None
    return []


def _normalize_event_path(raw: str, repo_root: Path | None = None) -> str:
    """Normalize a ``file_modified`` event path to a repo-relative dedup key.

    Collapses redundant separators / ``.`` / ``..`` via ``os.path.normpath``.
    When an absolute path falls under ``repo_root`` it is reduced to its
    repo-relative form, so the same file recorded once as an absolute path and
    once as a repo-relative path dedupes to a single count.
    """
    norm = os.path.normpath(raw)
    if repo_root is not None and os.path.isabs(norm):
        try:
            rel = os.path.relpath(norm, str(repo_root))
        except ValueError:  # different drive (Windows) — leave absolute
            return norm
        if not rel.startswith(".."):
            return rel
    return norm


def _count_file_modified(
    events: list[dict[str, object]],
    repo_root: Path | None = None,
) -> int:
    """Count DISTINCT normalized file paths across ``file_modified`` events.

    PRD-QUAL-101-FR02a: the review-scope gate measures *delivery size*, so N
    edits to ONE file must count once. The pre-FR02a implementation summed
    event occurrences, inflating the count (N edits → N). Paths are read from
    the top-level ``file`` field that ``post-tool-event.sh`` writes (see
    ``append_event`` in ``lib-trw.sh``; ``FileEventLogger.log_event`` likewise
    flattens a ``file`` key to the top level), normalized to a repo-relative
    key, then deduped.

    Events with NO ``file`` field (non-hook-sourced events, hook gaps, or
    Bash-driven changes that emit no path) are NOT dropped: each is counted as
    its own unit. Absence of a path can therefore only keep the count class the
    same or make it larger — never silently shrink it, which would weaken the
    gate.
    """
    distinct: set[str] = set()
    pathless = 0
    for ev in events:
        if str(ev.get("event", "")) != "file_modified":
            continue
        raw = str(ev.get("file", "")).strip()
        if not raw:
            pathless += 1
            continue
        distinct.add(_normalize_event_path(raw, repo_root))
    return len(distinct) + pathless


def _events_since_last_session_start(
    events: list[dict[str, object]],
    session_id: str | None = None,
) -> list[dict[str, object]]:
    """Return the caller's events after its current logical-session boundary.

    Scoped callers use the first matching ``session_start`` after their last
    matching ``trw_deliver_complete``. Repeated starts with the same stable ID
    are compaction/resume boundaries and must not hide pre-compaction edits.
    Foreign scoped events are filtered; unscoped legacy events remain visible
    conservatively to avoid review-scope and complexity-drift false negatives.
    Unscoped callers retain the legacy last-start behavior.
    """
    last_session_idx = -1
    if session_id is None:
        for i, ev in enumerate(events):
            if str(ev.get("event", "")) == "session_start":
                last_session_idx = i
    else:
        last_delivery_idx = max(
            (
                i
                for i, ev in enumerate(events)
                if str(ev.get("event", "")) == "trw_deliver_complete" and str(ev.get("session_id", "")) == session_id
            ),
            default=-1,
        )
        last_session_idx = next(
            (
                i
                for i, ev in enumerate(events[last_delivery_idx + 1 :], start=last_delivery_idx + 1)
                if str(ev.get("event", "")) == "session_start" and str(ev.get("session_id", "")) == session_id
            ),
            -1,
        )
        has_target_event = any(str(ev.get("session_id", "")) == session_id for ev in events)
        if last_session_idx < 0 and not has_target_event:
            for i, ev in enumerate(events):
                if str(ev.get("event", "")) == "session_start" and not ev.get("session_id"):
                    last_session_idx = i
    window = events if last_session_idx < 0 else events[last_session_idx + 1 :]
    if session_id is None:
        return window
    return [ev for ev in window if not ev.get("session_id") or str(ev.get("session_id")) == session_id]


def _count_file_modified_current_session(
    events: list[dict[str, object]],
    repo_root: Path | None = None,
    session_id: str | None = None,
) -> int:
    """Count DISTINCT modified file paths in the current session only.

    Uses ``session_start`` as the session boundary marker. Events from
    previous sessions (before the last ``session_start``) are excluded. Within
    the window, distinct-path semantics (FR02a) apply — see
    ``_count_file_modified``.
    """
    session_events = _events_since_last_session_start(events, session_id)
    return _count_file_modified(session_events, repo_root)


def _project_root_from_run(run_path: Path) -> Path | None:
    """Walk up from a run dir to the project root (the parent of ``.trw/``).

    Returns None when no ``.trw`` marker is found, in which case path
    normalization falls back to ``os.path.normpath`` only (still correct for
    dedup — the hook records one consistent path form per file per session).
    """
    for parent in run_path.parents:
        if (parent / ".trw").is_dir():
            return parent
    return None


def _read_run_yaml(run_path: Path, reader: FileStateReader) -> dict[str, object]:
    """Read run.yaml, returning empty dict on any error."""
    run_yaml_path = run_path / "meta" / "run.yaml"
    try:
        if run_yaml_path.exists():
            return reader.read_yaml(run_yaml_path)
    except Exception:  # justified: fail-open, run.yaml read must not block delivery
        logger.warning("run_yaml_read_failed", run_path=str(run_path), exc_info=True)
    return {}


def run_yaml_is_readable(run_path: Path, reader: FileStateReader) -> bool:
    """Could ``meta/run.yaml`` be read at all?

    Deliberately SEPARATE from :func:`_read_complexity_class` rather than folded
    into one ``(class, readable)`` call. Tests across this package monkeypatch
    ``_delivery_helpers._read_complexity_class`` to drive the gate, and a combined
    function would silently bypass every one of those patches — the callers would
    keep type-checking and quietly stop honouring the fixture. The facade
    indirection is load-bearing, not style.
    """
    run_yaml_path = run_path / "meta" / "run.yaml"
    if not run_yaml_path.exists():
        return False
    try:
        reader.read_yaml(run_yaml_path)
    except Exception:  # trw-fail-silent-allow: False IS the answer this predicate exists to give — "run.yaml could not be read" — and the caller escalates on it rather than proceeding; the failure is logged at warning
        logger.warning("run_yaml_read_failed", run_path=str(run_path), exc_info=True)
        return False
    return True


def _read_complexity_class_state(run_path: Path, reader: FileStateReader) -> tuple[str, bool]:
    """``(complexity_class, run_yaml_was_readable)``.

    The second element exists because ``""`` was answering two different
    questions. ``_read_run_yaml`` fails open to ``{}``, so an absent or malformed
    run.yaml produced the same empty class as a run that simply carries no
    ``complexity_class`` key — and every caller tests membership in
    ``("STANDARD", "COMPREHENSIVE")``, which ``""`` can never satisfy. The
    unreadable case therefore took the LENIENT branch at every decision point.

    That is a sampling bias, not just a fall-through: a run whose own metadata
    cannot be read is not a random run, it is a broken one, and broken runs are
    the population a delivery gate exists to catch. The fail-open comment says
    the read "must not block delivery"; the effect was to UNBLOCK a delivery that
    a review had explicitly blocked.

    Reported by a cross-family sweep 2026-09-12 with a measured downgrade table.
    """
    run_yaml_path = run_path / "meta" / "run.yaml"
    if not run_yaml_path.exists():
        return "", False
    try:
        run_data = reader.read_yaml(run_yaml_path)
    except Exception:  # justified: fail-open — the READABILITY is returned, not swallowed
        logger.warning("run_yaml_read_failed", run_path=str(run_path), exc_info=True)
        return "", False
    return str(run_data.get("complexity_class", "")), True


def _read_complexity_class(run_path: Path, reader: FileStateReader) -> str:
    """Read the complexity_class from run.yaml, or return empty string.

    Kept as the patchable single source of the CLASS. Readability is a separate
    question, answered by :func:`run_yaml_is_readable`.
    """
    return _read_complexity_class_state(run_path, reader)[0]


def _check_complexity_drift(
    run_data: dict[str, object],
    events: list[dict[str, object]],
    session_id: str | None = None,
) -> str | None:
    """Detect when actual work scope significantly exceeds the initial classification.

    Uses pre-read ``run_data`` and ``events`` (shared with other gate checks)
    so events.jsonl is read only once per delivery.

    Fires a WARNING (not a block) when:
      - ``complexity_class`` is ``MINIMAL``
      - actual file_modified count > REVIEW_SCOPE_FILE_THRESHOLD
      - actual count > COMPLEXITY_DRIFT_MULTIPLIER * planned files

    Returns:
        A warning string if complexity drift is detected, or None.
    """
    try:
        complexity_class = str(run_data.get("complexity_class", ""))
        if complexity_class != "MINIMAL":
            return None

        signals = run_data.get("complexity_signals")
        if not isinstance(signals, dict):
            return None
        planned_files = int(str(signals.get("files_affected", 0)))

        actual_files = _count_file_modified_current_session(events, session_id=session_id)

        if actual_files > REVIEW_SCOPE_FILE_THRESHOLD and actual_files > COMPLEXITY_DRIFT_MULTIPLIER * planned_files:
            logger.info(
                "complexity_drift_detected",
                complexity_class=complexity_class,
                planned_files=planned_files,
                actual_files=actual_files,
            )
            return (
                f"Complexity drift detected: classified MINIMAL "
                f"({planned_files} files planned) but {actual_files} files "
                f"were modified. Consider re-evaluating — tasks of this scope "
                f"typically require STANDARD complexity with mandatory REVIEW phase."
            )

    except Exception:  # justified: fail-open, complexity drift check must not block delivery
        logger.warning("complexity_drift_check_failed", exc_info=True)

    return None


def latest_build_check_failed(events: list[dict[str, object]] | None) -> bool | None:
    """Did the MOST RECENT recorded build check report failure? — PRD-FIX-140-FR05.

    ``True``  — the last ``build_check_complete`` event reported
                ``tests_passed`` falsy (an explicit failure, or a timeout
                recorded as one).
    ``False`` — the last one reported a pass, or the run recorded none at all.
    ``None``  — the event log could not be read (``events is None``); the caller
                treats that as a block, matching every other fail-closed input on
                the delivery path.

    The existing build gate asks ``any(_build_passed(e) for e in events)``, so a
    run that passed, then edited, then FAILED still satisfies it on the strength
    of the earlier pass unless the staleness heuristic happens to fire. The
    bundled PreToolUse hook covered that case by reading only the CURRENT
    ``build-status.yaml`` receipt; with the hook demoted to a diagnostic the
    latest-verdict rule has to live here.

    Deliberately narrow: only a self-reported failure counts. The degenerate-pass
    rejections (``test_count=0``, empty scope) stay with the build gate that owns
    them, so this predicate cannot double-report an existing warning as a new
    block.
    """
    if events is None:
        logger.warning("latest_build_check_uncomputable", outcome="fail_closed", reason="events_unreadable")
        return None
    from trw_mcp.tools._delivery_build_gates import _build_event_payload, _truthy

    for event in reversed(events):
        if str(event.get("event", "")) != "build_check_complete":
            continue
        return not _truthy(_build_event_payload(event).get("tests_passed"))
    return False


def latest_build_check_failed_for_run(run_path: Path) -> bool | None:
    """:func:`latest_build_check_failed` over a run directory's own event log.

    One bounded re-read of ``meta/events.jsonl`` on the delivery path: the
    dispatcher runs after ``check_delivery_gates`` has returned and no longer
    holds the materialised list, and threading it through would change a public
    signature owned by another module.
    """
    return latest_build_check_failed(_read_run_events(run_path, FileStateReader()))


def _read_unpinned_ceremony_state(trw_dir: Path | None) -> dict[str, object] | None | bool:
    """``dict`` when readable, ``False`` when absent/not-started, ``None`` when unreadable.

    Read here rather than through ``state._ceremony_progress_state.read_ceremony_state``
    because that reader deliberately fails OPEN to defaults on a corrupt file, which
    would report "nothing recorded" for a file nobody could read — the opposite of
    what a delivery gate needs.
    """
    if trw_dir is None:
        return False
    state_path = trw_dir / "context" / "ceremony-state.json"
    if not state_path.is_file():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:  # justified: fail-CLOSED, an unreadable state is not proof of a passing build
        logger.warning("unpinned_build_state_unreadable", outcome="fail_closed", path=str(state_path), exc_info=True)
        return None
    if not isinstance(state, dict) or not state.get("session_started"):
        return False
    return state


def _recorded_session_build(state: dict[str, object], session_id: str) -> object:
    """This session's recorded build result, or ``None``.

    Keyed on the CURRENT session only. ``build_check_result`` at the top level is
    project-global — it is exactly the cross-session state that made the bundled
    hook accept (or reject) evidence produced by a different agent in the same
    checkout, so it is not consulted.
    """
    per_session = state.get("session_build_results")
    return per_session.get(session_id) if isinstance(per_session, dict) else None


def unpinned_build_failure_recorded(trw_dir: Path | None, session_id: str) -> bool | None:
    """Did THIS session record a FAILED build check? — PRD-FIX-140-FR04.

    ``True``  — ``ceremony-state.json`` records a failure for ``session_id``.
    ``False`` — no ceremony state, ceremony never started, nothing recorded for
                this session, or the recorded result is pending/passing.
    ``None``  — the state file exists but could not be read or parsed, so the
                answer is uncomputable and the caller blocks (fail-closed).
    """
    state = _read_unpinned_ceremony_state(trw_dir)
    if not isinstance(state, dict):
        return None if state is None else False
    recorded = _recorded_session_build(state, session_id)
    if recorded is False:
        return True
    return isinstance(recorded, str) and recorded.strip().lower() in {"failed", "fail", "false"}


def unpinned_build_passed(trw_dir: Path | None, session_id: str, *, unscoped_since: datetime | None = None) -> bool:
    """Did THIS session record a PASSING build check that is still current? — PRD-FIX-140-FR04.

    Only a positive, session-attributed pass returns ``True``; everything else
    (absent, pending, failed, unreadable) is ``False``, so this can only ever
    RELEASE the gate on evidence, never on the absence of it.

    A pass is evidence for the tree as it stood when it was recorded. A
    ``file_modified`` record stamped after it (this session's, or any unpinned
    session's when ``unscoped_since`` says the key is unshared) makes it stale,
    and a pass with no recorded time cannot be shown current (release-verify
    2026-09-17 P1-1).
    """
    state = _read_unpinned_ceremony_state(trw_dir)
    if not isinstance(state, dict):
        return False
    recorded = _recorded_session_build(state, session_id)
    passed = recorded is True or (
        isinstance(recorded, str) and recorded.strip().lower() in {"pass", "passed", "success", "true"}
    )
    if not passed:
        return False
    stamps = state.get("session_build_results_at")
    passed_at = stamps.get(session_id) if isinstance(stamps, dict) else None
    if not isinstance(passed_at, str) or not passed_at:
        return False
    try:
        passed_dt = datetime.fromisoformat(passed_at.replace("Z", "+00:00"))
    except ValueError:
        # trw-fail-silent-allow: False is the CLOSED outcome — a pass whose stamp cannot be parsed cannot be shown current
        return False
    if passed_dt.tzinfo is None:
        passed_dt = passed_dt.replace(tzinfo=timezone.utc)
    return not _file_modified_since(trw_dir, session_id, passed_dt, unscoped_since=unscoped_since)


def _file_modified_since(
    trw_dir: Path | None, session_id: str, since: datetime, *, unscoped_since: datetime | None
) -> bool:
    """True when the session-scoped stream holds a ``file_modified`` record stamped at or after *since*.

    Fails closed: an unreadable stream counts as modified.
    """
    if trw_dir is None:
        return False
    events_path = trw_dir / "context" / "session-events.jsonl"
    if not events_path.exists():
        return False
    try:
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # trw-fail-silent-allow: torn tail line in an append-only log, same rule as read_jsonl
                continue
            if not isinstance(record, dict) or str(record.get("event", "")) != "file_modified":
                continue
            if unscoped_since is None and str(record.get("session_id", "")) != session_id:
                continue
            if _stamped_at_or_after(record.get("ts"), since):
                return True
        return False
    except (OSError, UnicodeDecodeError):  # justified: fail-CLOSED, an unreadable session log counts as modified
        logger.warning("unpinned_session_changes_unreadable", outcome="fail_closed", path=str(events_path))
        # trw-fail-silent-allow: True is the BLOCKING outcome here — an unreadable log is read as "modified"
        return True


def _stamped_at_or_after(raw_ts: object, since: datetime) -> bool:
    """True when an event's ``ts`` (``%Y-%m-%dT%H:%M:%SZ``, second resolution) is not before *since*.

    A missing or unparseable stamp (the writer emits the literal ``unknown`` when
    ``date`` fails) counts as inside the window: this reader exists to fail
    closed, and an edit whose time is unknown cannot be shown to predate the server.
    """
    if not isinstance(raw_ts, str) or not raw_ts:
        return True
    try:
        stamped = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return stamped >= since.replace(microsecond=0)


def unpinned_session_changed_files(
    trw_dir: Path | None, session_id: str, *, unscoped_since: datetime | None = None
) -> int | None:
    """Distinct files THIS unpinned session recorded modifying — PRD-FIX-140-FR04.

    The pinned path counts ``file_modified`` events in the run's own
    ``events.jsonl`` (:func:`_count_file_modified_current_session`). An unpinned
    session has no run directory, so the same evidence is read from the two
    session-scoped surfaces that survive without a pin:

    * ``.trw/context/session-events.jsonl`` — the stream the unpinned deliver
      marker already uses (``_delivery_build_gates.write_session_deliver_marker``);
      ``file_modified`` records carrying this ``session_id`` are counted by
      distinct normalised path, exactly like the pinned counter.
    * ``ceremony-state.json``'s ``files_modified_since_checkpoint`` — a count, not
      paths, so it is combined with ``max`` rather than added (the same file could
      appear in both).

    ``0`` is an HONEST zero: nothing was recorded, which is what a docs-only or
    research session looks like, and the caller leaves that advisory. ``None``
    means a surface exists but could not be read, and the caller blocks.

    The writer is ``data/hooks/post-tool-event.sh`` (``_append_unpinned_change``),
    keyed on ``TRW_SESSION_ID`` when the client exports one and otherwise on the
    host's own session id. Only ``claude-code`` publishes an identifier that both
    the hook and this server can observe (``client_profiles/session_identity.py``);
    for every other profile the server's key is its process UUID and can never
    equal the hook's. Passing ``unscoped_since`` is the caller's declaration that
    its key is unshared: every ``file_modified`` record stamped at or after that
    instant is then counted regardless of ``session_id``. That over-counts when two
    unpinned sessions edit the same checkout at once, which fails closed (a block
    that names the reason) rather than the silent zero a key mismatch produced.
    """
    state = _read_unpinned_ceremony_state(trw_dir)
    if state is None:
        return None
    counted = 0
    if isinstance(state, dict):
        raw = state.get("files_modified_since_checkpoint")
        if isinstance(raw, int) and raw > 0:
            counted = raw
    if trw_dir is None:
        return counted
    events_path = trw_dir / "context" / "session-events.jsonl"
    if not events_path.exists():
        return counted
    if not events_path.is_file():
        # Present but not a regular file: uncomputable, NOT an honest zero. Same
        # distinction ``_read_run_events`` draws for the pinned path (WD-03).
        logger.warning("unpinned_session_changes_unreadable", outcome="fail_closed", path=str(events_path))
        return None
    try:
        paths: set[str] = set()
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # A damaged LINE is not an unreadable FILE: the enclosing handler still fails closed
                # for the latter, and FileStateReader.read_jsonl skips a torn tail line the same way
                # on the pinned path.
                # trw-fail-silent-allow: torn tail line in an append-only log, same rule as read_jsonl
                continue
            if not isinstance(record, dict) or str(record.get("event", "")) != "file_modified":
                continue
            # One writer, one shape: post-tool-event.sh appends flat
            # {"event","session_id","file",...} lines (PRD-FIX-140-FR04).
            if unscoped_since is None:
                if str(record.get("session_id", "")) != session_id:
                    continue
            elif not _stamped_at_or_after(record.get("ts"), unscoped_since):
                continue
            raw_path = str(record.get("file", ""))
            if raw_path:
                paths.add(_normalize_event_path(raw_path, trw_dir.parent))
        return max(counted, len(paths))
    except Exception:  # justified: fail-CLOSED, an unreadable session log is uncomputable, not zero
        logger.warning("unpinned_session_changes_unreadable", outcome="fail_closed", path=str(events_path))
        return None
