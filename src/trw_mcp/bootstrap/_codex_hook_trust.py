"""Opt-in pre-approval of TRW's own Codex hooks, written as the record Codex's "Allow" writes.

Codex runs a project hook only once the user trusts it. The trust record lives in the USER config
(``$CODEX_HOME/config.toml``, default ``~/.codex/config.toml``) as one table per hook handler::

    [hooks.state."<abs path>/.codex/hooks.json:pre_tool_use:0:0"]
    trusted_hash = "sha256:<hex>"

The key is ``<hooks.json path>:<event>:<group index>:<handler index>`` and the hash is a sha256 over a
normalized identity of the handler (event, matcher, command, timeout, async, statusMessage), so any edit
to the hook entry changes the hash and Codex marks it Modified and asks again. The script bytes the
command runs are NOT part of the hash. Sources (openai/codex): ``codex-rs/hooks/src/engine/discovery.rs``
(``hook_hash``, ``hook_trust_status``), ``codex-rs/hooks/src/lib.rs`` (``hook_key``),
``codex-rs/config/src/fingerprint.rs`` (``version_for_toml``), ``codex-rs/hooks/src/config_rules.rs``
(only the user and session layers may carry hook state), ``codex-rs/tui/src/hooks_rpc.rs`` (the UI writes
``hooks.state.<key>.trusted_hash``). The hash below reproduced every trusted_hash the Codex UI wrote on
the operator's machine (9 of 9, codex-cli 0.160.0).

Fail-safe by construction: a hash this module gets wrong never trusts anything; Codex just reports the
hook as Modified and prompts, as it does today. Only handlers inside TRW-managed groups whose command runs
a TRW script are approved, and an edit to the user's config is verified by re-parsing before it is written.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from ._codex_hooks import _is_trw_hook_group
from ._file_ops import read_json_object

_EVENT_KEYS = {
    "PreToolUse": "pre_tool_use",
    "PermissionRequest": "permission_request",
    "PostToolUse": "post_tool_use",
    "PreCompact": "pre_compact",
    "PostCompact": "post_compact",
    "SessionStart": "session_start",
    "UserPromptSubmit": "user_prompt_submit",
    "SubagentStart": "subagent_start",
    "SubagentStop": "subagent_stop",
    "Stop": "stop",
}
#: Codex ignores a matcher on these events (``matcher_pattern_for_event``), so it is not hashed.
_MATCHERLESS_EVENTS = frozenset({"UserPromptSubmit", "Stop"})
#: Events whose ``additionalContextLimit`` Codex keeps; the default (2,500) is normalized away.
_CONTEXT_LIMIT_EVENTS = frozenset({"PreToolUse", "PostToolUse", "SessionStart", "UserPromptSubmit", "SubagentStart"})
_DEFAULT_CONTEXT_LIMIT = 2_500
_DEFAULT_TIMEOUT_SEC = 600
#: A handler is TRW's own only when its command runs a TRW-shipped script.
_TRW_SCRIPT_MARKERS = ("/.claude/hooks/", "/.codex/hooks/trw_")


def codex_home() -> Path:
    """Codex's home directory: ``$CODEX_HOME`` when set, else ``~/.codex``."""
    env_home = os.environ.get("CODEX_HOME", "").strip()
    return Path(env_home).expanduser() if env_home else Path.home() / ".codex"


def codex_hook_hash(event: str, matcher: object, handler: Mapping[str, object]) -> str | None:
    """Codex's ``trusted_hash`` for one command handler, or ``None`` when TRW will not approve it."""
    command = handler.get("command")
    timeout = handler.get("timeout", _DEFAULT_TIMEOUT_SEC)
    if (
        event not in _EVENT_KEYS
        or handler.get("type") != "command"
        or not isinstance(command, str)
        or not command.strip()
        or not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout < 0
    ):
        return None
    normalized: dict[str, object] = {
        "type": "command",
        "command": command,
        "timeout": max(timeout, 1),
        "async": handler.get("async") is True,
    }
    status = handler.get("statusMessage")
    if isinstance(status, str):
        normalized["statusMessage"] = status
    limit = handler.get("additionalContextLimit")
    if event in _CONTEXT_LIMIT_EVENTS and isinstance(limit, int) and limit != _DEFAULT_CONTEXT_LIMIT:
        normalized["additionalContextLimit"] = limit
    identity: dict[str, object] = {"event_name": _EVENT_KEYS[event], "hooks": [normalized]}
    if isinstance(matcher, str) and event not in _MATCHERLESS_EVENTS:
        identity["matcher"] = matcher
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trw_codex_hook_trust_entries(target_dir: Path) -> dict[str, str]:
    """``{hooks.state key: trusted_hash}`` for every TRW-owned handler in the project's ``.codex/hooks.json``.

    One key per path spelling: Codex keys the record by the hooks.json path it loaded, and a project
    reached through a symlink can be loaded under either spelling.
    """
    hooks_json = target_dir.absolute() / ".codex" / "hooks.json"
    payload = read_json_object(hooks_json, context="codex_hook_trust")
    events = payload.get("hooks") if payload is not None else None
    if not isinstance(events, dict):
        return {}
    spellings = dict.fromkeys([str(hooks_json), str(hooks_json.resolve())])
    entries: dict[str, str] = {}
    for event, groups in events.items():
        if not isinstance(groups, list) or event not in _EVENT_KEYS:
            continue
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict) or not _is_trw_hook_group(event, group):  # type: ignore[arg-type]
                continue
            handlers = group.get("hooks")
            for handler_index, handler in enumerate(handlers if isinstance(handlers, list) else []):
                command = handler.get("command") if isinstance(handler, dict) else None
                if not isinstance(command, str) or not any(m in command for m in _TRW_SCRIPT_MARKERS):
                    continue
                digest = codex_hook_hash(event, group.get("matcher"), handler)
                if digest is None:
                    continue
                for spelling in spellings:
                    entries[f"{spelling}:{_EVENT_KEYS[event]}:{group_index}:{handler_index}"] = digest
    return entries


@dataclass
class TrustReport:
    """What one approve/revoke pass did, line by line, for the operator."""

    config_path: Path
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    error: str | None = None


def _header(key: str) -> str:
    return f"[hooks.state.{json.dumps(key, ensure_ascii=False)}]"


def _block_end(lines: list[str], start: int) -> int:
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    return end


_HASH_LINE = re.compile(r"^\s*trusted_hash\s*=")


def _edit_state(text: str, desired: Mapping[str, str], *, revoke: bool, report: TrustReport) -> str:
    """Apply approvals (or revocations) to the config text, touching only TRW's own state tables."""
    state = _state_table(tomllib.loads(text))
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for key, digest in desired.items():
        entry = state.get(key)
        current = entry.get("trusted_hash") if isinstance(entry, dict) else None
        if (current == digest) != revoke:  # approve: already trusted; revoke: not TRW's hash, leave it
            report.unchanged.append(key)
            continue
        header = _header(key)
        idx = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        if idx is None:
            if key in state:
                report.skipped.append(f"{key}: written in a form TRW does not edit; review it in Codex /hooks")
                continue
            lines += ["\n", header + "\n", f'trusted_hash = "{digest}"\n']
            report.changed.append(key)
            continue
        end = _block_end(lines, idx)
        hash_rows = [i for i in range(idx + 1, end) if _HASH_LINE.match(lines[i])]
        new_row = [] if revoke else [f'trusted_hash = "{digest}"\n']
        if hash_rows:
            lines[hash_rows[0] : hash_rows[0] + 1] = new_row
        else:
            lines[idx + 1 : idx + 1] = new_row
        end = _block_end(lines, idx)
        if revoke and not any(line.strip() and not line.lstrip().startswith("#") for line in lines[idx + 1 : end]):
            del lines[idx:end]
        report.changed.append(key)
    return "".join(lines)


def _state_table(parsed: Mapping[str, Any]) -> dict[str, Any]:
    hooks = parsed.get("hooks")
    state = hooks.get("state") if isinstance(hooks, dict) else None
    return state if isinstance(state, dict) else {}


def _expected_config(
    before: dict[str, Any], desired: Mapping[str, str], report: TrustReport, *, revoke: bool
) -> dict[str, Any]:
    """The parsed config the edit must produce: ``before`` plus exactly the reported changes."""
    expected = copy.deepcopy(before)
    if not report.changed:
        return _pruned(expected)
    state = expected.setdefault("hooks", {}).setdefault("state", {})
    for key in report.changed:
        if revoke:
            state[key].pop("trusted_hash", None)
            if not state[key]:
                del state[key]
        else:
            state.setdefault(key, {})["trusted_hash"] = desired[key]
    return _pruned(expected)


def _pruned(config: dict[str, Any]) -> dict[str, Any]:
    """Drop an empty ``hooks.state`` / ``hooks``: removing TRW's last table may leave none, which is the same config."""
    hooks = config.get("hooks")
    if isinstance(hooks, dict) and hooks.get("state") == {}:
        del hooks["state"]
    if hooks == {}:
        del config["hooks"]
    return config


def _apply(target_dir: Path, *, revoke: bool, home: Path | None) -> TrustReport:
    config_path = (home or codex_home()) / "config.toml"
    report = TrustReport(config_path=config_path)
    desired = trw_codex_hook_trust_entries(target_dir)
    if not desired:
        report.skipped.append(f"no TRW-managed hooks in {target_dir.absolute() / '.codex' / 'hooks.json'}")
        return report
    if revoke and not config_path.is_file():
        report.skipped.append(f"{config_path} does not exist; no approvals to remove")
        return report
    if not config_path.parent.is_dir():
        report.error = f"{config_path.parent} does not exist (is Codex installed?); nothing written"
        return report
    try:
        text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        before = tomllib.loads(text)
        new_text = _edit_state(text, desired, revoke=revoke, report=report)
        verified = _pruned(tomllib.loads(new_text)) == _expected_config(before, desired, report, revoke=revoke)
    except (OSError, ValueError) as exc:  # tomllib.TOMLDecodeError and UnicodeDecodeError are ValueErrors
        report.error = f"{config_path} left unchanged: {type(exc).__name__}: {exc}"
        report.changed.clear()
        return report
    if not verified:
        report.error = f"{config_path} left unchanged: the edit would have changed more than TRW's hook approvals"
        report.changed.clear()
        return report
    if report.changed:
        _atomic_write(config_path, new_text)
    return report


def _atomic_write(path: Path, text: str) -> None:
    real = path.resolve()
    mode = real.stat().st_mode & 0o777 if real.exists() else 0o600
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=".config.toml.trw-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, real)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def trust_trw_codex_hooks(target_dir: Path, *, home: Path | None = None) -> TrustReport:
    """Record Codex trust for TRW's own hooks in *target_dir*, pinned to each handler's current hash."""
    return _apply(target_dir, revoke=False, home=home)


def revoke_trw_codex_hook_trust(target_dir: Path, *, home: Path | None = None) -> TrustReport:
    """Remove the trust records that exactly match TRW's hooks in *target_dir* (run before hooks.json goes)."""
    return _apply(target_dir, revoke=True, home=home)


def report_lines(report: TrustReport, *, revoke: bool) -> list[str]:
    """Operator-facing lines naming every approval written, kept, skipped or refused."""
    verb = "Revoked" if revoke else "Approved"
    lines = [f"  {verb} in {report.config_path}: {key}" for key in report.changed]
    lines += [f"  Unchanged: {key}" for key in report.unchanged]
    lines += [f"  Skipped: {reason}" for reason in report.skipped]
    if report.error:
        lines.append(f"  Error: {report.error}")
    return lines


def revoke_on_uninstall(target_dir: Path, removed_client: str | None) -> None:
    """Uninstall step: drop TRW's Codex approvals for a whole-project or ``--ide codex`` removal, naming each."""
    if removed_client not in (None, "codex"):
        return
    report = revoke_trw_codex_hook_trust(target_dir)
    for line in report_lines(report, revoke=True) if report.changed or report.error else []:
        print(line)


def run_cli(args: argparse.Namespace) -> None:
    """``trw-mcp trust-codex-hooks [DIR] [--revoke]``: the explicit, consented approve/revoke step."""
    revoke = bool(getattr(args, "revoke", False))
    target = Path(getattr(args, "target_dir", ".")).absolute()
    report = (revoke_trw_codex_hook_trust if revoke else trust_trw_codex_hooks)(target)
    for line in report_lines(report, revoke=revoke):
        print(line)
    if report.error:
        raise SystemExit(1)
