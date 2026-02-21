"""Centralized AI-facing messaging — single source of truth for TRW message strings.

PRD-INFRA-012: Centralized AI-Facing Messaging Registry with Value-Oriented Framing.

Loads messages from ``data/messages/messages.yaml`` and provides them as typed
accessors to all Python consumers (server.py, middleware, claude_md.py).

Shell hooks read deployed copies via grep (same pattern as behavioral_protocol.yaml).

No imports from other trw_mcp modules — prevents circular dependencies.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_DATA_DIR = Path(__file__).parent.parent / "data" / "messages"


@lru_cache(maxsize=1)
def _load_messages() -> dict[str, object]:
    """Load all messages from the bundled YAML file.

    Returns:
        Dict of message_key -> message content (str or list[str]).

    Raises:
        FileNotFoundError: If messages.yaml is missing (package integrity issue).
    """
    # Import here to keep module-level imports minimal and avoid
    # pulling ruamel.yaml into every consumer that just needs a string.
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.preserve_quotes = True
    path = _DATA_DIR / "messages.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f)
    return dict(data) if data else {}


def get_message(key: str, **kwargs: object) -> str:
    """Get a message by key, with optional ``str.format()`` substitution.

    Args:
        key: Message key from messages.yaml.
        **kwargs: Format string substitutions (e.g., event_count=5).

    Returns:
        Formatted message string.

    Raises:
        KeyError: If message key not found.
    """
    messages = _load_messages()
    raw = str(messages[key])
    if kwargs:
        return raw.format(**{k: str(v) for k, v in kwargs.items()})
    return raw


def get_message_or_default(key: str, default: str, **kwargs: object) -> str:
    """Get a message with fallback default — for backward compatibility.

    Args:
        key: Message key from messages.yaml.
        default: Fallback if key is missing or file not found.
        **kwargs: Format string substitutions.

    Returns:
        Formatted message string or default.
    """
    try:
        return get_message(key, **kwargs)
    except (KeyError, FileNotFoundError):
        if kwargs:
            return default.format(**{k: str(v) for k, v in kwargs.items()})
        return default


def get_message_lines(key: str) -> list[str]:
    """Get a list-type message (e.g., protocol_fallback_lines).

    Args:
        key: Message key that maps to a YAML list.

    Returns:
        List of message strings.

    Raises:
        KeyError: If message key not found.
    """
    messages = _load_messages()
    val = messages[key]
    if isinstance(val, list):
        return [str(item) for item in val]
    return [str(val)]
