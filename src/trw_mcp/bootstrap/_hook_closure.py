"""Registered-hook-plus-helper-closure computation (PRD-INFRA-192 FR10).

Install/update must deploy only hooks some client actually registers, plus
the helper scripts (``lib-*.sh``) those hooks ``source``. A helper never gets
a fake registration of its own merely to justify shipping it — it earns its
place by being sourced from a script that IS registered.

The registered set is derived from each client's real registration payload
(the bundled ``settings.json`` template for claude-code, ``_codex_hooks_payload``
for codex, ``_COPILOT_HOOK_MAP`` for copilot) rather than a hand-kept mirror
list, so a future hook addition/removal in those payloads is picked up here
automatically instead of silently drifting.

``.claude/hooks/`` is shared by three clients (claude-code, codex, copilot —
see ``bootstrap/_client_ownership.py``), so the deployable set for a given
install/update is the UNION of whichever of those three are in the resolved
client list, plus the transitive closure of sourced helpers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

# Matches the bundled command convention "<...>/.claude/hooks/<name>.sh",
# used identically by the claude-code settings.json template, the codex
# hooks.json payload, and the copilot hooks.json payload.
_HOOK_PATH_RE = re.compile(r"\.claude/hooks/([A-Za-z0-9_.\-]+\.sh)")

# Matches an in-script `. "$_hook_dir/<name>.sh"` (or equivalent) source line
# referencing a sibling script in the same hooks directory.
_SOURCE_REF_RE = re.compile(r"\$_hook_dir/([A-Za-z0-9_.\-]+\.sh)")


def _registered_claude_code_hooks() -> set[str]:
    """Hook scripts the bundled ``.claude/settings.json`` template registers.

    Always reads the package's own bundled ``settings.json`` (not a
    per-call *hooks_dir* override) — the registration payload is fixed
    content, independent of whichever hooks directory a caller (or test) is
    scanning for helper closures.
    """
    from ._utils import _DATA_DIR

    # No fallback: an unreadable bundled template is a broken install, and an
    # empty registered set here would let the update sweep withdraw every hook.
    raw = (_DATA_DIR / "settings.json").read_text(encoding="utf-8")
    return set(_HOOK_PATH_RE.findall(raw))


def _registered_codex_hooks() -> set[str]:
    """Hook scripts Codex's TRW-managed ``hooks.json`` payload registers."""
    from ._codex_hooks import _codex_hooks_payload

    return set(_HOOK_PATH_RE.findall(json.dumps(_codex_hooks_payload())))


def _registered_copilot_hooks() -> set[str]:
    """Hook scripts Copilot's TRW-managed ``hooks.json`` payload registers."""
    from ._copilot import _copilot_hooks_payload

    return set(_HOOK_PATH_RE.findall(json.dumps(_copilot_hooks_payload())))


_CLIENT_REGISTRARS = {
    "codex": _registered_codex_hooks,
    "copilot": _registered_copilot_hooks,
}


def registered_hook_scripts_for_clients(clients: Sequence[str]) -> set[str]:
    """Union of hook scripts registered by *clients* that share ``.claude/hooks/``.

    An empty/unrecognized *clients* list (no ownership record — see
    ``update_owns_surface``) falls back to claude-code's full registration,
    matching the historical no-record-means-no-narrowing default.
    """
    resolved = [c for c in clients if c == "claude-code" or c in _CLIENT_REGISTRARS]
    if not resolved:
        return _registered_claude_code_hooks()
    scripts: set[str] = set()
    for client in resolved:
        scripts |= _registered_claude_code_hooks() if client == "claude-code" else _CLIENT_REGISTRARS[client]()
    return scripts


def _sourced_helpers(script_path: Path) -> set[str]:
    """Helper script names *script_path* sources (empty when no such bundled file exists)."""
    # A registered name with no bundled file sources nothing (it is filtered out
    # of the deployable set below); any other read failure propagates.
    if not script_path.is_file():
        return set()
    return set(_SOURCE_REF_RE.findall(script_path.read_text(encoding="utf-8")))


def deployable_hook_files(clients: Sequence[str], hooks_dir: Path) -> set[str]:
    """Registered hooks for *clients* plus the transitive closure of what they source.

    *hooks_dir* is scanned for ``source``/``.`` references so a helper only
    ships when some registered script in this exact bundle actually sources
    it — never a second hand-kept helper list.
    """
    closure = registered_hook_scripts_for_clients(clients)
    frontier = set(closure)
    while frontier:
        next_frontier: set[str] = set()
        for name in frontier:
            for helper in _sourced_helpers(hooks_dir / name):
                if helper not in closure:
                    closure.add(helper)
                    next_frontier.add(helper)
        frontier = next_frontier
    # A registered name with no matching bundled file is not deployable — the
    # channel installer for that hook (if any) owns shipping it separately.
    return {name for name in closure if (hooks_dir / name).is_file()}
