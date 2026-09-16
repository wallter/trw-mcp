"""PRD-CORE-275-FR03/FR04/NFR01: canonical bodies, digests, closed schema.

The digest is the shared identifier two harnesses agree on without talking, so
these tests are about AGREEMENT, not formatting: same fields must give the same
bytes from any process, in any input order, and any change must move the digest.
"""

from __future__ import annotations

import json

import pytest

from trw_mcp.plan import (
    PlanError,
    PlanRefusal,
    build_proposal,
    build_review,
    canonical_bytes,
    digest_of,
    encode,
    parse_proposal,
    parse_review,
)

PID = "a" * 32


def _proposal(**over: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "plan_id": PID,
        "revision": 1,
        "paths": ["src/b.py", "src/a.py"],
        "test_paths": ["tests/test_a.py"],
        "summary": "do the thing",
    }
    fields.update(over)
    return build_proposal(**fields)  # type: ignore[arg-type]


def test_input_order_cannot_change_the_digest() -> None:
    """The contradiction an earlier draft carried, resolved by normalizing."""
    first = build_proposal(plan_id=PID, revision=1, paths=["b.py", "a.py"], test_paths=[], summary="s")
    second = build_proposal(plan_id=PID, revision=1, paths=["a.py", "b.py"], test_paths=[], summary="s")

    assert first == second
    assert first["paths"] == ["a.py", "b.py"]


def test_duplicate_paths_are_collapsed_before_digesting() -> None:
    body = build_proposal(plan_id=PID, revision=1, paths=["a.py", "a.py"], test_paths=[], summary="s")
    assert body["paths"] == ["a.py"]


def test_canonical_bytes_do_not_depend_on_key_insertion_order() -> None:
    forward = canonical_bytes({"a": 1, "b": 2})
    backward = canonical_bytes({"b": 2, "a": 1})
    assert forward == backward


@pytest.mark.parametrize("field", ["plan_id", "revision", "summary"])
def test_any_field_change_moves_the_digest(field: str) -> None:
    changed = {"plan_id": "b" * 32, "revision": 2, "summary": "other"}[field]
    assert _proposal()["digest"] != _proposal(**{field: changed})["digest"]


def test_digest_excludes_itself() -> None:
    """Otherwise a receiver could never recompute it."""
    body = _proposal()
    assert digest_of(body) == body["digest"]
    assert digest_of({**body, "digest": "tampered"}) == body["digest"]


def test_a_round_trip_verifies() -> None:
    assert parse_proposal(encode(_proposal()))["plan_id"] == PID


def test_a_tampered_body_is_refused() -> None:
    body = _proposal()
    body["summary"] = "changed after digesting"

    with pytest.raises(PlanError) as excinfo:
        parse_proposal(encode(body))

    assert excinfo.value.refusal is PlanRefusal.DIGEST_MISMATCH


def test_an_unsorted_but_honest_body_still_verifies() -> None:
    """Normalization happens before comparison, so presentation is not a refusal."""
    body = _proposal()
    raw = json.loads(encode(body))
    raw["paths"] = list(reversed(raw["paths"]))

    assert parse_proposal(json.dumps(raw))["digest"] == body["digest"]


@pytest.mark.parametrize(
    ("mutate", "refusal"),
    [
        ({"extra": 1}, PlanRefusal.UNKNOWN_KEY),
        ({"schema": "other.v1"}, PlanRefusal.WRONG_SCHEMA),
        ({"type": "review"}, PlanRefusal.WRONG_TYPE),
        ({"plan_id": "NOTHEX"}, PlanRefusal.BAD_PLAN_ID),
        ({"revision": 0}, PlanRefusal.BAD_REVISION),
        ({"revision": True}, PlanRefusal.BAD_REVISION),
        ({"paths": "src/a.py"}, PlanRefusal.WRONG_FIELD_TYPE),
        ({"paths": ["/abs/a.py"]}, PlanRefusal.ABSOLUTE_PATH),
        ({"paths": ["../out.py"]}, PlanRefusal.TRAVERSAL_PATH),
        ({"paths": [""]}, PlanRefusal.EMPTY_PATH),
        ({"summary": "x" * 2000}, PlanRefusal.TOO_LARGE),
        ({"paths": [f"p{n}.py" for n in range(200)]}, PlanRefusal.TOO_MANY),
        ({"approved": True}, PlanRefusal.AUTHORITY_FIELD),
    ],
)
def test_each_malformed_shape_has_its_own_reason(mutate: dict[str, object], refusal: PlanRefusal) -> None:
    """One distinct reason per shape: a single 'invalid' would be unactionable."""
    raw = json.loads(encode(_proposal()))
    raw.update(mutate)

    with pytest.raises(PlanError) as excinfo:
        parse_proposal(json.dumps(raw))

    assert excinfo.value.refusal is refusal


def test_missing_key_is_distinct_from_unknown_key() -> None:
    raw = json.loads(encode(_proposal()))
    del raw["summary"]
    with pytest.raises(PlanError) as excinfo:
        parse_proposal(json.dumps(raw))
    assert excinfo.value.refusal is PlanRefusal.MISSING_KEY


@pytest.mark.parametrize("raw", ["not json", "[1,2]", '"a string"', "null"])
def test_non_object_bodies_refuse(raw: str) -> None:
    with pytest.raises(PlanError) as excinfo:
        parse_proposal(raw)
    assert excinfo.value.refusal in {PlanRefusal.NOT_JSON, PlanRefusal.NOT_OBJECT}


def test_a_review_round_trips_and_carries_no_digest_of_its_own() -> None:
    review = build_review(plan_id=PID, revision=1, digest="d" * 64, findings=["a finding"])
    assert parse_review(encode(review))["findings"] == ["a finding"]


def test_refusals_never_leak_a_traceback_or_an_absolute_path() -> None:
    """NFR01: this text is read by another agent."""
    raw = json.loads(encode(_proposal()))
    raw["paths"] = ["/etc/shadow"]

    with pytest.raises(PlanError) as excinfo:
        parse_proposal(json.dumps(raw))

    rendered = str(excinfo.value)
    assert "Traceback" not in rendered
    # The refused path is echoed because it is the CALLER's own input, which is
    # what makes the refusal actionable; nothing about this machine leaks.
    assert "/home/" not in rendered and "Error" not in rendered
