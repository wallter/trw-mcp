"""PRD-CORE-347-FR03/NFR01: every vendored vector gives the result its name encodes."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.handoff._vectors import INVALID, STANDARD, VALID, expected_rule, handoff_for
from trw_mcp.handoff import AhrParseError, Finding, jcs, load, seal, validate
from trw_mcp.handoff._validate import MAX_CANONICAL_BYTES


def _check(path: Path) -> list[Finding]:
    try:
        doc = load(path)
    except AhrParseError as exc:
        return [exc.finding]
    hand = handoff_for(path)
    return validate(doc, load(hand) if hand else None)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_vector_conforms(path: Path) -> None:
    assert _check(path) == []


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_vector_fails_with_its_rule(path: Path) -> None:
    findings = _check(path)
    want = expected_rule(path)
    assert want in {f.rule for f in findings}, [f.as_dict() for f in findings]


def test_vector_set_is_vendored() -> None:
    assert len(VALID) >= 10
    assert len(INVALID) >= 80


def test_record_at_the_size_limit_validates_within_200ms() -> None:
    """NFR01: a record padded to just under 32 KiB (canonical) validates in < 200 ms."""
    doc = load(STANDARD)
    doc["extensions"]["https://example.org/ahr-ext/pad/1"] = {"pad": ""}
    doc["extensions"]["https://example.org/ahr-ext/pad/1"]["pad"] = "x" * (MAX_CANONICAL_BYTES - len(jcs(doc)) - 1)
    doc = seal(doc)
    assert MAX_CANONICAL_BYTES - 64 < len(jcs(doc)) <= MAX_CANONICAL_BYTES
    assert validate(doc) == []  # also warms the schema cache
    start = time.perf_counter()
    assert validate(doc) == []
    assert time.perf_counter() - start < 0.2


def test_record_over_the_size_limit_fails_x0() -> None:
    doc = load(STANDARD)
    doc["extensions"]["https://example.org/ahr-ext/pad/1"] = {"pad": "x" * MAX_CANONICAL_BYTES}
    doc = seal(doc)
    assert "X-0" in {f.rule for f in validate(doc)}
