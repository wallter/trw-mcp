"""Typed shapes for the dispatch client-spec registry.

Belongs to the ``_client_specs.py`` registry, which re-exports every symbol here.
Split out for the same reason the config-admission tables are: this module holds
the CONTRACT — the field set, the tri-state vocabularies, and the validation a
malformed entry trips — while its parent holds the DATA TABLE that grows once per
supported client. Kept together, the pair drifted past the module-size gate the
first time three clients were added at once.

Imports nothing from the rest of ``trw_mcp.dispatch``, so the shape stays
liftable into a shared cross-harness seam on its own (PRD-CORE-266-FR01).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trw_mcp.dispatch._child_marker import CHILD_MARKER_ENV, template_marks_child

__all__ = [
    "REVIEWER_ARGV_PLACEHOLDERS",
    "ClientSpec",
    "ClientVerification",
    "DispatchPosture",
    "OutputShape",
    "SandboxPosture",
    "SubAgentSupport",
    "UnknownClientError",
    "VerificationMethod",
]


#: How TRW established a client's capability data.
#:
#: ``executable``      — the binary was run on a box and its own output read.
#: ``primary_source``  — a dated vendor page stated it; the binary was not run.
#: ``unverified``      — neither. Dispatch refuses; see FR04.
VerificationMethod = Literal["executable", "primary_source", "unverified"]

#: The sandbox posture TRW's OWN dispatch achieves for a client — deliberately a
#: tri-state rather than a boolean (PRD-CORE-266-FR06).
#:
#: ``enforced``              — TRW emits an explicit sandbox flag on the
#:                             read-only path, so the child is sandboxed by
#:                             construction.
#: ``available_default_off`` — the client HAS a sandbox that TRW does not turn
#:                             on. An operator must not read this as protection.
#: ``none``                  — the client exposes no sandbox flag TRW can use;
#:                             read-only rests on the client denying writes.
#:
#: A boolean would render ``available_default_off`` as either a claim of
#: protection TRW does not provide, or as indistinguishable from a client that
#: has no sandbox at all. Both collapses are the failure this field exists to
#: prevent.
SandboxPosture = Literal["enforced", "available_default_off", "none"]

#: Whether the client can drive sub-agents. ``unknown`` is a RECORDED state, not
#: a ``False`` — "we have not established this" and "this client cannot" are
#: different facts and an operator planning a formation needs to tell them apart.
SubAgentSupport = Literal["yes", "no", "unknown"]

#: Which SHAPE a client's stdout has, so the output parsers can be keyed on the
#: shape instead of on a parallel client-id dict. Every parser degrades to
#: ``raw.strip()`` on a parse failure, so choosing the wrong shape costs nothing
#: and claims nothing — which is why an entry whose real output has not been
#: observed is given the shape its vendor documents (or ``trailing_text``) rather
#: than being left out of the mapping.
#:
#: ``single_json_object`` — one JSON document carrying the answer in a field.
#: ``json_lines``         — one JSON object per line; the last textual one wins.
#: ``ndjson_events``      — an NDJSON event stream to be reassembled in order.
#: ``enveloped_ndjson_events`` — NDJSON where each line is a TAGGED envelope,
#:                          ``{"event": <name>, <name>: {...}}``, and one
#:                          terminal envelope carries the whole answer. Distinct
#:                          from ``ndjson_events`` because the payload is nested
#:                          under a per-event key rather than flat, so a parser
#:                          for one cannot read the other.
#: ``trailing_text``      — legacy name for ANSI-cleaned full plain text.
OutputShape = Literal[
    "single_json_object",
    "json_lines",
    "ndjson_events",
    "enveloped_ndjson_events",
    "trailing_text",
]


#: Which POSTURE a dispatch runs under — the session-identity axis, distinct
#: from ``read_only`` (a permission axis) and from ``role`` (a prompt preamble
#: that binds nothing).
#:
#: ``default``  — the child's MCP surface is whatever its own configuration
#:                gives it. TRW makes no containment claim.
#: ``reviewer`` — the child is launched so that ITS trw-mcp server comes from
#:                TRW's own argv and runs with ``TRW_SURFACE_ROLE=reviewer``,
#:                which the server-side ``SurfaceAuthorityMiddleware`` bounds to
#:                :data:`trw_mcp.models.surface_packs.REVIEWER_TOOLS`. A
#:                ``reviewer`` request is refused before spawn for any client
#:                whose spec carries no ``reviewer_argv_template`` — the bound
#:                must be a mechanism, never a sentence in a prompt
#:                (PRD-SEC-015-FR06/FR07, OD-6).
DispatchPosture = Literal["default", "reviewer"]

#: The substitution vocabulary a ``reviewer_argv_template`` may reference.
#: Declared HERE, beside the field it constrains, so a malformed template fails
#: at import with the offending client id rather than rendering a literal
#: ``{typo}`` onto a command line. The renderer (``_posture.py``) imports this
#: set; the dependency runs one way, so this module still imports nothing from
#: the rest of the package.
#:
#: ``{reviewer_tools}`` — the reviewer allowlist as a TOML/JSON array literal.
#: ``{mcp_command}``    — absolute path to the interpreter that launches the
#:                        child's trw-mcp server (never a PATH lookup, never a
#:                        path inside the reviewed repository).
#: ``{mcp_args}``       — that server's argv tail as an array literal.
REVIEWER_ARGV_PLACEHOLDERS: frozenset[str] = frozenset({"reviewer_tools", "mcp_command", "mcp_args"})

#: A ``{bare_identifier}`` occurrence. Deliberately NARROW: a reviewer template
#: token can be a whole JSON document (claude's ``--mcp-config`` payload), and a
#: pattern that matched any brace pair would read ``{"mcpServers":{}}`` as a
#: placeholder and refuse a valid entry.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _placeholder_names(token: str) -> list[str]:
    """Every ``{name}`` placeholder referenced by one argv token."""
    return _PLACEHOLDER_RE.findall(token)


class UnknownClientError(KeyError):
    """Raised for a client id with no registry entry.

    A distinct type rather than a bare ``KeyError`` so callers can catch the
    "not registered" case without catching an unrelated dict miss. There is no
    fallback spec: answering a dispatch for an unregistered id with some default
    client's argv would answer the operator's question with a different agent.
    """


class ClientVerification(BaseModel):
    """How, from what, and when a client's capability data was established."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    method: VerificationMethod
    evidence: str = Field(
        min_length=1,
        description="The probe command and version, or the vendor URL and fetch date.",
    )
    verified_at: date = Field(description="When the evidence above was observed.")
    outstanding: str = Field(
        default="",
        description=(
            "What verification is still required. Non-empty exactly when method is "
            "'unverified' — the refusal message quotes it verbatim, so a caller is "
            "told what would settle the question rather than merely that it is open."
        ),
    )

    @model_validator(mode="after")
    def _unverified_states_what_is_outstanding(self) -> ClientVerification:
        if self.method == "unverified" and not self.outstanding.strip():
            raise ValueError("an unverified entry must state the verification still outstanding")
        if self.method != "unverified" and self.outstanding.strip():
            raise ValueError(f"outstanding is only meaningful for an unverified entry (method={self.method!r})")
        return self


class ClientSpec(BaseModel):
    """One client's dispatch policy, as data.

    Argv is assembled in field order by ``build_command``::

        base_argv always_argv structured_output_argv isolation_argv
        (read_only_argv | allow_writes_argv) [model_flag MODEL]
        [cwd_flag CWD] *extra_args (prompt_flag PROMPT | PROMPT)

    Every fragment is a tuple of literal strings, so the whole policy is
    inspectable without executing anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str
    binary: str = Field(min_length=1, description="Executable name as TRW spells it in argv.")
    binary_aliases: tuple[str, ...] = Field(
        default=(),
        description=(
            "Other documented spellings of the same executable. Consumed by the doctor "
            "readiness PATH probe, which reports WHICH name answered. build_command is a "
            "pure function and always emits ``binary`` — a PATH lookup inside the argv "
            "builder would make the same request produce different argv on different boxes."
        ),
    )
    base_argv: tuple[str, ...] = Field(description="Binary plus any subcommand, e.g. ('codex', 'exec').")
    always_argv: tuple[str, ...] = ()
    structured_output_argv: tuple[str, ...] = Field(
        default=(),
        description="Flags that make the client emit machine-readable output. Empty means it has none.",
    )
    isolation_argv: tuple[str, ...] = Field(
        default=(),
        description="Emitted when the request asks to isolate the child from host config/hooks/MCP.",
    )
    read_only_argv: tuple[str, ...] = Field(
        default=(),
        description=(
            "Emitted when read_only is True. EMPTY IS NOT A GAP: several clients deny "
            "writes headlessly by default, and for those the read-only mechanism is "
            "OMITTING allow_writes_argv rather than adding a flag."
        ),
    )
    allow_writes_argv: tuple[str, ...] = Field(
        default=(),
        description="Emitted only when read_only is False. This is the write-enabling fragment.",
    )
    confined_read_only_argv: tuple[str, ...] = Field(
        default=(),
        description=(
            "Emitted on the read-only path ONLY while a HOST write-denial wrapper is "
            "actually applied to the child (PRD-CORE-277-FR02). It exists for a client "
            "whose own headless permission layer denies READS as well as writes, so the "
            "only way to obtain a usable read-only run is to relax the client's permission "
            "prompt and let the operating system deny the writes instead. It is a "
            "permission bypass by itself, which is why the builder refuses to emit it "
            "unless the caller proves the wrapper was built: an empty tuple is the correct "
            "value for every client that can already read under read_only."
        ),
    )
    host_confinement: bool = Field(
        default=False,
        description=(
            "True iff TRW should try to wrap this client's read-only runs in a host "
            "write-denial sandbox. Declared per client rather than derived, because the "
            "wrapper changes what the child may do and that decision belongs beside the "
            "flags it pairs with."
        ),
    )
    fresh_mcp_server_table: bool = Field(
        default=False,
        description=(
            "Internal transport capability: render dotted -c MCP overrides into a fresh "
            "server table and disable the legacy entry because this client's config "
            "recursively merges inherited transport/env/tool-filter leaves. The renderer "
            "requires the controlled dotted transport contract when this flag is enabled."
        ),
    )
    reviewer_argv_template: tuple[str, ...] = Field(
        default=(),
        description=(
            "Argv fragment emitted INSTEAD of isolation_argv when the request carries "
            "posture='reviewer'. Tokens may reference the placeholders in "
            "REVIEWER_ARGV_PLACEHOLDERS, which the renderer substitutes literally (never "
            "str.format: a claude --mcp-config payload is JSON and is full of braces). "
            "EMPTY IS A REFUSAL, not a default: a client with no template has no argv "
            "channel able to carry the MCP transport, so a reviewer dispatch to it is "
            "rejected before spawn rather than downgraded to a prompt-only 'reviewer'."
        ),
    )
    trw_access_argv_template: tuple[str, ...] = Field(
        default=(),
        description=(
            "Argv fragment emitted INSTEAD of isolation_argv when the request carries "
            "with_trw=True (PRD-CORE-281-FR02): the SAME mechanism the reviewer template "
            "uses — TRW's own stdio trw-mcp server rendered into the child's argv — but "
            "with no TRW_SURFACE_ROLE marking and no enabled_tools allowlist, so the "
            "child gets an ORDINARY TRW session against this project. Tokens may "
            "reference REVIEWER_ARGV_PLACEHOLDERS. EMPTY IS A REFUSAL, not a default: a "
            "client with no argv channel for an MCP transport is rejected before spawn "
            "rather than launched with the flag silently dropped."
        ),
    )
    trw_access_config_residue: str = Field(
        default="",
        description=(
            "What this client STILL reads of the host/project configuration once "
            "``trw_access_argv_template`` has displaced ``isolation_argv`` — empty when the "
            "template preserves the same isolation the default fragment gave (PRD-CORE-281-FR04). "
            "DATA rather than a source comment, because a residue nobody can read at runtime is "
            "a residue nobody weighs: the runner logs this string on every enforced with_trw "
            "launch, so 'TRW's server is present' is never reported as 'the child is isolated'."
        ),
    )
    reviewer_env: Mapping[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment injected into the DIRECT child under posture='reviewer' (e.g. "
            "TRW_SURFACE_ROLE=reviewer). Defense in depth beside the argv template: the "
            "template marks the MCP server the child spawns, this marks the child itself, "
            "so a client that forwards its own environment to that server is bounded by "
            "either path. Applied on top of the credential allowlist, never instead of it."
        ),
    )
    model_flag: str | None = None
    cwd_flag: str | None = Field(
        default=None,
        description="Flag carrying the working directory, when the client needs it in argv.",
    )
    prompt_flag: str | None = Field(
        default=None,
        description="Flag introducing the prompt. None means the prompt is a trailing positional.",
    )
    version_argv: tuple[str, ...] = Field(
        description=(
            "Argv appended to the binary to print a version. Per-entry data rather than a "
            "hardcoded '--version' because at least one vendor exposes it as a subcommand."
        ),
    )
    forbidden_tokens: frozenset[str] = Field(
        default=frozenset(),
        description=(
            "This client's own permission-bypass flags, from its cited source. UNIONED "
            "with the shared floor, never substituted for it: a per-client set can only "
            "add restrictions."
        ),
    )
    output_shape: OutputShape = Field(
        description=(
            "The shape of this client's stdout, used to select an output parser. Keyed on "
            "the shape rather than on the client id so the parser table carries no client "
            "literal and a new client reuses an existing parser by declaring its shape."
        ),
    )
    credential_env: tuple[str, ...] = Field(
        default=(),
        description=(
            "Provider credential variables forwarded into this client's subprocess env, on "
            "top of the base allowlist. EMPTY IS THE SAFE DEFAULT and the correct value "
            "whenever no credential variable appears in the entry's cited source: a client "
            "with none recorded receives the base allowlist alone, never a wholesale "
            "environment (PRD-CORE-266-NFR03)."
        ),
    )
    instruction_files: tuple[str, ...] = ()
    profile_id: str | None = Field(
        default=None,
        description="Client-profile id, when this client has one. None means it is absent from that registry.",
    )
    sub_agents: SubAgentSupport
    sandbox: SandboxPosture
    verification: ClientVerification

    @property
    def binary_names(self) -> tuple[str, ...]:
        """Every documented spelling, most-preferred first."""
        return (self.binary, *self.binary_aliases)

    @property
    def supports_reviewer_posture(self) -> bool:
        """True iff this client can be launched under ``posture='reviewer'``.

        DERIVED from the template's presence rather than declared separately: a
        boolean a maintainer sets by hand could claim a posture the argv cannot
        deliver, which is precisely the "bounded by a sentence" failure OD-6
        exists to end.
        """
        return bool(self.reviewer_argv_template)

    @property
    def supports_trw_access(self) -> bool:
        """True iff this client can be launched with ``with_trw=True``.

        DERIVED from the template's presence for the same reason
        :attr:`supports_reviewer_posture` is: a hand-set boolean could claim a
        TRW connection the argv never delivers, and the caller would then read
        the child's "I have no TRW tools" as a server fault rather than as the
        unsupported client it is.
        """
        return bool(self.trw_access_argv_template)

    @property
    def headless_json(self) -> bool:
        """True iff this client can be asked for machine-readable output."""
        return bool(self.structured_output_argv)

    @property
    def agent_surface(self) -> bool | None:
        """Whether this client has an agent-definition surface.

        DERIVED from the ``PRD-CORE-252`` agent-format registry, never restated:
        a derived value cannot disagree with its source. ``None`` (not ``False``)
        when the client has no client profile at all — "absent from that
        registry" and "registered as having no agent surface" are different
        facts, and the readiness row reports them differently.

        The import is local to keep the registry's module-level dependency set
        empty, so this module stays liftable into a shared seam.
        """
        if self.profile_id is None:
            return None
        from trw_mcp.agents.agent_formats import agent_format_for

        return agent_format_for(self.profile_id).supports_agents

    @model_validator(mode="after")
    def _reviewer_posture_is_expressible(self) -> ClientSpec:
        """Reject a reviewer posture that could not be delivered as argv.

        Two ways an entry can claim containment it cannot perform, both fatal at
        import: a ``reviewer_env`` with no template (the marked child would spawn
        an MCP server TRW never configured), and a template naming a placeholder
        the renderer does not know (which would reach a command line verbatim).
        """
        if self.reviewer_env and not self.reviewer_argv_template:
            raise ValueError(
                f"{self.client_id!r}: reviewer_env is set but reviewer_argv_template is empty; "
                "the env alone marks the child without giving it a TRW-controlled MCP transport"
            )
        for field_name in ("reviewer_argv_template", "trw_access_argv_template"):
            for token in getattr(self, field_name):
                for name in _placeholder_names(token):
                    if name not in REVIEWER_ARGV_PLACEHOLDERS:
                        raise ValueError(
                            f"{self.client_id!r}: {field_name} references unknown placeholder "
                            f"{{{name}}}; known: {sorted(REVIEWER_ARGV_PLACEHOLDERS)}"
                        )
        # A with_trw child is an ORDINARY peer, not a bounded reviewer. Naming
        # the reviewer allowlist here would hand it the nine read-only tools
        # while the result reported an unrestricted TRW connection — the exact
        # "declared bound is not the delivered bound" inversion this module's
        # derived properties exist to prevent.
        for token in self.trw_access_argv_template:
            if "reviewer" in token:
                raise ValueError(
                    f"{self.client_id!r}: trw_access_argv_template token {token!r} references the "
                    "reviewer surface; use posture='reviewer' for a bounded lane"
                )
        # Nested-launch guard: the child's TRW server must be able to tell it
        # is a child. Only the two known transports are recognised, so a
        # template in any other shape is refused rather than assumed marked.
        if self.trw_access_argv_template and not template_marks_child(self.trw_access_argv_template):
            raise ValueError(
                f"{self.client_id!r}: trw_access_argv_template does not set {CHILD_MARKER_ENV}=1 in the "
                "rendered TRW server entry (known shapes: --mcp-config JSON env, codex -c dotted override)"
            )
        if self.trw_access_config_residue and not self.trw_access_argv_template:
            raise ValueError(
                f"{self.client_id!r}: trw_access_config_residue is set but trw_access_argv_template "
                "is empty; a residue describes what survives a with_trw launch this client can never "
                "have, so the two statements contradict each other"
            )
        return self

    @model_validator(mode="after")
    def _base_argv_starts_with_the_binary(self) -> ClientSpec:
        if not self.base_argv:
            raise ValueError(f"{self.client_id!r}: base_argv must be non-empty")
        if self.base_argv[0] != self.binary:
            raise ValueError(f"{self.client_id!r}: base_argv[0] must be the binary {self.binary!r}")
        return self


# Verification records for the four clients that predate this registry. Their
# ARGV FLAGS were established live on this box 2026-06-21 (the dates and the
# rejected alternatives are recorded in the per-entry comments below); their
# binaries and --version answers were re-confirmed 2026-09-05. verified_at is the
# older date deliberately — it is the age of the flag data, which is what a
# reader of the doctor row needs to judge, and dating it forward on the strength
