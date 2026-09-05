"""Ambient delivery-journal binding for NESTED crash boundaries — PRD-FIX-127 FR03.

Lives at the package root, beside ``_locking``, because it is a LAYER-NEUTRAL
primitive: a ``ContextVar`` and two context managers with no runtime dependency on
anything (the ``DeliverJournal`` import is ``TYPE_CHECKING`` only). ``state/`` may
never import from ``tools/`` (``tests/test_layer_boundaries.py``), and
``state/phase.py`` is one of its consumers, so it cannot live in ``tools/`` even
though ``tools/_delivery_journal_wiring.py`` owns the handle it carries.

Most delivery effects
are discrete statements in ``run_trw_deliver``, so they can hold the journal handle
directly. Three cannot: ``S02`` (the ceremony phase mirror inside
``update_run_phase``) and ``S06``/``S07`` (the acceptable-failure override ledger
and its event, inside the gate dispatcher). Both callees are reached from several
non-delivery paths too, so threading the handle through their signatures would put
a delivery concept into unrelated call chains.

This binds the active handle to a :class:`~contextvars.ContextVar` for the region
that owns it, and :func:`journal_step` opens a boundary against whatever is bound.
Outside a delivery — or on any path with no journal — ``journal_step`` yields
``True`` and records nothing, so ``update_run_phase`` called from ``trw_review``
behaves exactly as before.

There is deliberately no public getter for the bound handle. One shipped with
FR03 (``active_journal()``) and never acquired a caller: reading the handle out
of the region and using it elsewhere is precisely the leak ``bind_journal``'s
finally-restore exists to prevent, and a step recorded outside
:func:`journal_step` is a step with no boundary. Callers take the run/skip
boolean; the handle stays inside.

Thread scoping is a feature, not an accident: a new thread starts with a fresh
context, so the deferred batch's daemon thread never sees the synchronous
delivery's handle and cannot journal against it by mistake.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.tools._delivery_journal_wiring import DeliverJournal

_ACTIVE_JOURNAL: ContextVar[DeliverJournal | None] = ContextVar("trw_delivery_journal", default=None)

#: Effect ids whose crash boundary is open on THIS thread right now, innermost
#: last. Maintained by :meth:`DeliverJournal.step` for every boundary, bound or
#: not, so the FR05 input/output tracer can attribute an observed durable write to
#: the boundary that was open when it happened.
_OPEN_BOUNDARIES: ContextVar[tuple[str, ...]] = ContextVar("trw_delivery_open_boundaries", default=())

#: Finding code set by :func:`refuse_boundary` for the innermost open boundary.
#: A DECISION-shaped effect returns a verdict instead of raising, so the context
#: manager cannot tell "the call refused" from "the call succeeded" — without this
#: channel a refused gate override finalizes ``succeeded``, which is a lie the
#: resume path would then act on.
_BOUNDARY_REFUSALS: ContextVar[dict[str, str] | None] = ContextVar("trw_delivery_boundary_refusals", default=None)


def refuse_boundary(finding_code: str) -> None:
    """Record that the innermost open boundary's effect REFUSED (PRD-FIX-127 FR03).

    The enclosing :meth:`DeliverJournal.step` finalizes the step ``failed`` with
    this finding code instead of ``succeeded``. Use it for any effect whose
    wrapped call reports failure by RETURN VALUE rather than by raising: a step
    disposition must encode the business outcome, not merely "did the call raise".
    """
    boundaries = _OPEN_BOUNDARIES.get()
    if not boundaries:
        return
    _BOUNDARY_REFUSALS.set({**(_BOUNDARY_REFUSALS.get() or {}), boundaries[-1]: finding_code})


def take_refusal(effect_id: str) -> str:
    """Consume and return ``effect_id``'s refusal finding code, or ``""``."""
    refusals = _BOUNDARY_REFUSALS.get() or {}
    code = refusals.get(effect_id, "")
    if code:
        remaining = {k: v for k, v in refusals.items() if k != effect_id}
        _BOUNDARY_REFUSALS.set(remaining)
    return code


@contextmanager
def open_boundary(effect_id: str) -> Iterator[None]:
    """Mark ``effect_id``'s boundary open for the wrapped region (FR05 attribution)."""
    token = _OPEN_BOUNDARIES.set((*_OPEN_BOUNDARIES.get(), effect_id))
    try:
        yield
    finally:
        _OPEN_BOUNDARIES.reset(token)


def open_boundaries() -> tuple[str, ...]:
    """Every effect id whose boundary is open on this thread, innermost last."""
    return _OPEN_BOUNDARIES.get()


@contextmanager
def bind_journal(journal: DeliverJournal) -> Iterator[None]:
    """Make ``journal`` the ambient handle for the wrapped region, then restore.

    Always restores the previous binding, including on an exception, so a stale
    handle can never leak into a later tool call on the same thread — a leaked
    terminal operation would make every subsequent ``begin_step`` raise.
    """
    token = _ACTIVE_JOURNAL.set(journal)
    try:
        yield
    finally:
        _ACTIVE_JOURNAL.reset(token)


@contextmanager
def journal_step(effect_id: str) -> Iterator[bool]:
    """Open ``effect_id``'s boundary on the ambient journal, if there is one.

    Yields the same run/skip boolean :meth:`DeliverJournal.step` yields: ``False``
    only when a resumed delivery already proved this step succeeded. With no
    ambient journal it yields ``True`` and records nothing.
    """
    journal = _ACTIVE_JOURNAL.get()
    if journal is None:
        yield True
        return
    with journal.step(effect_id) as should_run:
        yield should_run
