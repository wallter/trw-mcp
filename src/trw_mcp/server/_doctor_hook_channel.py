"""``hook_channel`` doctor check (PRD-CORE-336-FR04).

Belongs to the ``_subcommands_doctor.py`` catalogue (registered in
``_doctor_checks_registry.py``). Reports, for the clients this project selected,
which ones receive the pre-edit hint from a hook and which fall back to the
instruction line that tells the agent to call ``trw_code(mode="hint")``.

Verdicts: PASS when every selected client is hook-wired, the hook is on
(``cc03_hook_enabled``), AND the registration is actually present on disk;
FAIL when the flag says on but the registration or the hook script itself is
missing (a false PASS would otherwise report hints as delivered when they are
not -- release-window fix, 2026-09-27); WARN naming the hook-wired clients when
the flag is off, and the fallback clients (and any client with no recorded
decision) otherwise; WARN when the selection cannot be read.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from trw_mcp.models.config._pre_edit_channels import (
    PRE_EDIT_HINT_CHANNELS,
    fallback_clients,
    unclassified_profiles,
)

if TYPE_CHECKING:
    from trw_mcp.server._subcommands_doctor import CheckResult

__all__ = ["check_hook_channel", "hook_channel_row"]

Status = Literal["PASS", "FAIL", "WARN"]

#: The remedy every unwired-CC03 verdict below names, so an operator (or a
#: caller reading only the string) knows the exact command and flag.
_REMEDY = "Remedy: trw-mcp update-project (flag: cc03_hook_enabled in .trw/config.yaml)."


def _reprovision_remedy(target: Path) -> str:
    """The remedy for a deleted CC-03 script: ``update-project`` keeps a deleted managed file deleted (a tombstone).

    Only ``--reprovision <path>`` brings back a tombstoned script, so the remedy names the tombstoned ones; a script
    that was never recorded is written by a plain ``update-project``.
    """
    from trw_mcp.bootstrap._claude_code_distill_channels import _CC03_HOOKS
    from trw_mcp.bootstrap._tombstones import detect_tombstones
    from trw_mcp.bootstrap._version_manifest import _manifest_key_path, _read_manifest

    tombstoned = {_manifest_key_path(key) for key in detect_tombstones(target, _read_manifest(target))}
    paths = [f".claude/hooks/{name}" for name in _CC03_HOOKS if f".claude/hooks/{name}" in tombstoned]
    if not paths:
        return _REMEDY
    flags = " ".join(f"--reprovision {path}" for path in paths)  # the flag takes one path each time it is given
    return f"Remedy: trw-mcp update-project {flags} (flag: cc03_hook_enabled in .trw/config.yaml)."


def _cc03_unwired_reason(target: Path, wired: list[str]) -> str | None:
    """Why the CC-03 hook is NOT actually wired for *wired*, or ``None`` when it is.

    Verifies the registration on disk (never trusts the flag alone): the
    ``.claude/settings.json`` PreToolUse entry (claude-code), the
    ``.codex/hooks.json`` PreToolUse group (codex), and the hook scripts
    themselves existing and executable under ``.claude/hooks/``.
    """
    from trw_mcp.bootstrap._claude_code_distill_channels import (
        cc03_hook_scripts_present,
        cc03_registered_in_settings,
    )

    cc03_clients = [client for client in wired if client in ("claude-code", "codex")]
    if not cc03_clients:
        return None
    if not cc03_hook_scripts_present(target):
        return "the CC-03 hook script is missing or not executable under .claude/hooks/"
    if "claude-code" in cc03_clients and not cc03_registered_in_settings(target):
        return ".claude/settings.json has no PreToolUse registration for the CC-03 hook"
    if "codex" in cc03_clients:
        from trw_mcp.bootstrap._codex_distill_channels import codex_pre_edit_hint_registered

        if not codex_pre_edit_hint_registered(target):
            return ".codex/hooks.json has no PreToolUse registration for the CC-03 hook"
    return None


def hook_channel_row(target: Path) -> tuple[Status, str]:
    """The ``hook_channel`` verdict for the clients *target* selected."""
    from trw_mcp.server._doctor_agent_parity import _selected_clients

    clients, unreadable = _selected_clients(target)
    if unreadable is not None:
        return "WARN", f"selected clients not measured ({unreadable})"
    from trw_mcp.channels.claude_code._hook_helpers import read_cc03_config

    fallback = fallback_clients(clients)
    unknown = unclassified_profiles(clients)
    wired = [client for client in clients if client not in fallback and client not in unknown]
    # The hook ships and registers only while cc03_hook_enabled is on; off, a
    # hook-wired client gets the hint only through the instruction line.
    hook_off = wired and not read_cc03_config(target)["cc03_hook_enabled"]
    if wired and not hook_off and (unwired_reason := _cc03_unwired_reason(target, wired)) is not None:
        remedy = _reprovision_remedy(target) if "missing" in unwired_reason else _REMEDY
        return "FAIL", (
            f"cc03_hook_enabled is on for {', '.join(wired)} but the hook is not wired: {unwired_reason}. {remedy}"
        )
    if not fallback and not unknown and not hook_off:
        channels = "; ".join(f"{client}: {PRE_EDIT_HINT_CHANNELS[client].channel}" for client in wired)
        return "PASS", f"pre-edit hint delivered by hook ({channels or 'no client selected'})"
    parts = []
    if hook_off:
        parts.append(
            f"pre-edit hint hook is off (cc03_hook_enabled is false) for {', '.join(wired)}: "
            'their instructions tell the agent to call trw_code(mode="hint") before editing'
        )
    if fallback:
        parts.append(
            f"no model-visible pre-edit hook channel for {', '.join(fallback)}: their instructions tell the "
            'agent to call trw_code(mode="hint") before editing'
        )
    if unknown:
        parts.append(f"no pre-edit hint decision recorded for {', '.join(unknown)}")
    if fallback and not unknown and not hook_off:
        # Nothing to fix: these clients have no hook channel by design (feedback #141), so this is information, not a
        # warning. The doctor vocabulary has no INFO level, so it reads as a PASS that says what the channel is.
        return "PASS", f"info: {parts[0]}"
    return "WARN", "; ".join(parts)


def check_hook_channel(target: Path, _config: object) -> CheckResult:
    """Doctor-registry entry point (imported by name into _subcommands_doctor.py's globals)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("hook_channel", *hook_channel_row(target))
