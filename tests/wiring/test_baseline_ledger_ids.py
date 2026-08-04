"""Finding 1 (2026-07-30 hardening pass): a baseline waiver must cite a real ledger row.

``BaselineEntry.__post_init__`` (``trw_mcp/wiring/baseline.py``) validates
``ledger_id`` for **regex shape only** — ``UF-\\d+`` / ``OQ-\\d+`` / ``DEAD-\\d+`` /
``PRD-[A-Z]+-\\d+`` — and is never cross-checked against
``docs/research/framework-simplification/DEFECT-LEDGER.md``. So
``BaselineEntry(key=<any finding's key>, ledger_id="UF-999999", rationale="...")``
constructs fine and permanently silences that finding, even though ``UF-999999``
exists nowhere. This module is the cross-check.

**Enforcement point: a dedicated test, not ``BaselineEntry.__post_init__``.**
Deliberate, for one structural reason: ``trw-mcp/pyproject.toml`` packages
``src/trw_mcp`` — including ``wiring/`` — onto PyPI (``pip install trw-mcp``),
but ``DEFECT-LEDGER.md`` is monorepo-internal dev documentation that is never
part of that package. ``BASELINE`` is also built at *import* time with no
``repo_root`` available (unlike ``build_registry(repo_root)``, every other
wiring entry point). Reading a monorepo-only path from ``__post_init__`` would
either crash every downstream install that imports ``trw_mcp.wiring.baseline``,
or need a silent except-and-continue fallback — which is exactly the fail-open
shape this finding is about. A test using the existing session-scoped
``repo_root`` fixture (``tests/wiring/conftest.py``, the same pattern
``test_self_check.py`` already uses) enforces the cross-check where the ledger
file is guaranteed to exist: this monorepo's own test run.

**Verified 2026-07-30**: both current ``BASELINE`` entries resolve.
``UF-031`` already did. ``PRD-CORE-231`` did **not** — zero matches anywhere in
the file, not as a row, not in prose — so the second waiver pointed at nothing
while passing ``_LEDGER_ID_RE``'s ``PRD-[A-Z]+-\\d+`` shape branch. That was
closed by adding the disposition the waiver always claimed to have
(``DEFECT-LEDGER.md`` §10, **UF-074**) and repointing the entry at it, with
PRD-CORE-231 kept in the rationale as the owning PRD.

``_KNOWN_UNRESOLVED_LEDGER_IDS`` is therefore empty. It is retained rather than
deleted because the ``xfail(strict=True)`` machinery around it is the honest way
to record a *future* gap: an id listed there fails loudly today and XPASS-fails
the moment the gap closes, so neither the gap nor its fix can land silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.wiring.baseline import BASELINE, BaselineEntry

LEDGER_RELATIVE_PATH = "docs/research/framework-simplification/DEFECT-LEDGER.md"

# Baseline ledger ids with a KNOWN, currently-unresolved gap against
# DEFECT-LEDGER.md. Exact-match discipline mirrors BASELINE's own design (the
# module docstring: "no --update-baseline flag exists, on purpose"): adding an
# id here is a source edit that shows up in a diff, and a stale entry (an id
# that no longer names a real gap) fails just as loudly as a missing one — see
# test_known_unresolved_set_has_no_stale_entries.
#
# Empty as of 2026-07-30: the one entry, ``PRD-CORE-231``, was resolved rather
# than tolerated. ``DEFECT-LEDGER.md`` §10 now carries **UF-074** — a real
# disposition row for the wiring-gate coverage decision — and the BASELINE entry
# cites that id, naming PRD-CORE-231 in its rationale as the owner. A waiver now
# points at a disposition instead of at a shape.
_KNOWN_UNRESOLVED_LEDGER_IDS: frozenset[str] = frozenset()

# Matches a DEFECT-LEDGER.md table row's leading ID cell: "| UF-031 |",
# "| ~~UF-001~~ |", "| **UF-045** |", "| ~~**UF-047**~~ |", "| Prior ID |"
# (header excluded — it has no digits). PRD-* never appears in this shape
# anywhere in the file today (confirmed by the xfail test below), so it is
# deliberately not part of the accepted row-id alternation: this parser only
# recognizes the forms the ledger's own maintenance protocol (§0) actually
# uses for a disposition-carrying row.
_ROW_ID_RE = re.compile(r"^\|\s*~{0,2}\*{0,2}(UF-\d+|OQ-\d+|DEAD-\d+)\*{0,2}~{0,2}\s*\|")


def _ledger_row_ids(repo_root: Path) -> frozenset[str]:
    text = (repo_root / LEDGER_RELATIVE_PATH).read_text(encoding="utf-8")
    ids: set[str] = set()
    for line in text.splitlines():
        match = _ROW_ID_RE.match(line)
        if match:
            ids.add(match.group(1))
    return frozenset(ids)


def test_ledger_parser_is_non_vacuous(repo_root: Path) -> None:
    """Guard the guard: a broken regex matching nothing must not silently pass every entry."""
    ids = _ledger_row_ids(repo_root)
    assert len(ids) >= 50, f"expected DEFECT-LEDGER.md to yield many row ids, got {len(ids)}"
    assert "UF-031" in ids


def _baseline_test_params() -> list[pytest.param]:
    params = []
    for entry in BASELINE:
        marks = []
        if entry.ledger_id in _KNOWN_UNRESOLVED_LEDGER_IDS:
            marks.append(
                pytest.mark.xfail(
                    strict=True,
                    reason=(
                        f"BaselineEntry(key={entry.key!r}, ledger_id={entry.ledger_id!r}) cites a "
                        f"ledger id that appears nowhere in {LEDGER_RELATIVE_PATH}. _LEDGER_ID_RE "
                        f"validates shape only, so the waiver is untracked by the document its own "
                        f"module docstring claims backs it. Remedy: add a disposition row, or "
                        f"repoint the entry at an id that already has one. strict=True means the "
                        f"day that row lands this XPASSes and CI fails until the marker and the id "
                        f"are removed together — the fix cannot land silently."
                    ),
                )
            )
        params.append(pytest.param(entry, id=entry.key, marks=marks))
    return params


@pytest.mark.parametrize("entry", _baseline_test_params())
def test_baseline_ledger_id_resolves_in_defect_ledger(entry: BaselineEntry, repo_root: Path) -> None:
    """Every ``BaselineEntry.ledger_id`` must be a real row in ``DEFECT-LEDGER.md``.

    Without this cross-check, a fabricated id like ``UF-999999`` passes
    ``_LEDGER_ID_RE``'s shape check and permanently silences whatever finding
    key it names.
    """
    ids = _ledger_row_ids(repo_root)
    assert entry.ledger_id in ids, (
        f"BaselineEntry(key={entry.key!r}).ledger_id={entry.ledger_id!r} is not a row in "
        f"{LEDGER_RELATIVE_PATH} — this waiver cites a disposition that does not exist"
    )


def test_known_unresolved_set_has_no_stale_entries() -> None:
    """An id that no longer names a real BASELINE entry must not linger as a silent exemption."""
    live_ledger_ids = {entry.ledger_id for entry in BASELINE}
    stale = _KNOWN_UNRESOLVED_LEDGER_IDS - live_ledger_ids
    assert not stale, (
        f"_KNOWN_UNRESOLVED_LEDGER_IDS names {sorted(stale)}, which no BASELINE entry "
        "currently cites — remove the stale entry (and its xfail) from this test module"
    )
