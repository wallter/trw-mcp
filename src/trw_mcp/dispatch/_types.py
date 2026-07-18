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

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# The set of coding-agent CLIs the dispatch layer can launch. Gemini CLI was
# EOL'd 2026-06-18 and is intentionally absent — callers asking for it are
# redirected to ``agy`` (Antigravity CLI). Keep this in lock-step with
# ``SUPPORTED_CLIENTS`` in ``_commands.py``.
DispatchClient = Literal["claude", "codex", "agy", "opencode"]

# Single source of truth for the supported client ids — the runtime tuple form of
# the ``DispatchClient`` Literal. Lives here (next to the Literal) so config-field
# defaults, the command builder, and the env allowlist all derive their allowed
# set from ONE place; ``_commands.py`` re-exports it for back-compat. Keep this in
# lock-step with the ``DispatchClient`` Literal above.
SUPPORTED_CLIENTS: tuple[DispatchClient, ...] = ("claude", "codex", "agy", "opencode")

# Upper bound on a forwarded model-override string. A model name is concatenated
# into argv; even within the benign charset an unbounded value is pointless and a
# resource/abuse vector, so cap it well above any real model id.
_MAX_MODEL_NAME_LEN = 256

# Security flags a caller must NOT be able to slip in through ``extra_args`` —
# they would override the dispatch layer's isolation / read-only / permission
# posture (e.g. re-enable writes, disable MCP isolation, point the child at a
# different config). ``extra_args`` is an API-only escape hatch for benign extra
# tokens; the CLI deliberately exposes no ``--extra-args`` surface.
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
    }
)

# A model override is forwarded verbatim into argv; constrain it to a benign
# charset so it cannot smuggle a second flag or shell metacharacters.
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9._:@/-]+")


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
            "request's ``read_only``). True ⇒ no write/permission-bypass flag was "
            "passed for any of the four clients, so writes were impossible."
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
