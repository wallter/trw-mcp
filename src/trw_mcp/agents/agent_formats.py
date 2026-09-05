"""Per-client agent-file format registry (PRD-CORE-252-FR01).

One typed entry per id in :data:`trw_mcp.agents.tier_resolver.KNOWN_CLIENTS`,
declaring where that client's agent definitions live, how they are serialized,
which bundled frontmatter keys survive translation, and under what names.

Why a registry
--------------
Before this module each client's agent shape was encoded implicitly and
separately — a dict of TOML strings for Codex, a dict of markdown strings for
Copilot and Antigravity, an f-string for Cursor IDE, a directory of pre-written
files for OpenCode. Nothing declared which frontmatter keys a client accepts, so
nothing could strip the ones it does not, and the eleven bundled specialists
shipped to exactly one harness in exactly one dialect. The Antigravity templates
had already drifted into naming a tool namespace that client's own profile does
not declare. A derived table cannot drift that way: the namespace is read from
:class:`~trw_mcp.models.config._client_profile.ClientProfile`, never restated.

Public API:
    AgentFormat
        The typed entry. Frozen; every field documented.
    agent_format_for(client) -> AgentFormat
        Registry lookup. Raises :class:`~trw_mcp.exceptions.AgentFormatError`
        for an id outside ``KNOWN_CLIENTS``.

Evidence for each row is the vendor's own subagent reference, fetched
2026-09-03; see ``docs/CLIENT-PROFILES.md`` §Agent Surfaces for the citation
table and the one row (cursor-cli) that is an absence rather than a format.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trw_mcp.exceptions import AgentFormatError

__all__ = ["AgentFormat", "agent_format_for"]

#: Every frontmatter key that appears in the bundled agent corpus. Each client
#: entry must account for all of them — a key is either mapped to a client key
#: or explicitly dropped. Adding a key to a bundled agent without deciding its
#: per-client fate fails :meth:`AgentFormat._every_bundled_key_decided` at
#: import time rather than silently leaking claude-code's dialect downstream.
#: Kept honest by ``tests/test_agent_format_adapters.py::
#: test_bundled_key_universe_matches_the_shipped_bundle``.
BUNDLED_AGENT_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "model",
        "effort",
        "maxTurns",
        "memory",
        "tools",
        "disallowedTools",
    }
)

#: The tool namespace the bundled corpus is authored in. A client whose profile
#: declares this same prefix needs no tool-name rewriting.
BUNDLED_TOOL_NAMESPACE: str = "mcp__trw__"

#: Values a client entry may list in :attr:`AgentFormat.derived_keys`. Each is
#: computed from the bundled frontmatter — never from the agent's *name* — so a
#: twelfth specialist inherits the behaviour with no per-client edit.
DerivedKey = Literal["readonly", "sandbox_mode", "permissions"]

Serialization = Literal["yaml_frontmatter_markdown", "toml"]


class AgentFormat(BaseModel):
    """How one client wants a bundled agent written to disk.

    Attributes:
        client_id: Client-profile identifier; must be in ``KNOWN_CLIENTS``.
        supports_agents: False for a harness with no agent surface at all. When
            False, ``destination_dir`` is None and ``unsupported_reason`` is set.
        destination_dir: Repo-relative directory agents are written to, or None.
        filename_suffix: Suffix appended to the bundled agent stem.
        serialization: How the frontmatter block is emitted.
        key_map: Bundled frontmatter key -> this client's key name. Keys absent
            from both this map and ``dropped_keys`` are a construction error.
        dropped_keys: Bundled keys this client's format does not declare. They
            are removed, not passed through: silent passthrough is what shipped
            ``effort``/``maxTurns``/``memory``/``disallowedTools`` to six
            harnesses that document none of them.
        constant_keys: Keys this client's format requires with a fixed value
            that the bundle does not express (e.g. opencode's ``mode``).
        derived_keys: Keys computed from the bundled frontmatter rather than
            copied from it.
        body_field: For ``toml`` serialization, the field the agent body is
            emitted into. None for markdown, where the body follows the fence.
        unsupported_reason: Why this client receives no agents. Non-empty
            exactly when ``supports_agents`` is False.
        max_agent_bytes: Upper bound on a bundled agent file read (NFR03). A
            file over the cap is rejected with a recorded error rather than
            read into memory.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str
    supports_agents: bool = True
    destination_dir: str | None = None
    filename_suffix: str = ".md"
    serialization: Serialization = "yaml_frontmatter_markdown"
    key_map: dict[str, str] = Field(default_factory=dict)
    dropped_keys: frozenset[str] = frozenset()
    constant_keys: dict[str, str | tuple[str, ...]] = Field(default_factory=dict)
    derived_keys: tuple[DerivedKey, ...] = ()
    body_field: str | None = None
    #: Bundled host-tool name -> this client's name for the same capability.
    #: Empty when the client documents no host-tool vocabulary TRW can map, in
    #: which case a retained ``tools`` grant would be untranslatable and the key
    #: belongs in ``dropped_keys`` instead.
    host_tool_aliases: dict[str, str] = Field(default_factory=dict)
    #: How this client spells an MCP tool inside a GRANT list, when that differs
    #: from how it spells one in prose. Copilot documents ``server/tool`` in
    #: ``tools:`` while naming the tool bare everywhere else, so the two are
    #: genuinely different facts rather than a second copy of one. Empty means
    #: grants use the profile's ``tool_namespace_prefix``, like everything else.
    mcp_grant_prefix: str = ""
    unsupported_reason: str | None = None
    #: 256 KiB. The largest bundled specialist is ~18 KiB, so the cap is an
    #: order of magnitude of headroom while still bounding a forked bundle.
    max_agent_bytes: int = Field(default=262_144, gt=0)

    @property
    def tool_namespace(self) -> str:
        """This client's MCP tool prefix, read from its ``ClientProfile``.

        Never restated here. The Antigravity templates this registry replaces
        asserted ``mcp_trw_`` while the profile declared a bare namespace; a
        derived value cannot disagree with its source.
        """
        from trw_mcp.models.config._profiles import resolve_client_profile

        return resolve_client_profile(self.client_id).tool_namespace_prefix

    @property
    def retained_keys(self) -> frozenset[str]:
        """Every frontmatter key this client's rendered agents may carry.

        Includes ``body_field`` for a TOML client: there is no out-of-band body
        in that serialization, so the instructions are one more key the emitted
        document declares and the FR02 key-set assertion must expect it.
        """
        keys = frozenset(self.key_map.values()) | frozenset(self.constant_keys) | frozenset(self.derived_keys)
        return keys | ({self.body_field} if self.body_field else frozenset())

    @property
    def translation_is_identity(self) -> bool:
        """True when translation must not touch the bundled frontmatter bytes.

        The bundle is authored in claude-code's dialect, so for claude-code the
        correct translation is to emit the block verbatim. Deriving that rather
        than special-casing the id keeps the byte-identity regression anchor
        (FR02) a property of the table instead of a branch in the transform.
        """
        return (
            self.supports_agents
            and not self.dropped_keys
            and not self.constant_keys
            and not self.derived_keys
            and all(bundled == client for bundled, client in self.key_map.items())
            and frozenset(self.key_map) == BUNDLED_AGENT_KEYS
            and self.tool_namespace == BUNDLED_TOOL_NAMESPACE
            and self.serialization == "yaml_frontmatter_markdown"
        )

    def destination_for(self, stem: str) -> str:
        """Repo-relative path this client's copy of agent *stem* is written to.

        Raises:
            AgentFormatError: When the client has no agent surface, or *stem*
                is not a safe single path component (NFR03).
        """
        if not self.supports_agents or self.destination_dir is None:
            raise AgentFormatError(f"client {self.client_id!r} has no agent surface: {self.unsupported_reason}")
        if not _SAFE_STEM_RE.match(stem):
            raise AgentFormatError(f"unsafe agent name {stem!r} for client {self.client_id!r}")
        return f"{self.destination_dir}/{stem}{self.filename_suffix}"

    @model_validator(mode="after")
    def _every_bundled_key_decided(self) -> AgentFormat:
        """Enforce the FR01 boundary semantics at construction time."""
        if not self.supports_agents:
            if self.destination_dir is not None or not self.unsupported_reason:
                raise ValueError(f"{self.client_id}: unsupported entries need destination_dir=None and a reason")
            return self
        if self.unsupported_reason is not None:
            raise ValueError(f"{self.client_id}: a supported entry must not carry an unsupported_reason")
        if not self.destination_dir or not self.filename_suffix or not self.retained_keys:
            raise ValueError(f"{self.client_id}: supported entries need a destination, suffix and retained keys")
        decided = frozenset(self.key_map) | self.dropped_keys
        if decided != BUNDLED_AGENT_KEYS:
            missing = sorted(BUNDLED_AGENT_KEYS - decided)
            surplus = sorted(decided - BUNDLED_AGENT_KEYS)
            raise ValueError(f"{self.client_id}: undecided bundled keys {missing}, unknown keys {surplus}")
        if self.serialization == "toml" and not self.body_field:
            raise ValueError(f"{self.client_id}: toml serialization needs a body_field")
        return self


# A bundled agent stem becomes a path component, so it is validated before it
# is joined (NFR03). Anchored, no separators, no dots — ``..`` and ``a/b`` and
# an absolute path all fail here rather than at the filesystem.
_SAFE_STEM_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_-]*\Z")


_REGISTRY: dict[str, AgentFormat] = {
    # The authoring dialect. Every key is retained under its own name and the
    # namespace already matches, so translation is the identity — see
    # ``translation_is_identity``. Source: code.claude.com/docs/en/sub-agents.
    "claude-code": AgentFormat(
        client_id="claude-code",
        destination_dir=".claude/agents",
        key_map={key: key for key in BUNDLED_AGENT_KEYS},
    ),
    # Source: cursor.com/docs/subagents — name, description, model
    # (``inherit`` or an id), readonly, is_background. No ``tools`` key is
    # documented, so the bundled grants are dropped rather than guessed at.
    # ``is_background`` is not emitted: nothing in the bundle expresses it, and
    # the retired stub set keyed it on one hardcoded agent name.
    "cursor-ide": AgentFormat(
        client_id="cursor-ide",
        destination_dir=".cursor/agents",
        key_map={"name": "name", "description": "description", "model": "model"},
        dropped_keys=frozenset({"effort", "maxTurns", "memory", "tools", "disallowedTools"}),
        derived_keys=("readonly",),
    ),
    # Source: opencode.ai/v2/docs/agents (fetched 2026-09-03) — the documented
    # markdown frontmatter is description, mode, model, color, steps and
    # permissions; "the Markdown body becomes system". ``steps`` is opencode's
    # turn budget, which is what the bundled ``maxTurns`` means.
    #
    # ``name`` is dropped: it appears in no documented frontmatter example, and
    # the page derives the agent id from the file path
    # (``.opencode/agents/reviewer.md`` -> ``reviewer``). ``model`` is dropped
    # because opencode wants ``provider/model#variant`` and TRW has no evidenced
    # mapping from a capability tier to a provider-qualified id for this
    # harness. ``tools`` is dropped because opencode expresses tool policy
    # through ``permissions``, which this entry derives instead.
    "opencode": AgentFormat(
        client_id="opencode",
        destination_dir=".opencode/agents",
        key_map={"description": "description", "maxTurns": "steps"},
        dropped_keys=frozenset({"name", "effort", "memory", "model", "tools", "disallowedTools"}),
        constant_keys={"mode": "subagent"},
        derived_keys=("permissions",),
    ),
    # Source: learn.chatgpt.com/docs/agent-configuration/subagents — name,
    # description and developer_instructions are required; model,
    # model_reasoning_effort (low|medium|high|xhigh|ultra) and sandbox_mode are
    # optional. The agent body is the instructions, so it lands in
    # ``developer_instructions`` rather than after a fence.
    "codex": AgentFormat(
        client_id="codex",
        destination_dir=".codex/agents",
        filename_suffix=".toml",
        serialization="toml",
        key_map={"name": "name", "description": "description", "effort": "model_reasoning_effort"},
        dropped_keys=frozenset({"maxTurns", "memory", "model", "tools", "disallowedTools"}),
        derived_keys=("sandbox_mode",),
        body_field="developer_instructions",
    ),
    # Source: docs.github.com/en/copilot/reference/custom-agents-configuration
    # (fetched 2026-09-03) — description is required; name, target, tools,
    # model, disable-model-invocation, user-invocable and mcp-servers are
    # optional. ``tools`` is RESTRICTIVE ("If unset, defaults to all tools";
    # a list enables "only those tools"), and that page publishes a complete,
    # case-insensitive alias table which the bundle's Claude Code tool names
    # are listed in verbatim — so the grants translate exactly rather than
    # being dropped, and a read-only auditor stops being handed ``edit``.
    # MCP tools are referenced ``server/tool`` inside that list.
    #
    # ``mcp-servers`` is deliberately NOT emitted. It is typed ``object`` —
    # a YAML rendering of the MCP server DEFINITION map (name -> type/command/
    # args/tools/env) — and is documented as "Not used in VS Code and other IDE
    # custom agents". TRW's retired template emitted the list ``[trw]``, which
    # is the wrong YAML type for that key; the per-tool ``trw/...`` grants below
    # are the documented way to scope a server's tools, and an unrecognised
    # tool name is documented as ignored rather than fatal.
    "copilot": AgentFormat(
        client_id="copilot",
        destination_dir=".github/agents",
        filename_suffix=".agent.md",
        key_map={"name": "name", "description": "description", "tools": "tools"},
        dropped_keys=frozenset({"effort", "maxTurns", "memory", "model", "disallowedTools"}),
        host_tool_aliases={
            "Bash": "execute",
            "Read": "read",
            "NotebookRead": "read",
            "Edit": "edit",
            "MultiEdit": "edit",
            "Write": "edit",
            "NotebookEdit": "edit",
            "Grep": "search",
            "Glob": "search",
            "Task": "agent",
            "WebSearch": "web",
            "WebFetch": "web",
            "TodoWrite": "todo",
        },
        mcp_grant_prefix="trw/",
    ),
    # Source: antigravity.google/docs/subagents — workspace subagents live in
    # ``.agents/agents`` (the same ``.agents`` tree the documented workspace
    # rules use), and ``model`` accepts only ``inherit``, ``flash`` or ``pro``.
    # TRW previously wrote ``.antigravitycli/agents`` with literal Gemini model
    # ids and ``temperature``/``max_turns``/``timeout_mins``, none of which
    # appear in that reference.
    "antigravity-cli": AgentFormat(
        client_id="antigravity-cli",
        destination_dir=".agents/agents",
        key_map={"name": "name", "description": "description", "model": "model"},
        dropped_keys=frozenset({"effort", "maxTurns", "memory", "tools", "disallowedTools"}),
    ),
    # Not a format — an absence, recorded as one. cursor-cli's bootstrap writes
    # the repo-root AGENTS.md and nothing else, and Cursor's CLI reference says
    # only that the CLI "supports the same modes as the editor"; whether it
    # loads ``.cursor/agents`` is undocumented. Claiming support on that basis
    # would be asserting a behaviour TRW has not measured.
    "cursor-cli": AgentFormat(
        client_id="cursor-cli",
        supports_agents=False,
        unsupported_reason=(
            "cursor-cli has no documented agent-definition surface: its instruction carrier is the "
            "repo-root AGENTS.md, and Cursor's CLI reference does not state that the CLI loads "
            ".cursor/agents. A co-installed Cursor IDE may provide agents in the shared .cursor tree."
        ),
    ),
}


def agent_format_for(client: str) -> AgentFormat:
    """Return the agent-file format *client* uses.

    Args:
        client: A client-profile identifier from ``KNOWN_CLIENTS``.

    Returns:
        The frozen registry entry. Callers must check ``supports_agents``
        before using ``destination_dir``.

    Raises:
        AgentFormatError: When *client* is not a registered client id. The
            registry is keyed on ``KNOWN_CLIENTS`` and nothing else, so an
            unrecognised harness is a caller error rather than a fallback.
    """
    try:
        return _REGISTRY[client]
    except KeyError:
        raise AgentFormatError(f"no agent format registered for client {client!r}") from None
