"""Pydantic v2 models for the cross-client dispatch layer.

Belongs to the ``trw_mcp.dispatch`` package. ``DispatchRequest`` is the typed
input contract for launching another coding-agent CLI headlessly;
``DispatchResult`` is the normalized, redacted output contract returned to a
shell-capable agent (e.g. Claude Code) for second-opinion audits.

The prompt body is never logged or surfaced raw in :attr:`DispatchResult.argv_redacted`
— it is replaced with a ``<prompt:NN chars>`` placeholder so transcripts and
artifacts cannot leak the audit instructions or any embedded context.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from trw_mcp.dispatch._client_spec_types import DispatchEffort as DispatchEffort

# ``DispatchClient`` and ``SUPPORTED_CLIENTS`` are DEFINED in ``_client_specs``,
# next to the registry entries they enumerate, and RE-EXPORTED here (``X as X``)
# so every existing importer — config fields, env allowlist, normalizer, runner —
# keeps its import site unchanged. Two hand-maintained lists that had to be kept
# "in lock-step" by comment are now one list plus one derivation, and the registry
# asserts their equality at import (PRD-CORE-266-FR01).
from trw_mcp.dispatch._client_specs import SUPPORTED_CLIENTS as SUPPORTED_CLIENTS
from trw_mcp.dispatch._client_specs import DispatchClient as DispatchClient
from trw_mcp.dispatch._client_specs import DispatchPosture as DispatchPosture
from trw_mcp.dispatch._client_specs import UnknownClientError, client_spec_for

# Upper bound on a forwarded model-override string. A model name is concatenated
# into argv; even within the benign charset an unbounded value is pointless and a
# resource/abuse vector, so cap it well above any real model id.
_MAX_MODEL_NAME_LEN = 256

# Security flags a caller must NOT be able to slip in through ``extra_args`` —
# they would override the dispatch layer's isolation / read-only / permission
# posture (e.g. re-enable writes, disable MCP isolation, point the child at a
# different config). ``extra_args`` is an API-only escape hatch for benign extra
# tokens; the CLI deliberately exposes no ``--extra-args`` surface.
#
# Provenance (PRD-LOCAL-074-FR07/FR09). The trailing 10 entries were added on
# 2026-09-04 (8) and 2026-09-05 (2);
# each carries the CLI and version whose ``--help`` output was read that day, so a
# later maintainer can re-verify rather than guess. ``trw_loop._argv_floor`` mirrors
# this set and the loop runtime's argv-floor parity test asserts the superset
# relation from source, so the two cannot drift silently.
_FORBIDDEN_EXTRA_ARG_TOKENS: frozenset[str] = frozenset(
    {
        "--setting-sources",
        "--sandbox",
        "--mcp-config",
        "--strict-mcp-config",
        "--permission-mode",
        "--permission-prompt-tool",
        "--dangerously-skip-permissions",
        "--no-isolate",
        "--bare",
        "--ignore-user-config",
        "--yes",
        "--yes-always",
        # codex: present in codex-cli 0.153.2 ``codex exec --help``.
        "--dangerously-bypass-approvals-and-sandbox",
        # codex: present in codex-cli 0.153.2 ``codex exec --help``.
        "--dangerously-bypass-hook-trust",
        # codex: present in codex-cli 0.153.2 ``codex exec --help`` — routes
        # approvals through automatic review under workspace-write.
        "--approve-for-me",
        # codex: present in codex-cli 0.153.2 top-level ``codex --help`` as
        # ``-a, --ask-for-approval``. Blocked defensively: the builders emit
        # ``<binary> exec ...``, where the token is not reachable, but a plugin
        # builder may not use the subcommand.
        "--ask-for-approval",
        # codex: LEGACY-UNVERIFIED. ABSENT from both ``codex --help`` and ``codex
        # exec --help`` in codex-cli 0.153.2; carried as a legacy/forward spelling
        # for an older or newer binary on PATH. Never assert it is PRESENT in a
        # CLI's help output.
        "--full-auto",
        # opencode: present in opencode 1.18.28 ``opencode run --help``,
        # documented there as auto-approve and dangerous.
        "--auto",
        # claude: present in Claude Code 2.1.261 ``claude --help``.
        "--allow-dangerously-skip-permissions",
        # claude: present in Claude Code 2.1.261 ``claude --help`` — selects who
        # answers permission prompts.
        "--permission-prompts",
        # claude: BOTH spellings are live in Claude Code 2.1.261 ``claude --help``,
        # measured 2026-09-05. Pre-authorises tool use without a human prompt, which
        # defeats the "a headless child cannot approve" mechanism the read-only
        # posture rests on -- so it is a permission bypass, not a convenience flag.
        "--allowed-tools",
        "--allowedTools",
        # claude: present in Claude Code 2.1.280 ``claude --help``, measured
        # 2026-09-24 (PRD-SEC-015-FR10). TRW itself emits this to RESTRICT the
        # reviewer posture's built-in tool set (reviewer_extra_argv); a caller
        # smuggling a second --tools through extra_args could widen it back
        # (claude/argparse-style CLIs take the LAST repeated flag), so it is
        # blocked from the caller-controlled surface the same way --tools's
        # emission from the registry itself is not.
        "--tools",
        # codex: present in codex-cli 0.156.0 top-level and ``codex exec --help``,
        # measured 2026-09-24 (PRD-SEC-015-FR10). TRW itself emits `--disable apps`
        # to bound the reviewer posture's host tool surface (reviewer_extra_argv);
        # both --enable and --disable are blocked here so a caller cannot append
        # `--enable apps` (or disable something the posture depends on) through
        # extra_args and countermand it — `-c features.<name>=...` is the
        # equivalent config-override form and is already blocked by codex's own
        # ``forbidden_tokens`` entry for ``-c``/``--config`` (unioned with this floor).
        "--disable",
        "--enable",
    }
)

# A model override is forwarded verbatim into argv; constrain it to a benign
# charset so it cannot smuggle a second flag or shell metacharacters.
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9._:@/-]+")


def effective_forbidden_tokens(client: str) -> frozenset[str]:
    """The forbidden-token set that applies to *client*: the shared floor UNION
    that client's own tokens (PRD-CORE-266-FR05).

    A union, never a substitution. A per-client set can only ADD restrictions —
    the floor stays a subset for every client, so no registry entry, present or
    future, can reopen the read-only posture the floor establishes. Duplicate
    declarations collapse silently because a set union cannot double-count.

    An unregistered client id yields the floor alone, which is the fail-closed
    direction: an id TRW does not know is held to the shared minimum rather than
    escaping validation entirely.
    """
    try:
        own = client_spec_for(client).forbidden_tokens
    except UnknownClientError:
        return _FORBIDDEN_EXTRA_ARG_TOKENS
    return _FORBIDDEN_EXTRA_ARG_TOKENS | own


def _is_forbidden_security_token(tok: str) -> bool:
    """True if *tok* (or the flag head of a ``--flag=value`` form) is a blocked
    security-posture override.

    Shared by the ``extra_args`` and ``model`` validators so both surfaces reject
    the SAME set of isolation/read-only/permission-bypass flags with identical
    logic (split on ``=`` and match the flag portion).
    """
    return tok.split("=", 1)[0] in _FORBIDDEN_EXTRA_ARG_TOKENS


class DispatchRequest(BaseModel):
    """Typed request to run another coding-agent CLI headlessly.

    Frozen so a request cannot mutate after validation — the same object can be
    safely passed to command-building, env-building, and the runner.
    """

    model_config = ConfigDict(frozen=True)

    client: DispatchClient = Field(description="Which coding-agent CLI to launch.")
    prompt: str = Field(min_length=1, description="The prompt/instruction body for the child agent.")
    model: str | None = Field(default=None, description="Optional model override passed to the client.")
    effort: DispatchEffort | None = Field(
        default=None,
        description=(
            "Portable reasoning-effort intent. The resolver fills it from the role's operator "
            "default; build_command maps it onto the client's own flag (clamping to what the "
            "client accepts) and omits it for a client with no documented flag."
        ),
    )
    effort_source: str = Field(
        default="none",
        description="Which precedence tier chose effort: request, config, table or none (PRD-CORE-290-FR03).",
    )
    model_source: str = Field(
        default="none",
        description="Which precedence tier chose model: request, config, table, unsupported or none.",
    )
    max_turns: int | None = Field(
        default=None,
        description="Turn cap passed through the client's verified flag; None applies none (PRD-CORE-290-FR04).",
    )
    max_turns_source: str = Field(default="none", description="default, config, disabled or none.")
    cwd: Path | None = Field(default=None, description="Working directory for the child process.")
    timeout_s: int = Field(default=600, gt=0, description="Hard wall-clock timeout in seconds.")
    read_only: bool = Field(
        default=True,
        description=(
            "Run the child agent with writes forbidden. The mechanism is OMITTING any "
            "write/permission-bypass flag (a headless child cannot approve writes), plus "
            "an explicit sandbox where the client supports one: codex --sandbox read-only "
            "and agy --sandbox; claude/opencode deny writes by default without a bypass. "
            "Set False (--allow-writes) to ACTUALLY enable writes: codex --sandbox "
            "workspace-write, claude --permission-mode acceptEdits, agy "
            "--dangerously-skip-permissions, opencode --auto."
        ),
    )
    isolate: bool = Field(
        default=True,
        description="Isolate the child from the host project's config/hooks/MCP (e.g. claude --bare, codex --ignore-user-config).",
    )
    posture: DispatchPosture = Field(
        default="default",
        description=(
            "Session IDENTITY of the child, distinct from read_only (permission) and role "
            "(a prompt preamble). 'reviewer' launches the child with TRW's OWN trw-mcp server "
            "in its argv, marked TRW_SURFACE_ROLE=reviewer, so the server bounds it to "
            "REVIEWER_TOOLS server-side. Refused before spawn for a client whose spec carries no "
            "reviewer argv template, and refused with writes — see the model validator."
        ),
    )
    with_trw: bool = Field(
        default=False,
        description=(
            "Give the child its own stdio trw-mcp connection to THIS project, by rendering only "
            "TRW's server into its argv (PRD-CORE-281-FR02). Isolation is client-specific: "
            "Codex still loads user/project config and other MCP servers; a client with no "
            "argv channel for an MCP transport is REFUSED "
            "rather than launched with the flag dropped. Mutually exclusive with "
            "posture='reviewer', which already injects the server under a bounded role."
        ),
    )
    use_pty: bool = Field(
        default=False,
        description="Opt-in pseudo-TTY wrapper (script) for clients that drop stdout in non-TTY contexts (agy bug #76).",
    )
    extra_args: tuple[str, ...] = Field(
        default=(),
        description=(
            "Additional raw argv tokens appended to the command. A TUPLE, not a list, and "
            "that is a security property rather than a style choice (PRD-CORE-277-FR10): "
            "``model_config`` freezes the MODEL, not a mutable field VALUE, so with a list "
            "a caller could construct a validated read-only request and then "
            "``req.extra_args.append('--dangerously-skip-permissions')`` -- measured "
            "2026-09-16, that produced `codex exec ... --sandbox read-only "
            "--dangerously-skip-permissions <prompt>`, because the validators run at "
            "construction and the runner re-checks only reviewer posture. An immutable "
            "value closes the window everywhere at once instead of adding a second check a "
            "third launch path could skip."
        ),
    )
    verify_sandbox: bool = Field(
        default=False,
        description=(
            "Run a live write-containment probe before this dispatch and report the verdict "
            "in ``DispatchResult.sandbox_verified``. OFF by default because it costs a "
            "second model call; refused on the background job path, whose watchdog budgets "
            "one child (PRD-CORE-277-FR03). BUDGET: ``timeout_s`` bounds EACH child, so a "
            "verified run can take up to ``timeout_s`` plus the probe's own bounded budget "
            "(``min(timeout_s, 180)``) in wall clock, and ``duration_s`` reports the task "
            "child alone."
        ),
    )

    @field_validator("extra_args")
    @classmethod
    def _reject_security_override_tokens(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ``extra_args`` tokens that would override the security posture.

        ``extra_args`` is a convenience for benign extra CLI flags; it must never
        become a back door that re-enables writes, disables isolation, or points
        the child at a different config/MCP. A ``--flag=value`` form is checked on
        the flag portion so ``--sandbox=workspace-write`` is also blocked.
        """
        for tok in value:
            if _is_forbidden_security_token(tok):
                raise ValueError(f"extra_args may not override security flag: {tok!r}")
        return value

    @model_validator(mode="after")
    def _reject_client_specific_bypass_tokens(self) -> DispatchRequest:
        """Reject ``extra_args`` tokens this CLIENT documents as a bypass.

        A model-level validator rather than a field validator because the check
        needs BOTH values: the field validator above cannot see ``client`` and so
        can only enforce the shared floor. Without this pass, copilot's
        ``--yolo`` and cursor-cli's ``--force`` would be legal ``extra_args``
        tokens — flags whose own vendors document them as enabling every
        permission — because the floor predates both clients.

        The same ``security flag`` wording and the same ``=`` head split as the
        floor check, so callers and tests observe one error path.

        A registered two-character flag ``-X`` is also refused in its ATTACHED
        spelling (``-Xvalue``): argument parsers accept a short option's value
        glued to it, so ``-sdanger-full-access`` means ``-s danger-full-access``
        and a head split on ``=`` alone never sees it (REPAIR-DESIGN-01). The rule
        is generic over the registry rather than a codex special case, so the
        reported floor (``effective_forbidden_tokens``) stays the enforced floor.
        """
        own = effective_forbidden_tokens(self.client) - _FORBIDDEN_EXTRA_ARG_TOKENS
        short = {tok for tok in own if len(tok) == 2 and tok[0] == "-" and tok[1] != "-"}
        for tok in self.extra_args:
            attached = not tok.startswith("--") and tok[:2] in short
            if tok.split("=", 1)[0] in own or attached:
                raise ValueError(f"extra_args may not override security flag: {tok!r} (client {self.client!r})")
        return self

    @model_validator(mode="after")
    def _reviewer_posture_is_read_only(self) -> DispatchRequest:
        """Refuse ``posture='reviewer'`` together with writes, at construction.

        The earliest possible refusal on purpose: every launch path (CLI, MCP
        tool, synchronous runner, background job, the detached ``_run_job``
        child) builds this model first, so a writable reviewer cannot exist as
        an object, let alone as a command line. Placing the check only in the
        resolver would leave the API and the job re-hydration path open.

        The reviewer TOOL bound is read-only by construction (a small read-report
        set); a child that could still edit the work it reviews would make
        ``posture='reviewer'`` a claim about the MCP surface and nothing else.
        """
        if self.posture == "reviewer" and not self.read_only:
            raise ValueError(
                "posture='reviewer' requires read_only=True (a reviewer may not modify the work it reviews)"
            )
        return self

    @model_validator(mode="after")
    def _trw_access_is_not_a_second_mcp_source(self) -> DispatchRequest:
        """Refuse ``with_trw`` together with ``posture='reviewer'``.

        Both render an MCP server into the SAME argv slot, and the builder emits
        exactly one. Silently preferring either would make the other a setting
        the caller asked for and did not get — and in the reviewer direction that
        is a bound the caller believes is present. Refused at construction, so
        no launch path (CLI, MCP tool, background re-hydration) can build it.
        """
        if self.with_trw and self.posture == "reviewer":
            raise ValueError(
                "with_trw cannot be combined with posture='reviewer': the reviewer posture already "
                "injects TRW's MCP server, bounded to the read-only reviewer surface. Choose one."
            )
        if self.with_trw and self.posture == "isolated-review":
            raise ValueError(
                "with_trw cannot be combined with posture='isolated-review': the lane admits a child only "
                "after proving it has no MCP server, and with_trw would add one."
            )
        return self

    @field_validator("model")
    @classmethod
    def _validate_model_name(cls, value: str | None) -> str | None:
        """Constrain a model override to a benign charset AND block flag smuggling.

        The model value is emitted verbatim as the argv token after ``--model``.
        A charset allowlist alone is insufficient: a value like
        ``--dangerously-skip-permissions`` or ``--read-only`` is charset-clean
        yet, positioned in argv, smuggles exactly the security-posture flags that
        ``extra_args`` blocks. So, in addition to the charset + length checks, we
        run the SAME forbidden-token check ``extra_args`` uses (including the
        ``--flag=value`` head split) and reject any value beginning with ``-`` —
        legitimate model ids (``claude-opus-4-8``, ``anthropic/claude-sonnet-5``,
        ``gpt-5.6``, ``qwen3:32b``) never start with ``-`` so they stay valid.
        """
        if value is None:
            return None
        if not _MODEL_NAME_RE.fullmatch(value):
            raise ValueError(f"invalid model name: {value!r}")
        if len(value) > _MAX_MODEL_NAME_LEN:
            raise ValueError(f"model name too long: {len(value)} chars (max {_MAX_MODEL_NAME_LEN})")
        if _is_forbidden_security_token(value):
            raise ValueError(f"model may not override security flag: {value!r}")
        if value.startswith("-"):
            raise ValueError(f"model may not start with '-' (flag smuggling): {value!r}")
        return value


class DispatchAttempt(BaseModel):
    """One client tried by the fallback chain (``_fallback.dispatch_with_fallback``)."""

    model_config = ConfigDict(frozen=True)

    client: str
    reason: str | None = Field(
        description=(
            "The attempt's silence_reason, 'launch_failed', 'unresolved', 'posture_unsupported' "
            "(skipped: the client cannot run the request's posture), or None when it answered."
        )
    )


class DispatchResult(BaseModel):
    """Normalized result of a dispatch run.

    Frozen — a result is an immutable record of what happened. The prompt is
    never present raw: :attr:`argv_redacted` carries the command with the prompt
    body replaced by a ``<prompt:NN chars>`` placeholder.
    """

    model_config = ConfigDict(frozen=True)

    client: DispatchClient
    argv_redacted: list[str] = Field(description="The launched command with the prompt body redacted.")
    read_only_enforced: bool = Field(
        description=(
            "True iff the child was launched with writes forbidden (mirrors the "
            "request's ``read_only``). The mechanism is registry-derived and stated "
            "as a mechanism rather than a client count: True means the builder "
            "emitted that client's ``read_only_argv`` and OMITTED its "
            "``allow_writes_argv``, so no write/permission-bypass flag reached the "
            "child. It holds for every registered client by construction — a count "
            "here went stale the moment the registry grew past four."
        ),
    )
    posture: DispatchPosture = Field(
        default="default",
        description="The posture the request ASKED for. Compare with posture_enforced before trusting it.",
    )
    posture_enforced: bool = Field(
        default=False,
        description=(
            "True iff a reviewer posture was requested AND this client's spec carried a reviewer "
            "argv template that was rendered into the launched command — i.e. the child's MCP "
            "server came from TRW's argv and is marked TRW_SURFACE_ROLE=reviewer. Derived per run "
            "from the registry, never hardcoded: 'posture' alone records an intention, and an "
            "intention beside an argv with no MCP override is exactly the delivered-but-not-wired "
            "claim this field exists to expose. False on every default-posture run."
        ),
    )
    trw_access_enforced: bool = Field(
        default=False,
        description=(
            "True iff with_trw was requested AND this client's spec carried an argv template that "
            "was rendered into the launched command — i.e. the child really did get a trw-mcp "
            "connection to this project. Derived per run from the registry, never hardcoded: a "
            "request flag alone records an intention, and an intention beside an argv with no MCP "
            "entry is the delivered-but-not-wired claim this field exists to expose."
        ),
    )
    exit_code: int | None = Field(description="Child process exit code; None if it timed out before exiting.")
    timed_out: bool = Field(description="True if the child exceeded timeout_s and was killed.")
    duration_s: float = Field(description="Wall-clock duration of the child process in seconds.")
    text: str = Field(description="The normalized final answer extracted from the client's output.")
    raw_stdout: str = Field(description="Unmodified child stdout.")
    raw_stderr: str = Field(description="Unmodified child stderr.")
    structured: dict[str, object] | None = Field(
        default=None,
        description="Parsed structured payload when the client emitted JSON/NDJSON; None otherwise.",
    )
    sandbox_verified: bool | Literal["unverified"] = Field(
        default="unverified",
        description=(
            "Whether a LIVE probe established that this client, argv and platform could not "
            "write to the filesystem on the read-only path. True and False are measurements "
            "from a disposable fixture; 'unverified' -- the default -- means no claim is "
            "being made, which is the honest value for every run that did not ask for the "
            "probe. Deliberately NOT derived from the registry's ``sandbox`` field: that "
            "field records what a client's flags are documented to do, and republishing it "
            "per run is how ``sandbox=enforced`` came to stand beside agy runs that wrote "
            "files (PRD-CORE-277-FR03)."
        ),
    )
    sandbox_note: str = Field(
        default="",
        description=(
            "What the sandbox verdict does and does not cover: the mechanism used, or why "
            "none was available. Scope is local filesystem writes only -- never network "
            "egress, never a tool the child reached through its own MCP servers."
        ),
    )
    next_read: str = Field(
        default="",
        description="Where the rest of the work is when the run stopped incomplete (a turn-cap hit); else empty.",
    )
    isolation: Literal["none", "snapshot-write-confined"] = Field(
        default="none",
        description=(
            "Where the child ran: the caller's tree, or a snapshot of it with writes confined. "
            "Reads are NOT confined: the child can read caller files and HOME secrets by absolute path."
        ),
    )
    contamination: Literal["not-checked", "clean", "contaminated"] = Field(
        default="not-checked",
        description="Whether the snapshot or the caller's git state changed during the run; contaminated is never ok.",
    )
    changed_paths: list[str] = Field(default_factory=list, description="What changed, when contaminated.")
    silence_reason: str | None = Field(
        default=None,
        description=(
            "Why this run produced no usable answer: 'timed_out', 'turn_cap_reached', 'auth_or_content_stop', "
            "'subagent_deferral' (the client's own status field claims success while its answer text only "
            "announces a handoff to a subagent -- the work was never actually returned), "
            "'quota_exhausted' (the provider refused on usage or billing; fail over to another client), "
            "'sandbox_unsupported' or 'client_unsupported' (the installed CLI lacks a flag dispatch passes; "
            "nothing was run), 'nonzero_exit', 'empty_output', or None when the answer is usable. Exists "
            "because a caller that sees only empty findings cannot tell a clean review from "
            "a child that never ran (PRD-CORE-277-FR04)."
        ),
    )

    attempts: list[DispatchAttempt] = Field(
        default_factory=list,
        description=(
            "Every client the fallback chain tried, in order; empty when no chain was configured. "
            "``client`` above is the one whose result this is."
        ),
    )
    fallback_note: str = Field(
        default="",
        description="Why another client answered, or that every client in the chain failed over; else empty.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """True iff the run completed cleanly with a non-empty answer.

        A run is successful only when it did not time out, the child exited
        zero, we extracted a non-empty normalized answer, and no silence reason
        was recorded — an empty answer from a zero exit (e.g. agy's non-TTY
        stdout drop) is not a success, and neither is a zero-exit run whose
        structured payload reports an error status.

        ``silence_reason is None`` is the load-bearing addition: the other three
        conditions already described "an answer arrived", and the fourth covers
        the case where an answer arrived that the CLIENT itself marked as a stop.

        Exposed as a ``computed_field`` so it is included in ``model_dump_json``
        for the ``--output-file`` / ``--json`` CLI surfaces.
        """
        clean = self.silence_reason is None and self.contamination != "contaminated"
        return not self.timed_out and self.exit_code == 0 and bool(self.text.strip()) and clean
