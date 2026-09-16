"""Test-side derivation of the bundled hook set and its registration carriers.

Import as::

    from tests._hook_carriers import BUNDLED_HOOKS, HOOK_CARRIERS

Why this exists
---------------
Several test modules answered "is this hook registered?" by reading a
hardcoded two-tuple of template paths (``data/settings.json`` and
``data/plugin/hooks/hooks.json``). That is the same shape as the skill-parity
5-tuple that let ``cursor-ide`` ship without ``trw-feedback``: a third carrier
would be invisible to every one of those assertions until someone remembered
to edit each copy.

The carrier PREDICATE is structural, not textual
------------------------------------------------
A substring scan for ``session-start.sh`` also matches ``bundle-hashes.json``
and ``config-unread-fields.json``, which merely *mention* hook filenames and
register nothing. Only a ``"command"`` value can cause a hook to run, and every
shipped hook schema (Claude Code's nested ``hooks`` arrays, Antigravity's flat
matcher entries, Copilot's lowercase events) spells it with that key — so the
predicate reads command values and nothing else.

The floors are the load-bearing part
------------------------------------
A glob that stops matching turns every consumer into a vacuous pass. Both
counts are therefore asserted at import time, before any parametrization can
read them.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data"
HOOK_DIR = DATA_DIR / "hooks"

#: Shared shell libraries register no event of their own; they are reached by
#: being sourced. Same rule as ``scripts/generate-inventory.py::_extract_hooks``
#: and ``scripts/tests/test_bundled_hook_registration.py``.
LIBRARY_PREFIX = "lib-"

#: Counts observed 2026-09-12. Lowering either is part of deleting a hook or a
#: carrier and must be a deliberate edit, never a silent derivation result.
MINIMUM_BUNDLED_HOOKS = 14
MINIMUM_HOOK_CARRIERS = 2


def _commands(node: object) -> list[str]:
    """Every ``"command": "<str>"`` value anywhere in a parsed hook config."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "command" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_commands(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_commands(item))
    return found


def bundled_hooks() -> tuple[str, ...]:
    """Every bundled script that is a HOOK — i.e. every one but the libraries."""
    return tuple(sorted(p.name for p in HOOK_DIR.glob("*.sh") if not p.name.startswith(LIBRARY_PREFIX)))


def registered_in(carrier: Path) -> frozenset[str]:
    """Bundled hook script names *carrier* registers as a runnable command."""
    try:
        data = json.loads(carrier.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return frozenset()
    blob = "\n".join(_commands(data))
    return frozenset(name for name in bundled_hooks() if name in blob)


def hook_carriers() -> tuple[Path, ...]:
    """Every shipped JSON template that REGISTERS a bundled hook as a command."""
    return tuple(path for path in sorted(DATA_DIR.rglob("*.json")) if registered_in(path))


def carrier_group(carrier: Path) -> str:
    """Which client bundle *carrier* belongs to — derived from its location.

    Parity is only meaningful WITHIN a bundle: Copilot's hook schema has five
    lowercase events and Claude Code's has eleven, so comparing across bundles
    would compare two vendors' schemas rather than two carriers of one protocol.
    ``plugin`` and ``hooks`` are packaging directories, not clients, so they
    resolve to the bundle that contains them (``""`` = the root claude-code
    bundle, whose carriers are ``settings.json`` and ``plugin/hooks/hooks.json``).
    """
    for part in carrier.relative_to(DATA_DIR).parts[:-1]:
        if part not in ("plugin", "hooks"):
            return part
    return ""


BUNDLED_HOOKS: tuple[str, ...] = bundled_hooks()
HOOK_CARRIERS: tuple[Path, ...] = hook_carriers()

# Explicit raises rather than ``assert``: a floor that ``python -O`` can strip
# is not a floor.
if len(BUNDLED_HOOKS) < MINIMUM_BUNDLED_HOOKS:
    raise RuntimeError(
        f"bundled-hook derivation returned {len(BUNDLED_HOOKS)} script(s) ({BUNDLED_HOOKS}), "
        f"below the shipped floor of {MINIMUM_BUNDLED_HOOKS}. Every consumer would check fewer "
        "hooks than ship. If a hook was deleted, lower MINIMUM_BUNDLED_HOOKS in the same change."
    )
if len(HOOK_CARRIERS) < MINIMUM_HOOK_CARRIERS:
    raise RuntimeError(
        f"hook-carrier derivation returned {len(HOOK_CARRIERS)} template(s) "
        f"({[str(p) for p in HOOK_CARRIERS]}), below the shipped floor of {MINIMUM_HOOK_CARRIERS}. "
        "A registration surface stopped being discovered, so per-carrier parity is not being checked."
    )
