"""Host write-denial confinement for read-only dispatch (PRD-CORE-277-FR02).

Belongs to the ``trw_mcp.dispatch`` package. Some clients cannot give TRW a
usable read-only run from their own flags: headless ``agy`` auto-denies file
READS unless its permission prompt is relaxed, and the flag that relaxes it
(``--dangerously-skip-permissions``) also removes the write guard. The way out
is to let the OPERATING SYSTEM deny the writes and to emit that flag only while
that denial is in force.

What this buys, stated exactly: a denial of local FILESYSTEM WRITES for the
wrapped process tree. It is NOT a network bound, and it does not reach a tool the
child calls through its own MCP servers. ``DispatchResult.sandbox_note`` repeats
that scope so no surface can restate it as "sandboxed".

macOS is the only implementation today. ``sandbox-exec`` is the same mechanism
codex's own read-only sandbox uses; it is deprecated by Apple but functional
(measured on Darwin 25.5.0, 2026-09-16: a write inside the wrapped tree failed
with "Operation not permitted" while the read succeeded). Everywhere else the
wrapper is empty, which is the fail-closed direction: no wrapper means no
``confined_read_only_argv``, so the child keeps the flags it has today.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["CONFINEMENT_MECHANISM", "confinement_prefix", "confinement_unavailable_reason"]

#: Absolute path to the macOS sandbox wrapper. Absolute on purpose: a PATH lookup
#: for a security control is a control an attacker can move.
_SANDBOX_EXEC = "/usr/bin/sandbox-exec"

#: Seatbelt profile: allow everything the child would normally do, then deny
#: every filesystem write, then re-allow ``/dev`` so the child can still talk to
#: its own stdout/stderr and /dev/null. ``deny file-write*`` covers create,
#: unlink, chmod, setattr and friends, so a marker file cannot appear anywhere the
#: profile has not explicitly re-allowed.
#:
#: The re-allow is a subpath of ``/dev`` only. A wider allowance (a temp
#: directory, say) would be exactly the hole the probe in ``_sandbox_probe`` looks
#: for: measured 2026-09-16, allowing ``/private/tmp`` let the child write its
#: marker there.
_WRITE_DENY_PROFILE = '(version 1)(allow default)(deny file-write*)(allow file-write* (subpath "/dev"))'

#: Human-readable name of the mechanism, reported in ``sandbox_note``.
CONFINEMENT_MECHANISM = "macos-seatbelt(sandbox-exec) filesystem write denial"


def confinement_prefix(writable: Path | None = None) -> list[str]:
    """Return the argv prefix that confines a child's writes, or ``[]``.

    ``[]`` means TRW cannot confine writes on this host. The caller MUST then
    withhold the client's ``confined_read_only_argv``: the permission bypass is
    only acceptable while the denial is in force.

    *writable* re-allows one directory, and only the isolated-review lane passes
    it: that lane's per-run temp HOME, created and removed around one run
    (PRD-CORE-297-FR05; agy cannot start without writing its HOME).
    """
    if sys.platform != "darwin":
        return []
    if not os.path.isfile(_SANDBOX_EXEC) or not os.access(_SANDBOX_EXEC, os.X_OK):
        return []
    if writable is None:
        return [_SANDBOX_EXEC, "-p", _WRITE_DENY_PROFILE]
    path = str(writable.resolve())  # seatbelt matches the real path (/var -> /private/var)
    if '"' in path or "\\" in path:
        raise ValueError(f"writable path {path!r} cannot be quoted into the sandbox profile")
    return [_SANDBOX_EXEC, "-p", f'{_WRITE_DENY_PROFILE}(allow file-write* (subpath "{path}"))']


def confinement_unavailable_reason() -> str:
    """Say why no wrapper is available, for the result's ``sandbox_note``."""
    if sys.platform != "darwin":
        return f"no host write-denial wrapper on {sys.platform}: TRW confines writes only via macOS sandbox-exec today"
    return f"{_SANDBOX_EXEC} is missing or not executable"
