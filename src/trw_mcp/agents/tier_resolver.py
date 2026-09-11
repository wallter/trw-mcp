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

Claude Code tier-resolution policy (PRD-QUAL-116 FR04; standing operator
rule 2026-07-07, recorded in ``CLAUDE-5-INTEGRATION-PLAN-2026-07-09.md``
§3 constraint 1):

- ``frontier`` resolves to ``opus`` for claude-code subagents. This is
  deliberate: the strongest *subagent* tier is Opus, not the mythos-class
  Fable tier.
- ``fable`` is INTENTIONALLY ABSENT from :data:`_CLAUDE_CODE_MAP`. The
  operator rule "no Fable-class subagents" (worded per-generation as
  "no Fable-5 subagents" on 2026-07-07; Fable 5.1 shipped 2026-09-01 and is
  covered by the same rule) means TRW-generated subagents run
  opus/sonnet/haiku only — Fable is a main-loop/orchestrator model, never a
  subagent target (the sole exception, initial PRD drafts, happens at the
  main-loop level, not through this resolver). The missing ``fable`` key is
  therefore policy, not a gap: do NOT "fix" it by adding a ``fable`` mapping.
- Mythos-class main-loop models INHERIT. TRW never rewrites the parent
  (main-loop) model, so a Fable/Mythos main loop is unaffected by tier
  resolution — this resolver only ever rewrites subagent ``model:`` lines,
  and ``rewrite_model_line`` leaves any line it does not match untouched.

See ``docs/CLIENT-PROFILES.md`` §Other Profile Notes for the matching
portable-facing note.
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)


# --- Vocabulary ---------------------------------------------------------------

#: Capability tiers used in framework-facing guidance and bundled agents.
#: When adding a new tier, every entry of :data:`_CLIENT_MAPS` MUST be
#: extended in lockstep so the resolver does not raise ``ValueError`` for
#: a known tier on any adapted client.
KNOWN_TIERS: frozenset[str] = frozenset({"frontier", "balanced", "local-large", "local-small"})

#: Recognised client-profile identifiers — the seven active profiles from
#: ``docs/CLIENT-PROFILES.md``. Clients in this set but absent from
#: :data:`_CLIENT_MAPS` are intentional passthrough (the harness accepts the
#: tier vocabulary directly, or the adapter has not yet been written and we
#: prefer to surface the tier at the destination). The retired ``aider``
#: identifier is deliberately absent: it takes the unknown-client path and
#: degrades to a safe default rather than receiving a raw tier token.
KNOWN_CLIENTS: frozenset[str] = frozenset(
    {
        "antigravity-cli",
        "claude-code",
        "codex",
        "copilot",
        "cursor-cli",
        "cursor-ide",
        "opencode",
    }
)


# --- Per-client mapping tables -----------------------------------------------

# Authoritative source: Claude Code subagent docs (code.claude.com/docs/en/sub-agents).
# Corrected 2026-09-10: that page now enumerates accepted ``model:`` values as
# ``fable | sonnet | opus | haiku | <full-model-id> | inherit`` -- ``fable`` IS
# accepted by the harness. The earlier comment here omitted it, which made the
# absence of a ``fable`` key above look like an oversight against the cited
# source. It is not: the harness accepting a value and TRW choosing to emit it
# are different questions, and the operator rule answers the second. Keeping the
# enumeration accurate matters precisely so the policy reads as policy.
# The aliases below are the harness-accepted shortnames; we deliberately use them
# rather than full model IDs so the resolver remains stable across Anthropic
# minor-version bumps.
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

# Antigravity CLI: its subagent reference (antigravity.google/docs/subagents)
# enumerates the accepted ``model:`` values as ``inherit``, ``flash`` and
# ``pro`` — three tokens, no model ids. TRW's retired templates wrote literal
# ``gemini-2.5-flash``/``gemini-2.5-pro``, which that schema does not admit, and
# every unmapped client before this landed a raw tier token instead. Two
# capability points have to carry four tiers: the two local-* tiers name small,
# fast models and resolve to ``flash``; ``frontier`` and ``balanced`` both name
# TRW's strong-reasoning work (audit, review, requirements) and resolve to
# ``pro`` rather than degrading a reviewer to the fast model.
_ANTIGRAVITY_MAP: dict[str, str] = {
    "frontier": "pro",
    "balanced": "pro",
    "local-large": "flash",
    "local-small": "flash",
}

_CLIENT_MAPS: dict[str, dict[str, str]] = {
    "antigravity-cli": _ANTIGRAVITY_MAP,
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

#: ``{tool:trw_x}`` markers in bundled agent bodies. Bundled files stay
#: profile-neutral; each install renders them for its own client.
_TOOL_PLACEHOLDER_RE = re.compile(r"\{tool:(trw_\w+)\}")


def render_agent_tool_names(text: str, *, client: str) -> str:
    """Expand ``{tool:trw_X}`` placeholders for *client*'s tool namespace.

    Bundled agents reference TRW tools through placeholders so one file can ship
    to harnesses that namespace MCP tools differently (claude-code exposes
    ``mcp__trw__trw_recall``; lighter profiles use the bare name). Without this
    step the installed agent tells the model to call a literal
    ``{tool:trw_recall}``, which is not a tool on any harness.

    Args:
        text: Full agent file content (frontmatter and body).
        client: Client-profile identifier. Unknown identifiers resolve through
            the profile registry's own fallback.

    Returns:
        *text* with every well-formed placeholder replaced by the rendered tool
        name. Malformed placeholders are left literal for the message-layer
        linter to surface.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile
    from trw_mcp.prompts.messaging import render_tool_name

    # An unrecognised harness gets bare tool names, the legacy-safe form the
    # message layer uses for ``profile=None``. Inheriting claude-code's
    # ``mcp__trw__`` prefix here would assert a namespace we cannot know the
    # harness uses — the same defensive stance ``resolve_tier`` takes when it
    # degrades an unknown client's model to ``inherit``.
    profile = resolve_client_profile(client) if client in KNOWN_CLIENTS else None
    return _TOOL_PLACEHOLDER_RE.sub(lambda m: render_tool_name(m.group(1), profile), text)


def materialize_agent(text: str, *, client: str) -> str:
    """Apply every bundle→installed transform for *client*, in order.

    The single definition of "what an installed agent looks like". Both the
    installer (``bootstrap/_install_one_agent``) and the update-path hash
    comparison (``bootstrap/_version_manifest._render_agent``) call this; when
    they each applied their own subset, an agent that was already up to date
    reported as a pending update forever.

    Three transforms, in this order (PRD-CORE-252-FR02):

    1. ``{tool:trw_x}`` body placeholders render into the client's namespace;
    2. the capability-tier ``model:`` line resolves to the client's vocabulary;
    3. the frontmatter block is re-emitted in the client's own agent format —
       mapped keys under their client names, unsupported keys dropped, derived
       keys computed, re-serialized as YAML frontmatter or TOML.

    Step 3 runs last so it operates on already-resolved values: the ``model``
    key a client retains carries the resolved token, not the bundled tier. For
    claude-code — the dialect the bundle is authored in — step 3 is the
    identity by derivation, so its output stays byte-identical.

    Raises:
        ValueError: propagated from :func:`resolve_tier` for an unknown tier.
        AgentFormatError: when *client* has no agent surface, is not a
            registered client id, or the bundled frontmatter cannot be
            translated for it.
    """
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.agents.agent_frontmatter import translate_agent_document

    resolved = rewrite_model_line(render_agent_tool_names(text, client=client), client=client)
    return translate_agent_document(resolved, agent_format_for(client))


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
