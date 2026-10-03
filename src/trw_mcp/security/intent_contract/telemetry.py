"""FR06: hook-firing telemetry, bounded: counters per outcome plus the last outcome.

Every intent hook fire (each Write/Edit) rewrites this file under a lock, so its size must not grow with the
number of fires. It used to append every outcome to ``recent_outcomes`` for a false-block rate that no production
path read or could feed (UF-MCP-03: 15,023 fires, 0 blocks, a 360 KB file rewritten per fire); the rate and that
list are gone, and a legacy list is folded into the counts on the next write.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from trw_mcp.security.intent_contract._atomic_json import atomic_write_json, locked

__all__ = ["Outcome", "read_telemetry", "record_firing", "telemetry_path"]

Outcome = Literal["blocked", "false_block", "break_glass", "allowed_match", "allowed_no_match", "infra_error"]

_DEFAULT_RELATIVE_PATH = ".trw/context/intent-hook-telemetry.json"


def telemetry_path(root: Path, configured: str | None = None) -> Path:
    return root / (configured or _DEFAULT_RELATIVE_PATH)


def _empty() -> dict[str, object]:
    return {"fires": 0, "break_glass": 0, "outcomes": {}, "last_outcome": ""}


def _load(path: Path) -> dict[str, object]:
    if not path.exists():
        return _empty()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(parsed, dict):
        return _empty()
    state = _empty()
    for key in ("fires", "break_glass"):
        value = parsed.get(key)
        state[key] = value if isinstance(value, int) else 0
    counts = parsed.get("outcomes")
    outcomes = {str(k): v for k, v in counts.items() if isinstance(v, int)} if isinstance(counts, dict) else {}
    legacy = parsed.get("recent_outcomes")
    if isinstance(legacy, list):  # a pre-UF-MCP-03 file: fold its log into the counts once; the log is not kept
        for item in legacy:
            outcomes[str(item)] = outcomes.get(str(item), 0) + 1
        state["last_outcome"] = str(legacy[-1]) if legacy else ""
    last = parsed.get("last_outcome")
    if isinstance(last, str) and last:
        state["last_outcome"] = last
    state["outcomes"] = outcomes
    return state


def _mutate(path: Path, mutate: Callable[[dict[str, object]], None]) -> dict[str, object]:
    """Load, apply *mutate*, and store under an exclusive lock on the file itself."""
    with locked(path):
        state = _load(path)
        mutate(state)
        atomic_write_json(path, state)
        return state


def record_firing(root: Path, outcome: Outcome, configured: str | None = None) -> dict[str, object]:
    """Count one firing outcome atomically."""

    def _apply(state: dict[str, object]) -> None:
        state["fires"] = int(cast("int", state["fires"])) + 1
        if outcome == "break_glass":
            state["break_glass"] = int(cast("int", state["break_glass"])) + 1
        counts = cast("dict[str, int]", state["outcomes"])
        counts[outcome] = counts.get(outcome, 0) + 1
        state["last_outcome"] = outcome

    return _mutate(telemetry_path(root, configured), _apply)


def read_telemetry(root: Path, configured: str | None = None) -> dict[str, object]:
    return _load(telemetry_path(root, configured))
