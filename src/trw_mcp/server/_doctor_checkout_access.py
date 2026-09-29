"""``trw-mcp doctor`` row for the checkout-access pinned-fd cache (PRD-CORE-316 P3, worker-3 review).

Belongs to the ``_subcommands_doctor.py`` facade.

``trw_mcp._checkout_access._race_loser_fds`` tracks descriptors this process deliberately never
closes: either a stat/open race's losing descriptor (FR02(b)), or a retirement whose key was
re-pinned by a different tracked path before the deferred close ran (the P0 fix from
core-316-sA round 3). Both are correctness-preserving by design -- closing either risks dropping
an unrelated live POSIX fcntl lock this process holds on the same inode via a different
descriptor -- but each one is a real, permanently-open file descriptor an operator should be able
to see. This row is always PASS: it is a diagnostic count, never a defect signal, so it never
trains an operator to treat a normal, bounded-by-rarity outcome as something to fix.
"""

from __future__ import annotations

__all__ = ["checkout_access_row"]


def checkout_access_row() -> tuple[str, str]:
    """Return ``(status, message)``: the pinned-fd cache's race-loser descriptor count."""
    from trw_mcp._checkout_access import _race_loser_fds

    count = len(_race_loser_fds)
    if count:
        message = (
            f"{count} race-loser descriptor(s) held open in this process (never closed by design: "
            "closing one could drop an unrelated live fcntl lock on the same inode)."
        )
    else:
        message = "0 race-loser descriptors held (the common case)."
    return "PASS", message
