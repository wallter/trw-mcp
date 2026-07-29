"""FR06: hook-firing telemetry with a BLOCKS-ONLY false-block denominator.

``recent_outcomes`` is an append-only log, not a ring buffer: "the first 30" is
implemented as a QUERY over block-class entries so allow-class volume can never
evict a block, and can never dilute the rate. A block only becomes a false block
when a human says so (``dispose_last_block``) — never auto-inferred.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from trw_mcp.security.intent_contract._atomic_json import atomic_write_json, locked

__all__ = [
    "BLOCK_CLASS_OUTCOMES",
    "Outcome",
    "dispose_last_block",
    "false_block_rate",
    "read_telemetry",
    "record_firing",
    "telemetry_path",
]

Outcome = Literal["blocked", "false_block", "break_glass", "allowed_match", "allowed_no_match", "infra_error"]

#: Only these outcomes enter the false-block denominator (R15). ``infra_error``
#: is deliberately EXCLUDED: a falsifier that could not be evaluated (collection
#: error, missing dependency, timeout) blocks, but it is an infrastructure defect,
#: not a judgement that the edit was safe or unsafe — counting it either way would
#: corrupt the rate the Track T gate is measured against.
BLOCK_CLASS_OUTCOMES: frozenset[str] = frozenset({"blocked", "false_block"})

_DEFAULT_RELATIVE_PATH = ".trw/context/intent-hook-telemetry.json"
_COUNTER_KEYS = ("fires", "false_blocks", "break_glass")


def telemetry_path(root: Path, configured: str | None = None) -> Path:
    return root / (configured or _DEFAULT_RELATIVE_PATH)


def _empty() -> dict[str, object]:
    return {"fires": 0, "false_blocks": 0, "break_glass": 0, "recent_outcomes": []}


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
    for key in _COUNTER_KEYS:
        value = parsed.get(key)
        state[key] = value if isinstance(value, int) else 0
    outcomes = parsed.get("recent_outcomes")
    state["recent_outcomes"] = [str(item) for item in outcomes] if isinstance(outcomes, list) else []
    return state


def _outcomes(state: dict[str, object]) -> list[str]:
    return cast("list[str]", state.get("recent_outcomes", []))


def _mutate(path: Path, mutate: Callable[[dict[str, object]], None]) -> dict[str, object]:
    """Load, apply *mutate*, and store under an exclusive lock on the file itself."""
    with locked(path):
        state = _load(path)
        mutate(state)
        atomic_write_json(path, state)
        return state


def record_firing(root: Path, outcome: Outcome, configured: str | None = None) -> dict[str, object]:
    """Append one firing outcome and increment its counter atomically."""

    def _apply(state: dict[str, object]) -> None:
        state["fires"] = int(cast("int", state["fires"])) + 1
        if outcome == "false_block":
            state["false_blocks"] = int(cast("int", state["false_blocks"])) + 1
        elif outcome == "break_glass":
            state["break_glass"] = int(cast("int", state["break_glass"])) + 1
        _outcomes(state).append(outcome)

    return _mutate(telemetry_path(root, configured), _apply)


def dispose_last_block(root: Path, *, is_false_positive: bool, configured: str | None = None) -> bool:
    """Human disposition step: convert the most recent ``blocked`` to a false block.

    Returns True when a pending block was dispositioned. A block is NEVER
    auto-classified as a false positive by any code path.
    """
    converted = False

    def _apply(state: dict[str, object]) -> None:
        nonlocal converted
        if not is_false_positive:
            return
        outcomes = _outcomes(state)
        for index in range(len(outcomes) - 1, -1, -1):
            if outcomes[index] == "blocked":
                outcomes[index] = "false_block"
                state["false_blocks"] = int(cast("int", state["false_blocks"])) + 1
                converted = True
                return

    _mutate(telemetry_path(root, configured), _apply)
    return converted


def read_telemetry(root: Path, configured: str | None = None) -> dict[str, object]:
    return _load(telemetry_path(root, configured))


def false_block_rate(root: Path, window: int = 30, configured: str | None = None) -> float | None:
    """Rate over the LAST *window* BLOCK-CLASS outcomes, or ``None`` when undefined.

    Allow-class outcomes never enter the denominator, so a mechanism that mostly
    allows cannot dilute its own false-block rate below the gate threshold.
    """
    if window < 1:
        return None
    blocks = [item for item in _outcomes(_load(telemetry_path(root, configured))) if item in BLOCK_CLASS_OUTCOMES]
    if len(blocks) < window:
        return None
    sample = blocks[-window:]
    return sum(1 for item in sample if item == "false_block") / float(window)
