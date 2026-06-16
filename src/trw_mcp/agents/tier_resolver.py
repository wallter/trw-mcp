"""Per-client capability-tier → model-ID resolver.

PRD-INFRA-104.

The framework uses a deliberate capability-tier vocabulary
(``frontier|balanced|local-large|local-small``) in bundled agent files.
Each client harness accepts a different concrete vocabulary in the
``model:`` field of its agent frontmatter — this module owns the
translation table.

Public API:
    resolve_tier(tier, *, client) -> str
        Translate a tier to the model identifier the client accepts.
    rewrite_model_line(text, *, client) -> str
        Rewrite the first ``^model:`` line in *text* using ``resolve_tier``.
    KNOWN_TIERS: frozenset[str]
        The four canonical capability tiers.
    KNOWN_CLIENTS: frozenset[str]
        Client-profile identifiers recognised by the framework.

The resolver is consumed by:
    - bootstrap/_init_project_skills.py::_install_agents (Claude Code)
    - scripts/sync-agents.py (dev-repo sync)

A future refactor may also have ``clients/llm.py::_resolve_model``
delegate here; that is out of scope for PRD-INFRA-104 (see OQ-1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


# --- Vocabulary ---------------------------------------------------------------

#: Capability tiers used in framework-facing guidance and bundled agents.
#: When adding a new tier, every entry of :data:`_CLIENT_MAPS` MUST be
#: extended in lockstep so the resolver does not raise ``ValueError`` for
#: a known tier on any adapted client.
KNOWN_TIERS: frozenset[str] = frozenset({"frontier", "balanced", "local-large", "local-small"})

#: Recognised client-profile identifiers. Clients in this set but absent
#: from :data:`_CLIENT_MAPS` are intentional passthrough (the harness
#: accepts the tier vocabulary directly, or the adapter has not yet been
#: written and we prefer to surface the tier at the destination).
KNOWN_CLIENTS: frozenset[str] = frozenset(
    {
        "claude-code",
        "cursor-ide",
        "cursor-cli",
        "opencode",
        "codex",
        "copilot",
        "gemini",
        "aider",
    }
)


# --- Per-client mapping tables -----------------------------------------------

# Authoritative source: Claude Code subagent docs (code.claude.com/docs/en/sub-agents)
# enumerate accepted ``model:`` values as ``sonnet | opus | haiku |
# <full-model-id> | inherit``. The aliases below are the harness-accepted
# shortnames; we deliberately use them rather than full model IDs so the
# resolver remains stable across Anthropic minor-version bumps.
_CLAUDE_CODE_MAP: dict[str, str] = {
    "frontier": "opus",
    "balanced": "sonnet",
    "local-large": "sonnet",
    "local-small": "haiku",
}

# Cursor IDE: the existing adapter at bootstrap/_cursor_ide.py:262
# hardcodes ``model: inherit`` for every TRW agent. We mirror that
# behaviour here so the resolver is a true superset of the existing
# special-cased path.
_CURSOR_IDE_MAP: dict[str, str] = dict.fromkeys(KNOWN_TIERS, "inherit")

_CLIENT_MAPS: dict[str, dict[str, str]] = {
    "claude-code": _CLAUDE_CODE_MAP,
    "cursor-ide": _CURSOR_IDE_MAP,
}

#: Universally-safe model token used when a *known* capability tier reaches
#: an unrecognised client (not adapted, not a recognised passthrough). Every
#: major subagent harness accepts ``inherit`` (Claude Code, Cursor) or
#: ignores an unknown ``model:`` more gracefully than rejecting a bare tier
#: token. This is the defence-in-depth net for Potemkin-Gate defect A
#: (sub_zAfRqZYYq2KtF72d): a known tier must NEVER leak raw to a client whose
#: harness would reject it outright ("issue with the selected model
#: (balanced)") and silently disable the agent.
_UNKNOWN_CLIENT_FALLBACK: str = "inherit"


@dataclass(frozen=True)
class LaunchThrottlePolicy:
    """Client-agnostic helper launch throttling guidance."""

    stagger_seconds: float
    max_concurrent_launches: int
    backoff_multiplier: float
    max_backoff_seconds: float
    rationale: str


def resolve_launch_throttle_policy(helper_count: int) -> LaunchThrottlePolicy:
    """Return portable launch throttling guidance for dense helper workloads."""
    if helper_count <= 0:
        raise ValueError("helper_count must be positive")
    if helper_count <= 3:
        return LaunchThrottlePolicy(0.0, helper_count, 1.5, 10.0, "small batch")
    if helper_count <= 8:
        return LaunchThrottlePolicy(1.0, 3, 2.0, 30.0, "moderate dense launch")
    return LaunchThrottlePolicy(2.0, 4, 2.0, 60.0, "large dense launch")


# --- Public API ---------------------------------------------------------------


def resolve_tier(tier: str, *, client: str) -> str:
    """Translate a capability tier to the model id *client* expects.

    Args:
        tier: A value taken from a bundled agent's ``model:`` line. Most
            commonly one of :data:`KNOWN_TIERS`, but this function does
            not validate against ``KNOWN_TIERS`` for unknown clients —
            see passthrough rule below.
        client: A client-profile identifier (e.g. ``"claude-code"``,
            ``"cursor-ide"``). Identifiers without an entry in
            :data:`_CLIENT_MAPS` but present in :data:`KNOWN_CLIENTS` fall
            through to *passthrough*: the ``tier`` argument is returned
            unchanged. Identifiers absent from *both* (a wholly unknown
            harness) degrade a recognised capability tier to
            :data:`_UNKNOWN_CLIENT_FALLBACK` — see the Potemkin-Gate net
            below.

    Returns:
        The resolved model identifier the harness expects.

    Raises:
        ValueError: If *client* has a defined map but *tier* is missing
            from it. The error message names the unknown tier and lists
            the known tiers for that client.

    Examples:
        >>> resolve_tier("frontier", client="claude-code")
        'opus'
        >>> resolve_tier("frontier", client="cursor-ide")
        'inherit'
        >>> resolve_tier("frontier", client="opencode")
        'frontier'
        >>> resolve_tier("balanced", client="some-unknown-harness")
        'inherit'
        >>> resolve_tier("gpt-4o", client="some-unknown-harness")
        'gpt-4o'
    """
    client_map = _CLIENT_MAPS.get(client)
    if client_map is None:
        # Recognised passthrough profile — its harness accepts the tier
        # vocabulary (or ``inherit``) directly, so the tier lands unchanged.
        if client in KNOWN_CLIENTS:
            return tier
        # Wholly unknown harness: a *known* capability tier must never leak
        # raw (it would be rejected outright and silently disable the agent
        # — Potemkin-Gate defect A). Degrade known tiers to a safe default;
        # pass an explicit concrete model id through unchanged.
        if tier in KNOWN_TIERS:
            logger.debug(
                "agent_tier_unknown_client_degraded",
                tier=tier,
                client=client,
                resolved=_UNKNOWN_CLIENT_FALLBACK,
            )
            return _UNKNOWN_CLIENT_FALLBACK
        return tier
    if tier not in client_map:
        raise ValueError(f"Unknown tier {tier!r} for client {client!r}; known tiers: {sorted(client_map)}")
    return client_map[tier]


# Anchored on line start; tolerates any whitespace after ``model:`` and
# any trailing whitespace. The capture group is the first whitespace-free
# token, which mirrors how Claude Code's subagent loader parses the
# field. A trailing comment (``# foo``) is preserved by anchoring on
# ``\S+`` rather than the rest of the line.
_MODEL_LINE_RE = re.compile(r"(?m)^(model:\s*)(\S+)(.*)$")


def rewrite_model_line(text: str, *, client: str) -> str:
    """Rewrite the first ``^model:`` line in *text* via :func:`resolve_tier`.

    Designed for use against an entire agent .md file (frontmatter
    *and* body). The regex is anchored to the start of a line and only
    matches the literal token ``model:`` followed by whitespace and a
    non-whitespace token, so prose mentioning "model:" inside the body
    is safe — see test ``test_install_agents_preserves_other_bytes``.

    Args:
        text: Full agent file content.
        client: Client-profile identifier passed through to
            :func:`resolve_tier`.

    Returns:
        *text* with the first ``model:`` line rewritten. Files without
        a ``model:`` line are returned unchanged. Files with multiple
        ``model:`` lines have only the first rewritten — the second is
        an authoring error and is detected separately by the bundle
        contract tests.

    Raises:
        ValueError: Propagates from :func:`resolve_tier` when the
            captured tier value is unknown for *client*. The caller is
            expected to log + skip the agent (see FR-11).
    """
    match = _MODEL_LINE_RE.search(text)
    if match is None:
        return text
    raw_value = match.group(2)
    resolved = resolve_tier(raw_value, client=client)
    if resolved == raw_value:
        return text
    logger.debug(
        "agent_tier_resolved",
        tier=raw_value,
        client=client,
        resolved=resolved,
    )
    return _MODEL_LINE_RE.sub(
        lambda m: f"{m.group(1)}{resolved}{m.group(3)}",
        text,
        count=1,
    )
