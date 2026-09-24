"""Per-member usage ledger for ``formation status --json`` (PRD-CORE-290-FR01).

Belongs to the ``trw_mcp.formation`` package. Read-only aggregation over events
TRW already writes to each run's ``meta/events-YYYY-MM-DD.jsonl``; there is no
ledger store of its own. Two categories, never added together:

- ``mcp_response_bytes``: serialized MCP responses, measured by the tool-call
  wrapper (``source: measured``, ``unit: bytes``);
- ``child_tokens``: dispatched children's own token reports (``source:
  self-reported``, ``unit: tokens``), one per ``child_id``.

A replayed event (same ``event_id``) counts once; events from a reconnected
session all count. A category with nothing observed is ABSENT, never zero.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

__all__ = ["formation_usage", "member_usage"]

_KINDS = ('"tool_call"', '"dispatch_usage"')
_TOKEN_FIELDS = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_input_tokens"}


def _events(run_path: Path) -> Iterator[dict[str, Any]]:
    """Each distinct tool_call / dispatch_usage event of *run_path*, oldest file first."""
    seen: set[str] = set()
    for events_file in sorted((run_path / "meta").glob("events-*.jsonl")):
        try:
            lines = events_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:  # trw-fail-silent-allow: an unreadable file is unobserved usage, reported as absent
            continue
        for line in lines:
            if not any(kind in line for kind in _KINDS):
                continue
            try:
                record = json.loads(line)
            except ValueError:  # trw-fail-silent-allow: a torn line is skipped, never counted
                continue
            if not isinstance(record, dict):
                continue
            event_id = str(record.get("event_id", ""))
            if event_id and event_id not in seen:
                seen.add(event_id)
                yield record


def _tally(records: Iterable[dict[str, Any]], children: dict[str, dict[str, Any]]) -> dict[str, Any]:
    calls = measured = total = 0
    first = last = ""
    for record in records:
        raw = record.get("payload")
        payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if record.get("event_type") == "dispatch_usage":
            child_id = str(payload.get("child_id", ""))
            if child_id and child_id not in children:
                children[child_id] = {**payload, "ts": str(record.get("ts", ""))}
            continue
        calls += 1
        size = payload.get("response_bytes")
        if isinstance(size, int) and not isinstance(size, bool):
            measured += 1
            total += size
            ts = str(record.get("ts", ""))
            first, last = (min(first, ts) if first else ts), max(last, ts)
    if not measured:
        return {}
    return {
        "mcp_response_bytes": {
            "value": total,
            "unit": "bytes",
            "source": "measured",
            "events": measured,
            "coverage": f"{measured}/{calls} tool calls measured",
            "first": first,
            "last": last,
        }
    }


def _child_block(children: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not children:
        return {}
    block: dict[str, Any] = {}
    for label, key in _TOKEN_FIELDS.items():
        values = [c[key] for c in children.values() if isinstance(c.get(key), int)]
        if values:
            block[label] = sum(values)
    stamps = sorted(str(c.get("ts", "")) for c in children.values())
    return {
        "child_tokens": {
            **block,
            "unit": "tokens",
            "source": "self-reported",
            "children": len(children),
            "first": stamps[0],
            "last": stamps[-1],
        }
    }


def member_usage(run_path: Path) -> dict[str, Any]:
    """The ledger for one member's run; ``{}`` when nothing was observed."""
    children: dict[str, dict[str, Any]] = {}
    usage = _tally(_events(run_path), children)
    return {**usage, **_child_block(children)}


def formation_usage(run_paths: Iterable[Path]) -> dict[str, Any]:
    """The formation total: members' bytes summed, each child's tokens counted once."""
    children: dict[str, dict[str, Any]] = {}
    records = (record for run in run_paths for record in _events(run))
    return {**_tally(records, children), **_child_block(children)}
