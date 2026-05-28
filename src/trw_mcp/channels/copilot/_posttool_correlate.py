"""postToolUse correlation helper for Copilot channel telemetry.

Replaces the shell+jq pipeline approach (P1-22) with a dedicated Python script
that gracefully handles empty logs, missing logs, and malformed JSONL lines.

Usage (from shell script):
    python3 _posttool_correlate.py \\
        --file-path trw-mcp/src/trw_mcp/state/ceremony.py \\
        --tool-name edit \\
        [--events-log .trw/telemetry/channel-events.jsonl] \\
        [--window-seconds 3600]

Algorithm:
    1. Read events log; if absent or empty: exit 0 silently.
    2. Parse each line with json.loads; skip malformed lines without crashing.
    3. Find last push event for file_path within window_seconds.
    4. If found: append edit_after_push correlation record.
    5. Exit 0.

PRD-DIST-2406, FR15, P1-22.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

__all__ = [
    "correlate_posttool_event",
    "main",
]

_DEFAULT_EVENTS_LOG = ".trw/telemetry/channel-events.jsonl"
_DEFAULT_WINDOW_SECONDS = 3600


def _load_events(log_path: Path) -> list[dict[str, object]]:
    """Load events from a JSONL file, skipping malformed lines.

    Handles:
    - Missing file: returns []
    - Empty file: returns []
    - Malformed lines: skipped without crashing (P1-22)

    Args:
        log_path: Path to the JSONL log file.

    Returns:
        List of parsed event dicts.
    """
    if not log_path.exists():
        return []

    events: list[dict[str, object]] = []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except (json.JSONDecodeError, ValueError):
            # Skip malformed lines gracefully (P1-22)
            continue

    return events


def _find_last_push_event(
    events: list[dict[str, object]],
    file_path: str,
    *,
    window_seconds: int,
) -> dict[str, object] | None:
    """Find the most recent push event for the given file within the time window.

    Args:
        events: List of parsed event dicts.
        file_path: File path to match against event records.
        window_seconds: Maximum age of push events to consider.

    Returns:
        The most recent matching push event dict, or None if not found.
    """
    if not file_path:
        return None

    now = time.time()
    cutoff = now - window_seconds
    best: dict[str, object] | None = None

    for event in events:
        if event.get("event_type") not in ("push_write", "push_ephemeral"):
            continue

        # Match by file_path in record_ids or extra fields
        event_file: str = ""
        extra = event.get("extra", {})
        if isinstance(extra, dict):
            event_file = str(extra.get("file_path", ""))

        record_ids = event.get("record_ids", [])
        if isinstance(record_ids, list):
            for rid in record_ids:
                rid_str = str(rid)
                if file_path in rid_str:
                    event_file = file_path
                    break

        if not event_file or file_path not in event_file:
            continue

        # Check timestamp
        ts_str = str(event.get("ts", ""))
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            event_time = dt.timestamp()
        except (ValueError, AttributeError):
            continue

        if event_time < cutoff:
            continue

        # Keep the most recent
        if best is None:
            best = event
        else:
            best_ts = str(best.get("ts", ""))
            try:
                from datetime import datetime

                dt_best = datetime.fromisoformat(best_ts.replace("Z", "+00:00"))
                if event_time > dt_best.timestamp():
                    best = event
            except (ValueError, AttributeError):
                best = event

    return best


def _append_correlation_record(
    log_path: Path,
    *,
    file_path: str,
    tool_name: str,
    push_event: dict[str, object],
) -> None:
    """Append an edit_after_push correlation record to the events log.

    Args:
        log_path: Path to the JSONL log file.
        file_path: The file that was edited.
        tool_name: The tool name that triggered postToolUse.
        push_event: The matching push event.
    """
    import time as _time
    from datetime import datetime, timezone

    now_ts = datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")
    push_ts = str(push_event.get("ts", ""))
    lag_seconds: float = 0.0
    try:
        dt_push = datetime.fromisoformat(push_ts.replace("Z", "+00:00"))
        lag_seconds = round(_time.time() - dt_push.timestamp(), 1)
    except (ValueError, AttributeError):
        pass

    push_sha: str = ""
    extra_data = push_event.get("extra", {})
    if isinstance(extra_data, dict):
        push_sha = str(extra_data.get("sidecar_sha", push_event.get("sidecar_sha", "")))

    record: dict[str, object] = {
        "schema_version": "channel-event/v1",
        "channel_id": "copilot-instructions-distill",
        "client": "copilot",
        "ts": now_ts,
        "event_type": "edit_after_push",
        "file_path": file_path,
        "tool": tool_name,
        "lag_seconds": lag_seconds,
        "push_sha": push_sha,
    }

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        # Fail-open: correlation loss is acceptable (NFR06 pattern)
        pass


def correlate_posttool_event(
    *,
    file_path: str,
    tool_name: str,
    events_log: Path,
    window_seconds: int = _DEFAULT_WINDOW_SECONDS,
) -> bool:
    """Main correlation logic as a callable for testing.

    Args:
        file_path: File path to correlate.
        tool_name: Tool name from postToolUse event.
        events_log: Path to the JSONL events log.
        window_seconds: Maximum age of push events to consider.

    Returns:
        True if a correlation record was appended, False otherwise.
    """
    if not file_path:
        return False

    events = _load_events(events_log)
    if not events:
        return False

    push_event = _find_last_push_event(events, file_path, window_seconds=window_seconds)
    if push_event is None:
        return False

    _append_correlation_record(
        events_log,
        file_path=file_path,
        tool_name=tool_name,
        push_event=push_event,
    )
    return True


def main() -> int:
    """CLI entrypoint for postToolUse correlation.

    Returns:
        Exit code (always 0 — fail-open pattern, P1-22).
    """
    parser = argparse.ArgumentParser(
        description="Correlate postToolUse events with distill channel push events."
    )
    parser.add_argument(
        "--file-path",
        default="",
        help="File path that was edited (from postToolUse event).",
    )
    parser.add_argument(
        "--tool-name",
        default="",
        help="Tool name from postToolUse event.",
    )
    parser.add_argument(
        "--events-log",
        default=_DEFAULT_EVENTS_LOG,
        help=f"Path to channel-events.jsonl (default: {_DEFAULT_EVENTS_LOG}).",
    )
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=_DEFAULT_WINDOW_SECONDS,
        help=f"Correlation window in seconds (default: {_DEFAULT_WINDOW_SECONDS}).",
    )
    args = parser.parse_args()

    try:
        correlate_posttool_event(
            file_path=args.file_path,
            tool_name=args.tool_name,
            events_log=Path(args.events_log),
            window_seconds=args.window_seconds,
        )
    except Exception:
        # Always exit 0 — fail-open per P1-22
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
