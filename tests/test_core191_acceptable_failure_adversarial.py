"""ROUND-2 HARDENING — adversarial battery for the acceptable-failure parser.

Surface: ``trw_mcp.tools._acceptable_failure_validation`` (PRD-CORE-191) +
``trw_mcp.models._acceptable_failure.AcceptableFailureRecord``.

Behavior contract (from the module docstrings): the parser is a fail-CLOSED
governance gate — an ``unverified_reason`` is accepted ONLY when it parses to a
structurally complete, unexpired, control-char-free record. Every malformed,
hostile, or ambiguous input must be cleanly REJECTED via ``(None, error)`` and
must never crash. The ledger writer is fail-OPEN (NFR02): an I/O fault is
swallowed, delivery is never blocked, but a malformed record must never reach it.

Every test asserts the SAFE behavior (clean rejection / safe accept), not mere
non-crash. Kept as regression tests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from trw_mcp.models._acceptable_failure import AcceptableFailureRecord
from trw_mcp.tools._acceptable_failure_validation import (
    ledger_run_id,
    parse_acceptable_failure,
)

_FAR_FUTURE = "2099-01-01"


def _valid(**over: object) -> str:
    base = {
        "failed_command": "pytest -q",
        "residual_risk": "two flaky tests; core verified",
        "owner": "agent-run-7",
        "expiry_iso": _FAR_FUTURE,
    }
    base.update(over)
    return json.dumps(base)


# --------------------------------------------------------------------------- #
# Expiry adversarial values — all must REJECT (never silently bypass).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "expiry",
    [
        "2026-13-45",  # impossible month/day
        "∞",  # unicode infinity
        "9999-99-99",
        "not-a-date",
        "2026/01/01",  # wrong separator
        "01-01-2026",  # wrong order
        "2026-1-1",  # date.fromisoformat is strict on zero-pad in <3.11? accept either way
        "",  # empty (min_length)
        "   ",  # whitespace-only
        "2026-02-30",  # nonexistent calendar day
    ],
)
def test_malformed_expiry_is_rejected(expiry: str) -> None:
    rec, err = parse_acceptable_failure(_valid(expiry_iso=expiry))
    assert rec is None
    assert err is not None
    # Either a schema/expiry error — never an accept.


def test_expiry_as_epoch_int_is_rejected() -> None:
    # JSON int expiry: json.loads keeps real int; schema requires str -> reject.
    payload = json.dumps({"failed_command": "x", "residual_risk": "y", "owner": "o", "expiry_iso": 4102444800})
    rec, err = parse_acceptable_failure(payload)
    assert rec is None
    assert err is not None and "schema required" in err


def test_expiry_as_list_is_rejected() -> None:
    payload = json.dumps({"failed_command": "x", "residual_risk": "y", "owner": "o", "expiry_iso": ["2099-01-01"]})
    rec, err = parse_acceptable_failure(payload)
    assert rec is None


def test_past_expiry_is_rejected() -> None:
    rec, err = parse_acceptable_failure(_valid(expiry_iso="2000-01-01"))
    assert rec is None
    assert err is not None and "expired" in err


# --------------------------------------------------------------------------- #
# Type-confusion: list / dict / null as scalar fields — all REJECT.
# --------------------------------------------------------------------------- #


def test_list_as_field_is_rejected() -> None:
    payload = json.dumps({"failed_command": ["a", "b"], "residual_risk": "y", "owner": "o", "expiry_iso": _FAR_FUTURE})
    rec, err = parse_acceptable_failure(payload)
    assert rec is None


def test_deeply_nested_field_is_rejected() -> None:
    nested: object = 1
    for _ in range(50):
        nested = {"x": nested}
    payload = json.dumps({"failed_command": nested, "residual_risk": "y", "owner": "o", "expiry_iso": _FAR_FUTURE})
    rec, err = parse_acceptable_failure(payload)
    assert rec is None


def test_json_null_field_is_rejected() -> None:
    payload = json.dumps({"failed_command": None, "residual_risk": "y", "owner": "o", "expiry_iso": _FAR_FUTURE})
    rec, err = parse_acceptable_failure(payload)
    assert rec is None


def test_yaml_null_field_is_rejected() -> None:
    # The YAML-subset path: a ``~``/null value must drop the key so the schema
    # rejects it as missing (not coerce to the literal string "None").
    text = "failed_command: ~\nresidual_risk: y\nowner: o\nexpiry_iso: 2099-01-01"
    rec, err = parse_acceptable_failure(text)
    assert rec is None


# --------------------------------------------------------------------------- #
# Control characters / unicode / encodings — REJECT (audit-injection guard).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "owner",
    [
        "ev\x00il",  # NUL — corrupts YAML ledger / breaks C string paths
        "evil\x00x",
        "be\x07ll",  # BEL
        "esc\x1b[31mred",  # ANSI escape (terminal/log injection)
        "unit\x1fsep",  # mid-string C0 control (US)
        "del\x7fchar",  # DEL
        "next\x85line",  # C1 NEL mid-string
    ],
)
def test_control_chars_in_owner_are_rejected(owner: str) -> None:
    rec, err = parse_acceptable_failure(_valid(owner=owner))
    assert rec is None, f"control-char owner {owner!r} must be rejected, not blessed"
    assert err is not None


@pytest.mark.parametrize("ws", ["tab\tmid", "line\nbreak", "carriage\rreturn"])
def test_embedded_whitespace_control_is_tolerated(ws: str) -> None:
    # Pinned contract (not a bug): tab/newline/CR are permitted mid-string —
    # ``residual_risk`` is legitimately multi-line prose, and both the YAML
    # ledger writer (quoting) and the structlog JSON renderer (escaping) handle
    # them safely. Only non-whitespace control bytes are an injection vector.
    rec, err = parse_acceptable_failure(_valid(residual_risk=ws))
    assert rec is not None and err is None


def test_control_chars_blocked_at_model_layer() -> None:
    with pytest.raises(ValueError, match="control characters"):
        AcceptableFailureRecord(failed_command="x", residual_risk="y", owner="a\x00b", expiry_iso=_FAR_FUTURE)


def test_unicode_printable_owner_is_accepted() -> None:
    # A legitimately non-ASCII owner (accented name) must still be accepted —
    # the guard targets control bytes, not all non-ASCII.
    rec, err = parse_acceptable_failure(_valid(owner="José Ortiz"))
    assert rec is not None and err is None
    assert rec.owner == "José Ortiz"


def test_windows1252_smart_quote_does_not_crash() -> None:
    # A latin-1/Windows-1252 smart-quote byte (0x93) is a printable unicode char
    # after decode; it must parse without crashing (accept is fine — printable).
    raw = b'{"failed_command":"x\x93y","residual_risk":"y","owner":"o","expiry_iso":"2099-01-01"}'
    rec, err = parse_acceptable_failure(raw.decode("latin-1"))
    assert (rec is not None) ^ (err is not None)  # exactly one populated; no crash


# --------------------------------------------------------------------------- #
# Oversized payloads + YAML structural attacks.
# --------------------------------------------------------------------------- #


def test_one_megabyte_field_does_not_crash() -> None:
    rec, err = parse_acceptable_failure(_valid(failed_command="A" * 1_000_000))
    # 1MB of printable 'A' is a structurally valid (if absurd) record.
    assert rec is not None and err is None


def test_duplicate_yaml_keys_are_rejected_not_crash() -> None:
    # ruamel's safe loader raises DuplicateKeyError; the parser must swallow it
    # and fall through to a clean schema-required rejection (no traceback escape).
    text = "failed_command: a\nfailed_command: b\nresidual_risk: y\nowner: o\nexpiry_iso: 2099-01-01"
    rec, err = parse_acceptable_failure(text)
    assert rec is None
    assert err is not None and "schema required" in err


def test_yaml_anchor_alias_bomb_is_bounded() -> None:
    # billion-laughs style anchor expansion. ruamel's safe loader must not blow
    # up CPU/memory; bound with a generous timeout and assert clean rejection.
    bomb = "\n".join(
        [
            "a: &a [x,x,x,x,x,x,x,x,x]",
            "b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]",
            "c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]",
            "d: &d [*c,*c,*c,*c,*c,*c,*c,*c,*c]",
            "e: &e [*d,*d,*d,*d,*d,*d,*d,*d,*d]",
            "failed_command: ok",
        ]
    )
    start = time.monotonic()
    rec, err = parse_acceptable_failure(bomb)
    assert time.monotonic() - start < 5.0, "YAML anchor expansion was not bounded"
    assert rec is None  # incomplete record -> rejected


def test_yaml_anchor_alias_pair_is_handled() -> None:
    text = "owner: &o agent-7\nfailed_command: x\nresidual_risk: y\nexpiry_iso: 2099-01-01"
    rec, err = parse_acceptable_failure(text)
    # &o on a scalar is just an anchor with no alias reference -> valid record.
    assert rec is not None and err is None


# --------------------------------------------------------------------------- #
# Empty / non-dict roots — REJECT.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "WIP", "[]", "[1,2,3]", "42", "null", "true"])
def test_non_dict_or_prose_roots_are_rejected(text: str) -> None:
    rec, err = parse_acceptable_failure(text)
    assert rec is None
    assert err is not None


# --------------------------------------------------------------------------- #
# ledger_run_id — filename safety (path traversal / NUL must be neutralized).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name,expected_safe",
    [
        ("clean-run_1", "clean-run_1"),
        ("../../../etc", None),  # traversal dots replaced
        ("a/b/c", None),  # separators replaced
        ("evil\x00name", None),  # NUL replaced
        ("..", None),
    ],
)
def test_ledger_run_id_is_filesystem_safe(name: str, expected_safe: str | None) -> None:
    out = ledger_run_id(Path(name))
    # No path separators, no NUL, no bare-dot traversal token survives.
    assert "/" not in out
    assert "\\" not in out
    assert "\x00" not in out
    assert out != ".." and ".." not in out.replace("-", "")
    if expected_safe is not None:
        assert out == expected_safe


def test_ledger_run_id_none_is_no_run() -> None:
    assert ledger_run_id(None) == "no-run"
