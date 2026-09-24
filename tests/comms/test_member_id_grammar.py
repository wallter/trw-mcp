"""The member-id grammar is written once and reused (ledger RC-003).

It used to be spelled out identically in three places: ``_envelope.MEMBER_ID``,
``_peers_page._MEMBER_ID``, and inside ``_scope``'s shard-key regex. A grammar
change needed three synchronized edits, and a missed one would silently accept
or reject ids the other two did not. These tests fail on a reintroduced copy and
on a shard key that disagrees with the envelope about what a member id is.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trw_mcp.comms import _envelope, _peers_page, _scope

_LITERAL = "[A-Za-z0-9][A-Za-z0-9._-]{0,63}"


def test_every_site_uses_the_one_grammar() -> None:
    assert _peers_page.MEMBER_ID is _envelope.MEMBER_ID
    assert _envelope.MEMBER_ID.pattern == _envelope.MEMBER_ID_PATTERN
    assert _envelope.MEMBER_ID_PATTERN in _scope._SHARD_KEY.pattern


def test_no_module_spells_the_grammar_a_second_time() -> None:
    """A copy is the defect; the gate is on the source, so it catches one before it drifts."""
    package = Path(_envelope.__file__).parent
    offenders = {
        path.name: [
            node.lineno
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _LITERAL in node.value
        ]
        for path in sorted(package.glob("*.py"))
        if path.name != "_envelope.py"
    }
    assert not {name: lines for name, lines in offenders.items() if lines}


@pytest.mark.parametrize(
    ("member_id", "valid"),
    [
        ("worker-1", True),
        ("a", True),
        ("A0._-", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("", False),
        ("-leading", False),
        (".leading", False),
        ("has space", False),
        ("üñî", False),
    ],
)
def test_a_shard_key_admits_exactly_the_ids_the_envelope_admits(member_id: str, valid: bool) -> None:
    key = f"{_scope.SHARD}n{'0' * 32}{_scope.SHARD}{'0' * 16}{_scope.SHARD}{member_id}"
    assert bool(_envelope.MEMBER_ID.fullmatch(member_id)) is valid
    assert _scope.is_shard_key(key) is valid
