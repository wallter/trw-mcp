"""The temporal half of a lease, as one public contract (PRD-CORE-296-FR03, ledger RC-011).

Two leases decide "does this holder still hold it?": a comms endpoint
(``comms._endpoints.Endpoint``, epoch-second floats, never released, only
replaced by a newer generation) and a swarm path lease (aware datetimes, released
explicitly, optionally never expiring). Each kept its own clock and its own
comparison, so nothing stopped one from treating the expiry instant as live
while the other treated it as expired.

The contract is only the question, asked at a caller-supplied instant: the clock
stays the implementer's, and a lease that cannot be released or cannot be
open-ended simply does not claim that capability to the conformance check.
What is spatial -- which paths a lease covers and whether two overlap -- is not
temporal and stays with its owner.

Dependency direction is one-way: implementers elsewhere import this module
(structurally, or in their tests for :func:`check_temporal_fence`); nothing here
imports them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, TypeVar

__all__ = ["TemporalFence", "check_temporal_fence"]

InstantT = TypeVar("InstantT")
InstantT_contra = TypeVar("InstantT_contra", contravariant=True)


class TemporalFence(Protocol[InstantT_contra]):
    """A lease's hold, judged at an instant on the implementer's own clock."""

    def is_live(self, now: InstantT_contra) -> bool:
        """True while held: not released, and ``now`` strictly before any expiry."""
        ...


def check_temporal_fence(
    make: Callable[[InstantT | None], TemporalFence[InstantT]],
    *,
    before: InstantT,
    expiry: InstantT,
    after: InstantT,
    release: Callable[[TemporalFence[InstantT]], TemporalFence[InstantT]] | None = None,
    open_ended: bool = False,
) -> None:
    """Raise ``AssertionError`` naming the first rule *make*'s fences break.

    *make* builds a fence expiring at the given instant (``None`` = never, asked
    only when *open_ended*). ``before < expiry < after`` on the implementer's clock.
    *release*, when the lease can be released, returns the released fence.
    """
    fence = make(expiry)
    rules = [
        ("live before its expiry", fence.is_live(before)),
        ("not live AT its expiry (the instant is exclusive)", not fence.is_live(expiry)),
        ("not live after its expiry", not fence.is_live(after)),
    ]
    if release is not None:
        rules.append(("not live once released, even before expiry", not release(fence).is_live(before)))
    if open_ended:
        rules.append(("live at any instant when it never expires", make(None).is_live(after)))
    broken = [name for name, held in rules if not held]
    if broken:
        raise AssertionError(f"TemporalFence contract broken: {', '.join(broken)}")
