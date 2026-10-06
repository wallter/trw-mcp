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

Codex registers its scripts only when the project turns Codex hooks on
(``[features].hooks`` in ``.codex/config.toml``) -- the same predicate that
gates writing ``.codex/hooks.json``. Without it codex runs none of them, so it
registers none (PRD-CORE-301 FR07), and an update withdraws unedited copies an
earlier install left behind (``_template_updater._withdraw_retired_hooks``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
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


def _registered_codex_hooks(target_dir: Path) -> set[str]:
    """Hook scripts Codex's TRW-managed ``hooks.json`` payload registers in *target_dir*.

    Empty unless Codex hooks are enabled there: ``generate_codex_hooks`` writes
    the payload only under ``codex_hooks_enabled``, so without it no codex
    configuration runs these scripts.
    """
    from ._codex import codex_hooks_enabled
    from ._codex_hooks import _codex_hooks_payload

    if not codex_hooks_enabled(target_dir):
        return set()
    return set(_HOOK_PATH_RE.findall(json.dumps(_codex_hooks_payload())))


def _registered_copilot_hooks(_target_dir: Path) -> set[str]:
    """Hook scripts Copilot's TRW-managed ``hooks.json`` payload registers (unconditionally)."""
    from ._copilot import _copilot_hooks_payload

    return set(_HOOK_PATH_RE.findall(json.dumps(_copilot_hooks_payload())))


_CLIENT_REGISTRARS: dict[str, Callable[[Path], set[str]]] = {
    "codex": _registered_codex_hooks,
    "copilot": _registered_copilot_hooks,
}


def registered_hook_scripts_for_clients(clients: Sequence[str], target_dir: Path) -> set[str]:
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
        scripts |= (
            _registered_claude_code_hooks() if client == "claude-code" else _CLIENT_REGISTRARS[client](target_dir)
        )
    return scripts


def init_hook_files(clients: Sequence[str], hooks_dir: Path, target_dir: Path, *, explicit: bool) -> list[str]:
    """The ``.claude/hooks`` files ``_init_project._install_hooks`` copies, sorted.

    A written ``.claude/settings.json`` registers claude-code's hooks whatever
    the resolved clients are (a bare init that detected codex still writes it),
    so claude-code joins the registrars exactly when that file is written.
    """
    from ._client_ownership import writes_surface

    with_settings = writes_surface(".claude/settings.json", clients, explicit=explicit)
    registrars = [*clients, "claude-code"] if with_settings else list(clients)
    return sorted(deployable_hook_files(registrars, hooks_dir, target_dir))


def _sourced_helpers(script_path: Path) -> set[str]:
    """Helper script names *script_path* sources (empty when no such bundled file exists)."""
    # A registered name with no bundled file sources nothing (it is filtered out
    # of the deployable set below); any other read failure propagates.
    if not script_path.is_file():
        return set()
    return set(_SOURCE_REF_RE.findall(script_path.read_text(encoding="utf-8")))


def deployable_hook_files(clients: Sequence[str], hooks_dir: Path, target_dir: Path) -> set[str]:
    """Registered hooks for *clients* in *target_dir* plus the transitive closure of what they source.

    Called by ``_init_project._install_hooks`` and ``_template_updater._update_hooks``.

    *hooks_dir* is scanned for ``source``/``.`` references so a helper only
    ships when some registered script in this exact bundle actually sources
    it — never a second hand-kept helper list.
    """
    closure = registered_hook_scripts_for_clients(clients, target_dir)
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


def settings_hook_refs(target_dir: Path) -> set[str]:
    """Every ``.claude/hooks/<name>.sh`` the project's own ``.claude/settings.json`` registers (empty when absent).

    Read as text, so a settings file that does not parse still names its hooks. Used by ``doctor``'s
    ``hook_family`` row: a registered hook missing from disk fails on every Claude Code event.
    """
    try:
        raw = (target_dir / ".claude" / "settings.json").read_text(encoding="utf-8", errors="replace")
    except OSError:  # trw-fail-silent-allow: no readable settings.json registers no hook
        return set()
    return set(_HOOK_PATH_RE.findall(raw))
