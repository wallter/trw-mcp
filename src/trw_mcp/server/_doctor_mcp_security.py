"""The ``mcp_security`` row of ``trw-mcp doctor``: what the WARN is about and where to read more.

Belongs to the ``_subcommands_doctor.py`` facade; kept in a sibling for the 350-effective-LOC gate.
A bare count ("1 recent anomaly") sent the operator nowhere, so a WARN names the newest anomaly and
the command that lists them all. Read-only; the status document is computed by the caller.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from trw_mcp.bootstrap._utils import printable

__all__ = ["MORE_COMMAND", "security_row"]

#: Prints the full status document (every recent anomaly, the quarantined servers) for the current project.
MORE_COMMAND = "trw-mcp telemetry security"

#: Server and tool names come from recorded events, so they are clipped as well as escaped.
_MAX_FIELD = 60


def _clip(value: object) -> str:
    text = printable(str(value or ""))
    return text if len(text) <= _MAX_FIELD else text[: _MAX_FIELD - 3] + "..."


def _instant(anomaly: Mapping[str, Any]) -> datetime:
    """The anomaly's time as an instant, so rows written with different UTC offsets still order correctly."""
    try:
        parsed = datetime.fromisoformat(str(anomaly.get("ts", "")))
    except ValueError:  # trw-fail-silent-allow: an unparseable time sorts oldest; the row is still reported
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _newest(anomalies: list[Mapping[str, Any]]) -> str:
    """``kind from server S, tool T, at <time>`` for the most recent anomaly; absent fields are left out."""
    newest = max(anomalies, key=_instant)
    parts = [_clip(newest.get("type")) or "unrecorded kind"]
    if server := _clip(newest.get("server")):
        parts.append(f"from server {server}")
    if tool := _clip(newest.get("tool")):
        parts.append(f"tool {tool}")
    if ts := _clip(newest.get("ts")):
        parts.append(f"at {ts}")
    return ", ".join(parts)


def _details_command(target: Path | None) -> str:
    """:data:`MORE_COMMAND`, preceded by a ``cd`` when the doctor was pointed at another project.

    ``telemetry security`` reads the project it runs in, so the bare command would show a different
    project's status to an operator who ran ``trw-mcp doctor <path>`` from elsewhere.
    """
    if target is None or target.resolve() == Path.cwd().resolve():
        return MORE_COMMAND
    return f"cd {shlex.quote(str(target))} && {MORE_COMMAND}"


def security_row(status: Mapping[str, Any], target: Path | None = None) -> tuple[Literal["PASS", "WARN"], str]:
    """WARN naming the newest anomaly and the details command when one is recorded or a server is quarantined."""
    anomalies = list(status.get("recent_anomalies") or [])
    quarantined = [str(name) for name in status.get("quarantined_servers") or []]
    if not anomalies and not quarantined:
        return "PASS", "no recent anomalies, no quarantined servers"
    count = len(anomalies)
    message = f"{count} recent anomal{'y' if count == 1 else 'ies'}"
    if anomalies:
        message += f" (newest: {_newest(anomalies)})"
    message += f", {len(quarantined)} quarantined server{'' if len(quarantined) == 1 else 's'}"
    if quarantined:
        message += f" ({', '.join(_clip(name) for name in quarantined)})"
    return "WARN", f"{message}; details: {_details_command(target)}"
