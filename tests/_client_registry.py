"""Test-side access to the client-profile registry, with a non-vacuity floor.

Import as::

    from tests._client_registry import ACTIVE_CLIENT_IDS

Why this exists
---------------
A per-profile test that hardcodes the client set can only check the clients it
was told about. Three defects of that exact shape shipped in this repo within
24 hours (a skill-parity 5-tuple that let ``cursor-ide`` ship without
``trw-feedback``; a 9-tuple of release-leak path roots against 10 declared
packages; a 10-entry package roster in ``_prd_proof_paths`` that had drifted).
Deriving the parametrization from ``builtin_client_ids()`` means a new profile
is exercised by every per-profile invariant the moment it is registered.

The floor is the load-bearing part
----------------------------------
``pytest.mark.parametrize`` over an EMPTY list collects zero cases and the
suite still reports green, so a broken derivation would convert every
per-profile check into a vacuous pass — strictly worse than the hardcoded
tuples, which at least failed loudly. ``ACTIVE_CLIENT_IDS`` is therefore
asserted non-empty and at least as large as the count TRW has shipped, at
import time, before any parametrization can read it.

``builtin_client_ids()`` carries its own fail-closed floor in
``models/config/_profiles.py``; this is the independent second one, on the
consuming side, because that is where a vacuous pass would be reported as
success.
"""

from __future__ import annotations

from trw_mcp.models.config import builtin_client_ids, retired_client_ids

#: Count of active profiles TRW has shipped: antigravity-cli, claude-code,
#: codex, copilot, cursor-cli, cursor-ide, opencode. Raising this is part of
#: adding a profile; lowering it is part of REMOVING one (as ``gemini`` was on
#: 2026-07-24) and must be a deliberate edit, never a silent derivation result.
MINIMUM_ACTIVE_CLIENTS = 7

#: Active (installable) profile ids, registry order.
ACTIVE_CLIENT_IDS: tuple[str, ...] = tuple(builtin_client_ids())

#: Retired ids retained for uninstall/migration cleanup only.
RETIRED_CLIENT_IDS: frozenset[str] = retired_client_ids()

# Explicit raises rather than ``assert``: a floor that ``python -O`` can strip
# is not a floor.
if len(ACTIVE_CLIENT_IDS) < MINIMUM_ACTIVE_CLIENTS:
    raise RuntimeError(
        f"client-profile derivation returned {len(ACTIVE_CLIENT_IDS)} ids "
        f"({ACTIVE_CLIENT_IDS}), below the shipped floor of {MINIMUM_ACTIVE_CLIENTS}. "
        "Every derived per-profile parametrization would collect fewer cases than it should. "
        "Either the registry is broken or a profile was removed — if removed, lower "
        "MINIMUM_ACTIVE_CLIENTS deliberately in the same change."
    )
if set(ACTIVE_CLIENT_IDS) & RETIRED_CLIENT_IDS:
    raise RuntimeError(
        f"retired client id(s) reported as active: {sorted(set(ACTIVE_CLIENT_IDS) & RETIRED_CLIENT_IDS)}"
    )
