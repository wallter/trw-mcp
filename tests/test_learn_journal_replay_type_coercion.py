"""Replay must be EQUIVALENT to the original `trw_learn` call (PRD-INFRA-171 FR06).

Observed 2026-07-25 by running the real operator drain against real pending records:
6 of 9 were RETAINED rather than recovered. The store raised
``ValueError: 'gotcha' is not a valid MemoryType``.

Root cause: `trw_learn` coerces advertised type aliases at the TOOL layer
(`_learning_module_helpers._coerce_learn_type`) before enum validation, but
`replay_journaled_learn` calls `execute_learn` directly and skipped that step.
Because the D8 contract RETAINS a record on a store error, such a record failed
identically on every future drain — permanent journal poison that could never be
consumed, silently capping the journal's usable capacity.

These tests assert the equivalence property, not the specific alias, so adding a
new alias to `_LEARN_TYPE_ALIASES` cannot silently regress the replay path.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._learn_journal_wiring import JOURNAL_ARG_KEYS
from trw_mcp.tools._learning_module_helpers import _LEARN_TYPE_ALIASES, _coerce_learn_type


@pytest.mark.unit
def test_every_advertised_alias_coerces_to_a_valid_memory_type() -> None:
    """The alias table must only map to values the storage enum accepts.

    If this fails, some alias is unreplayable-by-construction: the tool layer
    would accept it and the store would reject it.
    """
    from trw_memory.models.memory import MemoryType

    valid = {m.value for m in MemoryType}
    assert _LEARN_TYPE_ALIASES, "alias table is empty — the coercion seam would be vacuous"
    for alias, resolved in _LEARN_TYPE_ALIASES.items():
        assert resolved in valid, f"alias {alias!r} maps to {resolved!r}, which is not a MemoryType"


@pytest.mark.unit
def test_replay_applies_the_same_type_coercion_as_the_tool_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """`replay_journaled_learn` must coerce `type` exactly as `trw_learn` does.

    Captures the kwargs handed to `execute_learn` instead of asserting on a
    stored row, so the property is proven at the seam and needs no backend.
    """
    from trw_mcp.tools import _learn_journal_wiring as wiring

    captured: dict[str, object] = {}

    def _fake_execute_learn(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"status": "recorded"}

    monkeypatch.setattr("trw_mcp.tools._learn_impl.execute_learn", _fake_execute_learn)

    alias = next(iter(_LEARN_TYPE_ALIASES))
    payload: dict[str, object] = {"summary": "s", "detail": "d", "type": alias}
    wiring.replay_journaled_learn(object(), object(), "L-test", payload)  # type: ignore[arg-type]

    assert captured["type"] == _coerce_learn_type(alias), (
        f"replay passed type={captured['type']!r} for alias {alias!r}; "
        f"the tool layer would have passed {_coerce_learn_type(alias)!r}"
    )
    from trw_memory.models.memory import MemoryType

    assert captured["type"] in {m.value for m in MemoryType}


@pytest.mark.unit
def test_replay_leaves_an_already_valid_type_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coercion must be identity for a real MemoryType — no silent remapping."""
    from trw_mcp.tools import _learn_journal_wiring as wiring

    captured: dict[str, object] = {}

    def _fake_execute_learn(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"status": "recorded"}

    monkeypatch.setattr("trw_mcp.tools._learn_impl.execute_learn", _fake_execute_learn)
    wiring.replay_journaled_learn(  # type: ignore[arg-type]
        object(), object(), "L-test", {"summary": "s", "detail": "d", "type": "pattern"}
    )
    assert captured["type"] == "pattern"


@pytest.mark.unit
def test_type_is_a_journaled_arg_so_the_coercion_seam_is_reachable() -> None:
    """Guard: if `type` ever leaves JOURNAL_ARG_KEYS the coercion becomes dead code."""
    assert "type" in JOURNAL_ARG_KEYS


@pytest.mark.unit
def test_journal_payload_round_trips_from_raw_tool_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """The payload the system WRITES must be one replay can consume (agy review, 2026-07-25).

    The original defect survived 32 passing tests because each side was tested
    against its own idea of a payload: the tool path against raw args, replay
    against an already-normalized dict. Nothing forced the ACTUAL serialized
    record across the replay boundary.

    This asserts the round-trip property directly: capture what
    `capture_journal_payload` produces for a call carrying a raw alias, then
    require replay to accept it and hand the store a valid MemoryType. It fails
    if the write side and the replay side ever disagree again, whatever the
    disagreeing field is.
    """
    from trw_memory.models.memory import MemoryType

    from trw_mcp.tools import _learn_journal_wiring as wiring
    from trw_mcp.tools._learn_journal_wiring import capture_journal_payload

    alias = next(iter(_LEARN_TYPE_ALIASES))
    # The shape execute_learn's locals take for a call that came in with an alias
    # and was NOT coerced upstream (a hand-written record, an older client, or a
    # future caller that bypasses the tool layer).
    payload = capture_journal_payload({"summary": "s", "detail": "d", "type": alias, "impact": 0.5})
    assert payload["type"] == alias, "fixture precondition: the raw alias must survive capture"

    captured: dict[str, object] = {}

    def _fake_execute_learn(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"status": "recorded"}

    monkeypatch.setattr("trw_mcp.tools._learn_impl.execute_learn", _fake_execute_learn)
    wiring.replay_journaled_learn(object(), object(), "L-rt", payload)  # type: ignore[arg-type]

    assert captured["type"] in {m.value for m in MemoryType}, (
        f"replay handed the store type={captured['type']!r}, which the storage enum rejects — "
        "the journal write side and the replay side have diverged"
    )
