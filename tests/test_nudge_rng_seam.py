"""Tests for the nudge RNG override seam (7.0.0 C14 determinism fix).

Production nudge variety uses ``random.SystemRandom()`` in both
``state._nudge_content`` (message text within a pool) and ``state._nudge_rules``
(weighted pool choice) — unseedable by design, so identical code produced
different nudge text run to run. ``scripts/_context_cost_probe.py`` overrides
both with a seeded ``random.Random`` so repeated context-cost measurements of
identical code are reproducible; production behavior is unchanged (still
``SystemRandom`` unless something calls ``set_rng``). These tests prove the
seam actually swaps the RNG used for selection, not just an attribute nobody
reads.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

import pytest

from trw_mcp.state import _nudge_content, _nudge_rules


@pytest.fixture(autouse=True)
def _restore_rngs() -> Iterator[None]:
    original_content, original_rules = _nudge_content._RNG, _nudge_rules._RNG
    yield
    _nudge_content.set_rng(original_content)
    _nudge_rules.set_rng(original_rules)


def test_nudge_content_set_rng_makes_pool_selection_reproducible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _nudge_content,
        "_load_pool_yaml",
        lambda pool: {"messages": [{"text": f"msg-{i}"} for i in range(20)]},
    )
    _nudge_content.set_rng(random.Random(42))
    first = [_nudge_content.load_pool_message("workflow") for _ in range(10)]
    _nudge_content.set_rng(random.Random(42))
    second = [_nudge_content.load_pool_message("workflow") for _ in range(10)]
    assert first == second
    assert len(set(first)) > 1  # exercises real selection variety, not a constant result


def test_nudge_content_set_rng_actually_replaces_the_module_rng() -> None:
    sentinel = random.Random(1)
    _nudge_content.set_rng(sentinel)
    assert _nudge_content._RNG is sentinel


def test_nudge_rules_set_rng_makes_weighted_choice_reproducible() -> None:
    _nudge_rules.set_rng(random.Random(7))
    first = [_nudge_rules._RNG.choices(["a", "b", "c"], weights=[1, 2, 3], k=1)[0] for _ in range(20)]
    _nudge_rules.set_rng(random.Random(7))
    second = [_nudge_rules._RNG.choices(["a", "b", "c"], weights=[1, 2, 3], k=1)[0] for _ in range(20)]
    assert first == second
    assert len(set(first)) > 1


def test_nudge_rules_set_rng_actually_replaces_the_module_rng() -> None:
    sentinel = random.Random(1)
    _nudge_rules.set_rng(sentinel)
    assert _nudge_rules._RNG is sentinel
