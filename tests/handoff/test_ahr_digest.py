"""PRD-CORE-347-FR02: RFC 8785 bytes and the AHR digest agree with the spec's pinned digests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.handoff._vectors import INVALID, VALID
from trw_mcp.handoff import JcsError, digest, jcs, load


def _sealed(paths: list[Path]) -> list[Path]:
    sealed = [p for p in paths if "integrity" in load(p)]
    assert len(sealed) >= 5
    return sealed


@pytest.mark.parametrize("path", _sealed(VALID), ids=lambda p: p.name)
def test_digest_matches_pinned_integrity(path: Path) -> None:
    doc = load(path)
    assert digest(doc) == doc["integrity"]["digest"]


def test_readback_binds_to_handoff_digest() -> None:
    vectors = {p.name: load(p) for p in VALID}
    assert vectors["02-readback-for-01.json"]["handoff"]["digest"] == digest(vectors["01-standard-handoff.json"])
    assert vectors["07-readback-for-04-critical.json"]["handoff"]["digest"] == digest(
        vectors["04-critical-supersedes-01.json"]
    )


def test_astral_key_vector_is_sealed() -> None:
    doc = load(next(p for p in VALID if p.name.startswith("11-")))
    assert digest(doc) == doc["integrity"]["digest"]


def test_keys_order_by_utf16_code_units_not_code_points() -> None:
    # U+1F600 encodes as D83D DE00 in UTF-16, which sorts before U+E000;
    # by code point it would sort after.
    value = {"\ue000": 1, "\U0001f600": 2, "a": 3}
    assert jcs(value) == '{"a":3,"\U0001f600":2,"\ue000":1}'.encode()
    assert sorted(value) != ["a", "\U0001f600", "\ue000"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"b": 1, "a": [True, None, "\u00e9"]}, '{"a":[true,null,"\u00e9"],"b":1}'),
        ({"s": '\u001f\n"\\/'}, '{"s":"\\u001f\\n\\"\\\\/"}'),
        ([], "[]"),
        ({"n": -(2**53 - 1)}, '{"n":-9007199254740991}'),
    ],
)
def test_known_rfc8785_vectors(value: object, expected: str) -> None:
    assert jcs(value) == expected.encode()


@pytest.mark.parametrize("bad", [1.5, 2**53, "\ud800", {"\udc00": 1}])
def test_r_int_3_and_4_violations_raise(bad: object) -> None:
    with pytest.raises(JcsError, match="X-0"):
        jcs(bad)


def test_digest_ignores_integrity() -> None:
    doc = load(INVALID[0])
    stripped = {k: v for k, v in doc.items() if k != "integrity"}
    assert digest(doc) == digest(stripped)
