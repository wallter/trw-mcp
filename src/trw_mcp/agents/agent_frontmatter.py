"""Bundled-agent frontmatter translation (PRD-CORE-252-FR02).

Belongs to the ``tier_resolver.py`` facade: ``materialize_agent`` calls
:func:`translate_agent_document` as its third and last transform, after tool
placeholders are rendered and the capability-tier ``model:`` line is resolved.

What it does
------------
The eleven bundled specialists are authored in Claude Code's dialect. Every one
declares ``effort``, ``maxTurns``, ``memory`` and ``disallowedTools`` — four
keys no other harness's shipped agent format documents — and lists MCP tools
under Claude Code's ``mcp__trw__`` namespace. Emitting that block to Cursor,
OpenCode, Codex, Copilot or Antigravity is not a translation; it is a copy with
a different filename.

This module parses the block, applies the target's
:class:`~trw_mcp.agents.agent_formats.AgentFormat` — mapped keys under their
client names, unsupported keys dropped, tool names rewritten into the client's
namespace, derived keys computed from the bundle — and re-serializes in the
client's declared serialization.

For a client whose translation is the identity (claude-code, by derivation not
by branch) the document is returned untouched, which is what makes the
byte-identity regression anchor exact rather than approximate.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
import yaml

from trw_mcp.agents.agent_formats import BUNDLED_AGENT_KEYS, AgentFormat
from trw_mcp.exceptions import AgentFormatError

logger = structlog.get_logger(__name__)

__all__ = ["split_agent_document", "translate_agent_document"]

_FENCE = "---"

#: Host tools whose presence in a bundled ``tools:`` grant means the agent may
#: change the workspace. Used to derive Cursor's ``readonly``, Codex's
#: ``sandbox_mode`` and OpenCode's ``permissions`` from the bundle instead of
#: from a hand-kept list of agent names — the retired stub sets decided Cursor's
#: ``readonly`` by testing ``name != "trw-implementer"``, which is exactly the
#: per-client hand-maintenance this PRD removes.
_WRITE_CAPABLE_TOOLS: frozenset[str] = frozenset({"Bash", "Edit", "Write", "NotebookEdit"})

#: The namespace the bundle spells its own MCP tool grants in.
_BUNDLED_MCP_PREFIX = "mcp__trw__"


def split_agent_document(text: str) -> tuple[str, str]:
    """Split *text* into its raw frontmatter block and its body.

    Args:
        text: Full agent file content.

    Returns:
        ``(frontmatter_text, body)``. The frontmatter excludes both fences; the
        body is everything after the closing fence's newline.

    Raises:
        AgentFormatError: When the document has no opening fence or no closing
            fence. A bundled agent without parseable frontmatter is a defect in
            the bundle, and the installer records it per-agent rather than
            writing a file the harness cannot load.
    """
    if not text.startswith(_FENCE):
        raise AgentFormatError("agent document has no opening '---' frontmatter fence")
    rest = text[len(_FENCE) :]
    if not rest.startswith("\n"):
        raise AgentFormatError("agent document's opening fence is not followed by a newline")
    end = rest.find(f"\n{_FENCE}")
    if end == -1:
        raise AgentFormatError("agent document has no closing '---' frontmatter fence")
    frontmatter = rest[1:end]
    after = rest[end + 1 + len(_FENCE) :]
    return frontmatter, after.removeprefix("\n")


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    """Parse the YAML frontmatter block into a mapping."""
    try:
        parsed = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise AgentFormatError(f"agent frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AgentFormatError("agent frontmatter is not a mapping")
    return {str(key): value for key, value in parsed.items()}


def _is_read_only(fields: dict[str, Any]) -> bool:
    """Whether the bundled grants say this agent cannot change the workspace.

    True when the ``tools`` grant names no write-capable host tool. A bundle
    with no ``tools`` key at all is treated as write-capable: an unstated grant
    means the harness default, and claiming read-only for an agent that can
    edit is the failure direction that costs the user something.
    """
    granted = fields.get("tools")
    if not isinstance(granted, list):
        return False
    names = {str(item) for item in granted}
    return not (names & _WRITE_CAPABLE_TOOLS)


def _derived_value(key: str, fields: dict[str, Any]) -> object:
    """Compute one derived frontmatter value from the bundled *fields*."""
    read_only = _is_read_only(fields)
    if key == "readonly":
        return read_only
    if key == "sandbox_mode":
        return "read-only" if read_only else "workspace-write"
    if key == "permissions":
        # OpenCode v2 permissions are an ORDERED ARRAY of {action, resource,
        # effect} rules, not a keyed map: "Permissions are an ordered array of
        # rules" (opencode.ai/v2/docs/agents, fetched 2026-09-03), and that
        # page's own read-only reviewer example is exactly the two rules below.
        # TRW's retired opencode bundle shipped `bash: deny / edit: deny /
        # write: deny` — the wrong SHAPE and, twice over, the wrong vocabulary:
        # the documented v2 action names are `shell` for shell commands and
        # `edit` for "all edit/write/patch tools", so `bash` and `write` name
        # nothing. Emitted only when the bundled grants say the agent is
        # read-only; a write-capable agent gets the harness default rather than
        # an invented allowance.
        if not read_only:
            return None
        return [
            {"action": "edit", "resource": "*", "effect": "deny"},
            {"action": "shell", "resource": "*", "effect": "deny"},
        ]
    raise AgentFormatError(f"no derivation defined for frontmatter key {key!r}")


def _normalize_scalar(value: object) -> object:
    """Trim incidental whitespace a bundled block scalar carries into a value.

    The bundle writes most descriptions as YAML folded scalars (``>``), which
    parse with a trailing newline. Re-dumping that verbatim produces a quoted
    multi-line scalar with a stray blank line inside it — valid YAML, but ugly
    in a file a user reads, and it makes the emitted bytes depend on the
    authoring style rather than the content.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_normalize_scalar(item) for item in value]
    return value


def _rewrite_tool_namespace(value: object, namespace: str) -> object:
    """Rewrite ``mcp__trw__``-prefixed tool names in *value* into *namespace*.

    Applies to a retained key whose value is a tool grant. The bundle is
    authored in claude-code's namespace, so any surviving grant must be
    re-prefixed for a harness that names the same tools differently — including
    to the bare name when the profile declares an empty prefix.
    """
    if isinstance(value, str):
        if value.startswith(_BUNDLED_MCP_PREFIX):
            return f"{namespace}{value.removeprefix(_BUNDLED_MCP_PREFIX)}"
        return value
    if isinstance(value, list):
        return [_rewrite_tool_namespace(item, namespace) for item in value]
    return value


def _translate_tool_grants(value: object, fmt: AgentFormat) -> list[str]:
    """Render a bundled ``tools`` grant in *fmt*'s own tool vocabulary.

    The bundle names Claude Code's host tools (``Read``, ``Bash``, ...) and its
    MCP tools (``mcp__trw__trw_recall``). A client that documents a grant list
    needs both halves translated: host tools through the alias table its own
    reference publishes, MCP tools through the grant spelling that reference
    uses. A host tool with no documented alias is DROPPED rather than passed
    through — an unmapped name in a restrictive grant list either does nothing
    or silently removes a capability, and neither is worth guessing at.

    Returns a sorted, de-duplicated list: several bundled tools can collapse
    onto one alias (``Grep`` and ``Glob`` are both ``search``), and a stable
    order is what makes a re-render byte-comparable for the idempotence guard.
    """
    if not isinstance(value, list):
        return []
    grants: set[str] = set()
    for item in value:
        name = str(item)
        if name.startswith(_BUNDLED_MCP_PREFIX):
            bare = name.removeprefix(_BUNDLED_MCP_PREFIX)
            prefix = fmt.mcp_grant_prefix or fmt.tool_namespace
            grants.add(f"{prefix}{bare}")
            continue
        alias = fmt.host_tool_aliases.get(name)
        if alias is not None:
            grants.add(alias)
    return sorted(grants)


def _translate_fields(fields: dict[str, Any], fmt: AgentFormat) -> dict[str, object]:
    """Apply *fmt*'s key map, drop set, constants and derivations to *fields*."""
    translated: dict[str, object] = {}
    for bundled_key, value in fields.items():
        if bundled_key in fmt.dropped_keys:
            continue
        client_key = fmt.key_map.get(bundled_key)
        if client_key is None:
            # Neither mapped nor dropped. A bundled agent grew a key nobody
            # decided the per-client fate of; a silent passthrough here is the
            # original defect, so this is a hard failure.
            raise AgentFormatError(
                f"bundled frontmatter key {bundled_key!r} has no mapping and no drop rule for client {fmt.client_id!r}"
            )
        if bundled_key == "tools" and fmt.host_tool_aliases:
            translated[client_key] = _translate_tool_grants(value, fmt)
            continue
        translated[client_key] = _normalize_scalar(_rewrite_tool_namespace(value, fmt.tool_namespace))

    for constant_key, constant in fmt.constant_keys.items():
        translated[constant_key] = list(constant) if isinstance(constant, tuple) else constant

    for derived_key in fmt.derived_keys:
        derived = _derived_value(derived_key, fields)
        if derived is not None:
            translated[derived_key] = derived
    return translated


def _dump_yaml_frontmatter(fields: dict[str, object]) -> str:
    """Serialize *fields* as a YAML frontmatter block body.

    ``yaml.safe_dump`` quotes any value that would otherwise be misread — shell
    metacharacters, colons, leading indicators, ANSI escapes — so no
    agent-derived text can break out of its scalar (NFR03). ``sort_keys=False``
    keeps the bundle's authoring order, which is what makes a re-render a
    stable byte comparison for the idempotence guard.
    """
    dumped = yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100_000)
    return dumped.rstrip("\n")


def _dump_toml_document(fields: dict[str, object], body: str, body_field: str) -> str:
    """Serialize *fields* plus the agent *body* as a flat TOML document.

    Values go through ``json.dumps``, whose string grammar is a subset of
    TOML's basic string: every control character, quote and backslash is
    escaped, so the body's markdown — fences, quotes, braces — round-trips
    through a TOML parser unchanged.
    """
    lines = [f"{key} = {_toml_scalar(value)}" for key, value in fields.items()]
    lines.append(f"{body_field} = {json.dumps(body)}")
    return "\n".join(lines) + "\n"


def _toml_scalar(value: object) -> str:
    """Render one TOML scalar or flat array."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(item) for item in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(f"{key} = {_toml_scalar(item)}" for key, item in value.items())
        return "{ " + inner + " }"
    raise AgentFormatError(f"cannot render {type(value).__name__} as a TOML value")


def translate_agent_document(text: str, fmt: AgentFormat) -> str:
    """Re-emit agent *text* in the shape *fmt* declares.

    Args:
        text: Full agent file content, with tool placeholders already rendered
            and the ``model:`` tier already resolved for this client.
        fmt: The target client's registry entry.

    Returns:
        The document as that client's harness expects to read it.

    Raises:
        AgentFormatError: When the client has no agent surface, when the
            frontmatter is unparseable, or when a bundled key has neither a
            mapping nor a drop rule for this client.
    """
    if not fmt.supports_agents:
        raise AgentFormatError(f"client {fmt.client_id!r} has no agent surface: {fmt.unsupported_reason}")
    if fmt.translation_is_identity:
        return text

    frontmatter, body = split_agent_document(text)
    fields = _parse_frontmatter(frontmatter)
    unknown = sorted(set(fields) - BUNDLED_AGENT_KEYS)
    if unknown:
        raise AgentFormatError(f"bundled agent declares unregistered frontmatter keys {unknown}")
    translated = _translate_fields(fields, fmt)
    logger.debug(
        "agent_frontmatter_translated",
        client=fmt.client_id,
        kept=sorted(translated),
        dropped=sorted(fmt.dropped_keys & set(fields)),
    )

    if fmt.serialization == "toml":
        if fmt.body_field is None:  # unreachable: AgentFormat's validator requires it
            raise AgentFormatError(f"client {fmt.client_id!r} declares toml serialization with no body field")
        return _dump_toml_document(translated, body, fmt.body_field)
    block = _dump_yaml_frontmatter(translated)
    return _FENCE + "\n" + block + "\n" + _FENCE + "\n\n" + body.lstrip("\n")
