"""Centralized AI-facing messaging — single source of truth for TRW message strings.

PRD-INFRA-012: Centralized AI-Facing Messaging Registry with Value-Oriented Framing.

Loads messages from ``data/messages/messages.yaml`` and provides them as typed
accessors to all Python consumers (server.py, middleware, claude_md.py).

Shell hooks read deployed copies via grep (same pattern as behavioral_protocol.yaml).

No imports from other trw_mcp modules — prevents circular dependencies.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config._client_profile import ClientProfile

_DATA_DIR = Path(__file__).parent.parent / "data" / "messages"

_logger = structlog.get_logger(__name__)

# PRD-FIX-078: {tool:trw_xxx} placeholder expansion. Only accepts bare identifiers
# starting with ``trw_`` followed by one or more word chars — rejects format
# directives (``!r``, ``:spec``) and empty bodies (NFR03).
_TOOL_PLACEHOLDER_RE = re.compile(r"\{tool:(trw_\w+)\}")
_TOOL_PLACEHOLDER_ANY_RE = re.compile(r"\{tool:([^}]*)\}")


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

    # Use safe loader — bundled file, but defense-in-depth against tampered packages.
    yaml = YAML(typ="safe")
    path = _DATA_DIR / "messages.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.load(f)
    return dict(data) if data else {}


def render_tool_name(tool_name: str, profile: ClientProfile | None = None) -> str:
    """Render a tool name with the profile's MCP namespace prefix (PRD-FIX-078).

    claude-code exposes MCP tools under ``mcp__{server}__{tool}``; other clients
    (opencode, cursor-ide, codex, aider) use bare names. The ``ClientProfile``
    carries a ``tool_namespace_prefix`` field that this helper prepends.

    Non-``trw_`` tool names (e.g., ``Bash``) are returned unchanged.

    Args:
        tool_name: Bare tool name such as ``trw_session_start``.
        profile: Active ``ClientProfile``. ``None`` yields bare names (legacy).

    Returns:
        Prefixed tool name (``mcp__trw__trw_session_start`` for claude-code) or
        bare name.
    """
    if not tool_name.startswith("trw_"):
        return tool_name
    if profile is None:
        return tool_name
    return f"{profile.tool_namespace_prefix}{tool_name}"


def _expand_tool_placeholders(text: str, profile: ClientProfile | None) -> str:
    """Substitute ``{tool:trw_X}`` placeholders with the rendered tool name.

    PRD-FIX-078 FR02 / NFR02 / NFR03:
    - Well-formed ``{tool:trw_foo}`` → ``render_tool_name("trw_foo", profile)``
    - Malformed placeholders (``{tool:}`` empty, ``{tool:!r}`` format directive,
      ``{tool:not_trw}`` non-trw prefix) log a warning and are left literal.
    """

    def _replace_valid(match: re.Match[str]) -> str:
        return render_tool_name(match.group(1), profile)

    # First pass: replace all valid placeholders.
    rendered = _TOOL_PLACEHOLDER_RE.sub(_replace_valid, text)

    # Second pass: surface malformed placeholders (still literal in output).
    for malformed in _TOOL_PLACEHOLDER_ANY_RE.finditer(rendered):
        body = malformed.group(1)
        if not body or not body.startswith("trw_") or not re.fullmatch(r"trw_\w+", body):
            _logger.warning(
                "messaging_malformed_tool_placeholder",
                placeholder=malformed.group(0),
                body=body,
            )

    return rendered


def get_message(
    key: str,
    profile: ClientProfile | None = None,
    **kwargs: object,
) -> str:
    """Get a message by key, with optional ``str.format()`` substitution.

    PRD-FIX-078: when ``profile`` is supplied, ``{tool:trw_X}`` placeholders are
    expanded to the profile's rendered tool name. Calls without ``profile`` keep
    placeholders (or, equivalently, render bare names via ``render_tool_name``
    with ``profile=None``) — legacy-safe default for existing call sites.

    Args:
        key: Message key from messages.yaml.
        profile: Optional active ``ClientProfile`` for tool-name rendering.
        **kwargs: Format string substitutions (e.g., event_count=5).

    Returns:
        Formatted message string.

    Raises:
        KeyError: If message key not found.
    """
    messages = _load_messages()
    raw = str(messages[key])
    # PRD-FIX-078: always expand {tool:...} placeholders. When profile is None,
    # the renderer falls back to bare names (legacy-safe behavior for callers
    # that haven't been threaded with a profile yet).
    raw = _expand_tool_placeholders(raw, profile)
    if kwargs:
        return raw.format(**{k: str(v) for k, v in kwargs.items()})
    return raw


def render_message(key: str, profile: ClientProfile | None, **kwargs: object) -> str:
    """Explicit profile-aware renderer (PRD-FIX-078 convenience wrapper)."""
    return get_message(key, profile=profile, **kwargs)


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
        return get_message(key, profile=None, **kwargs)
    except Exception:  # justified: fail-open, message registry errors fall back to inline defaults
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
