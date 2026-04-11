"""YAML-backed nudge content loader for pool-based selection.

PRD-CORE-129: Loads nudge messages from YAML files in data/surfaces/,
supporting phase-hint filtering and both list (workflow) and dict
(ceremony) message formats.

Bounded context: content loading only. No state mutation, no decision logic.
"""

from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from ruamel.yaml import YAML

logger = structlog.get_logger(__name__)

_yaml = YAML(typ="safe")
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "surfaces"


@lru_cache(maxsize=4)
def _load_pool_yaml(pool: str) -> dict[str, Any]:
    """Load and cache a pool's YAML content file.

    Returns empty dict if the file does not exist or is malformed.
    Fail-open: never raises.
    """
    path = _DATA_DIR / f"nudge_{pool}.yaml"
    if not path.exists():
        logger.debug("nudge_pool_yaml_not_found", pool=pool, path=str(path))
        return {}
    try:
        raw = _yaml.load(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:  # justified: fail-open — malformed YAML returns empty, never raises
        logger.debug("nudge_pool_yaml_load_failed", pool=pool, exc_info=True)
        return {}


def load_pool_message(pool: str, phase_hint: str = "") -> str:
    """Load a random message from a pool's YAML file.

    For list-format pools (workflow): filters by phase_hint when provided,
    falls back to random selection from all messages.

    For dict-format pools (ceremony): uses phase_hint as a direct key
    lookup into the messages dict.

    Returns empty string if no messages are available.
    """
    data = _load_pool_yaml(pool)
    messages = data.get("messages", [])
    if not messages:
        return ""

    if isinstance(messages, dict):
        # Ceremony pool: keyed by step name
        if phase_hint and phase_hint in messages:
            entry = messages[phase_hint]
            if isinstance(entry, dict):
                return str(entry.get("text", ""))
        return ""

    # Workflow pool: list with phase_hint filtering
    if not isinstance(messages, list):
        return ""

    if phase_hint:
        relevant = [m for m in messages if isinstance(m, dict) and m.get("phase_hint", "") == phase_hint]
        if relevant:
            chosen = random.choice(relevant)
            return str(chosen.get("text", "")) if isinstance(chosen, dict) else ""

    # Fallback: random from all messages
    chosen = random.choice(messages)
    return str(chosen.get("text", "")) if isinstance(chosen, dict) else ""
