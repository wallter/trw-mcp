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

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ClientSpec",
    "ClientVerification",
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
#: ``trailing_text``      — no machine format; the trailing non-empty lines.
OutputShape = Literal["single_json_object", "json_lines", "ndjson_events", "trailing_text"]


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
