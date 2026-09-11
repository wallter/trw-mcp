"""PRD-CORE-244-FR04 has a production caller, and it is the delivery gate.

Why this exists (2026-09-11, submission ``sub_MiY9DhQHvY56zEkC``).
``apply_contradiction_penalty`` was marked ``implemented`` and had **no
production caller at all**. PRD-CORE-244 shipped it on the recall verification
pass; PRD-CORE-268 then retired implicit verification on recall and removed that
call site, and nothing noticed — the function kept its tests, its config field,
its ``__all__`` entry and its re-export through two facades, so every surface
that looks like wiring was still present. Only the call was gone.

The user's underlying complaint stayed live the whole time: the bandit's only
reward was a uniform session-wide signal, so it optimised retrieval FREQUENCY,
which happily promotes a confidently-wrong memory that keeps matching the query.

The delivery gate is the right home. The recall objection was a latency budget,
and ``test_recall_verification_budget.py`` still asserts the penalty must never
run on recall — that test and this one are deliberately complementary, and a
future change that satisfies one by breaking the other should fail loudly.

These tests assert the WIRING, because the wiring is what was missing. The
penalty's own behaviour (cooldown, discount, reward value) is covered by
``test_memory_attribution_retirement.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[1] / "src/trw_mcp"
_GATE = _SRC / "tools/_delivery_helpers.py"
_NUDGE = _SRC / "tools/_retraction_nudge.py"
_PENALTY = "apply_contradiction_penalty"


def _called_names(path: Path) -> set[str]:
    """Every function name that is actually CALLED in the module.

    An import is not a call — the whole defect was a module that still imported
    and re-exported the function while nothing invoked it — so this looks for
    ``ast.Call`` nodes specifically.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)
    return names


def test_the_delivery_gate_calls_the_contradiction_penalty() -> None:
    """The regression this file exists for: remove the call and this fails."""
    assert _PENALTY in _called_names(_GATE), (
        f"{_PENALTY} has no caller in {_GATE.name}. PRD-CORE-244-FR04 is then "
        "'implemented' with nothing invoking it — the exact state submission "
        "sub_MiY9DhQHvY56zEkC reported. Either restore the call or mark FR04 "
        "reverted; do not leave the requirement claiming a signal it never emits."
    )


def test_it_is_fed_the_durable_but_FRESH_verdict() -> None:
    """The penalty must be driven by ``fresh_contradiction_ids``, not by the
    unbounded ``unsettled_contradiction_ids`` the advisory uses.

    Two separate properties, and the distinction is load-bearing:

    * DURABLE — the ids come from stored claim evidence, already persisted, so
      running this at delivery costs nothing and does not re-open the recall
      latency objection that PRD-CORE-268 removed the old call site over.
    * FRESH — bounded by ``verification_cache_ttl_seconds``. Without this the
      cooldown is one UTC day while the stored failure never expires, so an entry
      is re-penalised every day until someone retracts it, Q converges to 0, and
      ``learning_auto_prune_on_deliver`` (default True) nominates it obsolete.
      Found in pre-release review 2026-09-11; behaviour pinned by
      ``test_contradiction_penalty_safety.py::TestFreshnessBound``.
    """
    called = _called_names(_GATE)
    assert "fresh_contradiction_ids" in called, "the penalty must consume the freshness-bounded id list"
    assert "unretracted_contradiction_nudge" in called, "FR06's advisory must still run, unbounded by freshness"


def test_the_penalty_is_still_absent_from_the_recall_path() -> None:
    """Complement to ``test_recall_verification_budget.py``.

    FR04 belongs at delivery, NOT on recall. If a future change satisfies the
    wiring test above by calling the penalty from a recall module instead, this
    fails rather than letting the budget regression back in silently.
    """
    offenders = [
        path.relative_to(_SRC).as_posix()
        for path in _SRC.rglob("*.py")
        if "recall" in path.name and _PENALTY in _called_names(path)
    ]
    assert not offenders, f"{_PENALTY} must not be called on a recall path: {offenders}"


def test_the_id_source_is_shared_with_the_nudge() -> None:
    """FR04 and FR06 must read one traversal, not two.

    Two independent walks of the same entries can disagree — one naming an entry
    for retraction that the other did not penalise. ``unretracted_contradiction_nudge``
    therefore consumes ``unsettled_contradiction_ids`` rather than re-deriving it.
    """
    assert "unsettled_contradiction_ids" in _called_names(_NUDGE)


def test_the_call_detector_is_not_vacuous() -> None:
    """Non-vacuity partner. If ``_called_names`` silently returned an empty set
    (a parse change, a renamed file), every assertion above that checks for
    ABSENCE would pass trivially and the presence checks would fail with a
    confusing message."""
    gate_calls = _called_names(_GATE)
    assert len(gate_calls) > 10, f"call extraction found only {gate_calls!r}"
    assert "unretracted_contradiction_nudge" in gate_calls
