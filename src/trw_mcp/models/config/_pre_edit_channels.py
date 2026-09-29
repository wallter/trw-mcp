"""How each client profile receives the pre-edit hint (PRD-CORE-336-FR04).

One row per profile in the registry (``models/config/_profiles.py``). A profile
is either:

- ``hook``: a bundled pre-edit hook prints the hint into a field the client's
  own hook documentation says reaches the model. ``output_path`` pins that field
  and the per-client contract test asserts the hook prints exactly that shape.
- ``instruction_fallback``: the client documents no such field. Its instructions
  tell the agent to call ``trw_code(mode="hint")`` before editing
  (:data:`PRE_EDIT_HINT_INSTRUCTION`), and ``trw-mcp doctor`` names it in the
  ``hook_channel`` row.

A channel counts as wired only when the vendor documents it as model-visible on
an ALLOW. A field documented for denials only, or one that can be set only by
also auto-approving the tool call, is not a pre-edit hint channel. Each
``doc_url`` is the primary source the decision was read from (2026-09-26).

``live_check`` records an observed run of the real client (date, client
version, the text the model received). ``None`` means the check is pending: the
contract test proves the shape, not that the vendor renders it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "PRE_EDIT_HINT_CHANNELS",
    "PRE_EDIT_HINT_INSTRUCTION",
    "PreEditHintChannel",
    "fallback_clients",
    "render_pre_edit_hint_instruction",
    "unclassified_profiles",
]

Delivery = Literal["hook", "instruction_fallback"]

#: The CC-03 hook. Claude Code runs it from ``.claude/settings.json``; Codex runs
#: the same installed file from ``.codex/hooks.json`` (matcher ``apply_patch``).
_CC03_HOOK = "claude_code/hooks/pre-tool-distill-hint.sh"
_ADDITIONAL_CONTEXT = ("hookSpecificOutput", "additionalContext")

#: The instruction line every fallback client's carrier renders. Conditional, so
#: a shared carrier (AGENTS.md) read by a wired client stays correct there too.
PRE_EDIT_HINT_INSTRUCTION = (
    "- **Before editing a file**: if no `[TRW]` pre-edit hint for it is already in your context, "
    'call `trw_code(mode="hint", files=["<path>"])` first.\n'
)

#: 2026-09-27 audit (touchpoint #4): no deployed instruction surface told an
#: agent that `trw-distill query|rca` exist, even on a project where the
#: sidecar and lessons already build automatically. One line, appended only
#: when the proprietary package is installed (never claimed otherwise).
_DISTILL_CLI_INSTRUCTION = (
    "- **A failing test or an unfamiliar file**: if `trw-distill` is installed, "
    "`trw-distill query deps <path>` and `trw-distill rca trace <test>` answer "
    "'what does this touch' and 'why did this fail' from the codebase map — see "
    "`trw-distill query --help` / `trw-distill rca --help`.\n"
)


@dataclass(frozen=True, slots=True)
class PreEditHintChannel:
    """One client's pre-edit hint delivery decision."""

    client_id: str
    delivery: Delivery
    channel: str
    doc_url: str
    hook: str | None = None
    output_path: tuple[str, ...] = ()
    live_check: str | None = None


PRE_EDIT_HINT_CHANNELS: dict[str, PreEditHintChannel] = {
    entry.client_id: entry
    for entry in (
        PreEditHintChannel(
            client_id="claude-code",
            delivery="hook",
            channel="PreToolUse hookSpecificOutput.additionalContext",
            doc_url="https://code.claude.com/docs/en/hooks",
            hook=_CC03_HOOK,
            output_path=_ADDITIONAL_CONTEXT,
            live_check="2026-09-26, Claude Code 2.1.283: the model quoted the [TRW] beacon line",
        ),
        PreEditHintChannel(
            client_id="codex",
            delivery="hook",
            channel="PreToolUse hookSpecificOutput.additionalContext (added as developer context)",
            doc_url="https://developers.openai.com/codex/hooks",
            hook=_CC03_HOOK,
            output_path=_ADDITIONAL_CONTEXT,
            live_check="2026-09-26, codex-cli 0.157.1: the model quoted the [TRW] beacon line after apply_patch",
        ),
        PreEditHintChannel(
            client_id="cursor-ide",
            delivery="instruction_fallback",
            channel=(
                "preToolUse agent_message is documented as sent to the agent only when the action is denied; "
                "the bundled allow-path hook stays installed pending a live check"
            ),
            doc_url="https://cursor.com/docs/agent/hooks",
        ),
        PreEditHintChannel(
            client_id="cursor-cli",
            delivery="instruction_fallback",
            channel="the CLI hook subset registers no preToolUse, and agent_message is deny-only",
            doc_url="https://cursor.com/docs/agent/hooks",
        ),
        PreEditHintChannel(
            client_id="copilot",
            delivery="instruction_fallback",
            channel=(
                "preToolUse command hooks document no additionalContext; permissionDecisionReason is "
                "'required when deny' (CLI reference) and 'shown to user (for deny/ask)' (SDK reference)"
            ),
            doc_url="https://docs.github.com/en/copilot/reference/hooks-configuration",
        ),
        PreEditHintChannel(
            client_id="opencode",
            delivery="instruction_fallback",
            channel="tool.execute.before can only rewrite arguments or throw (which blocks the tool)",
            doc_url="https://opencode.ai/docs/plugins/",
        ),
        PreEditHintChannel(
            client_id="antigravity-cli",
            delivery="instruction_fallback",
            channel="PreToolUse reason needs a decision, and decision=allow auto-approves the tool call",
            doc_url="https://antigravity.google/docs/hooks",
        ),
        PreEditHintChannel(
            client_id="grok",
            delivery="instruction_fallback",
            channel="PreToolUse stdout carries only decision/reason for a deny",
            doc_url="https://docs.x.ai/build/features/hooks",
        ),
    )
}


def unclassified_profiles(client_ids: Iterable[str]) -> tuple[str, ...]:
    """The ids in *client_ids* with no pre-edit decision, in the order given."""
    return tuple(client_id for client_id in client_ids if client_id not in PRE_EDIT_HINT_CHANNELS)


def fallback_clients(client_ids: Iterable[str]) -> tuple[str, ...]:
    """The ids in *client_ids* that fall back to the instruction line."""
    return tuple(
        client_id
        for client_id in client_ids
        if client_id in PRE_EDIT_HINT_CHANNELS and PRE_EDIT_HINT_CHANNELS[client_id].delivery == "instruction_fallback"
    )


def render_pre_edit_hint_instruction() -> str:
    """The fallback instruction line, rendered into every client carrier.

    2026-09-27 audit (touchpoint #4): appends one distill-discoverability line
    when the proprietary package is installed. Uses ``distill_installed()``'s
    existing ``importlib.util.find_spec`` probe (never a ``trw_distill``
    import — this package's IP boundary).
    """
    try:
        from trw_mcp.tools._sidecar_substrate import distill_installed

        if distill_installed():
            return PRE_EDIT_HINT_INSTRUCTION + _DISTILL_CLI_INSTRUCTION
    except Exception:  # trw-fail-silent-allow: an instruction line must never break instruction rendering
        pass
    return PRE_EDIT_HINT_INSTRUCTION
