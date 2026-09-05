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

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

# ``DispatchClient`` and ``SUPPORTED_CLIENTS`` are DEFINED in ``_client_specs``,
# next to the registry entries they enumerate, and RE-EXPORTED here (``X as X``)
# so every existing importer — config fields, env allowlist, normalizer, runner —
# keeps its import site unchanged. Two hand-maintained lists that had to be kept
# "in lock-step" by comment are now one list plus one derivation, and the registry
# asserts their equality at import (PRD-CORE-266-FR01).
from trw_mcp.dispatch._client_specs import SUPPORTED_CLIENTS as SUPPORTED_CLIENTS
from trw_mcp.dispatch._client_specs import DispatchClient as DispatchClient
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
            "workspace-write, claude --permission-mode acceptEdits, agy/opencode "
            "--dangerously-skip-permissions. Enforced uniformly for all four clients."
        ),
    )
    isolate: bool = Field(
        default=True,
        description="Isolate the child from the host project's config/hooks/MCP (e.g. claude --bare, codex --ignore-user-config).",
    )
    use_pty: bool = Field(
        default=False,
        description="Opt-in pseudo-TTY wrapper (script) for clients that drop stdout in non-TTY contexts (agy bug #76).",
    )
    extra_args: list[str] = Field(
        default_factory=list, description="Additional raw argv tokens appended to the command."
    )

    @field_validator("extra_args")
    @classmethod
    def _reject_security_override_tokens(cls, value: list[str]) -> list[str]:
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
        """
        own = effective_forbidden_tokens(self.client) - _FORBIDDEN_EXTRA_ARG_TOKENS
        for tok in self.extra_args:
            if tok.split("=", 1)[0] in own:
                raise ValueError(f"extra_args may not override security flag: {tok!r} (client {self.client!r})")
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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        """True iff the run completed cleanly with a non-empty answer.

        A run is successful only when it did not time out, the child exited
        zero, and we extracted a non-empty normalized answer — an empty answer
        from a zero exit (e.g. agy's non-TTY stdout drop) is not a success.

        Exposed as a ``computed_field`` so it is included in ``model_dump_json``
        for the ``--output-file`` / ``--json`` CLI surfaces.
        """
        return not self.timed_out and self.exit_code == 0 and bool(self.text.strip())
