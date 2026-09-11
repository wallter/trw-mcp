"""Every key ``WalCheckpointResultDict`` declares must be one ``maybe_checkpoint_wal``
can actually return.

Why this exists (2026-09-11). The WAL payload was trimmed for the hot-path token
budget — ``wal_frames``, ``reclaimed_mb`` and ``truncate_state`` moved to the
``wal_checkpoint_complete`` structlog event — but the TypedDict kept declaring
all three, with paragraph-long comments describing what they meant.

``total=False`` is what made that invisible: it tells mypy any key may be
absent, so nothing complained that *no* key was ever present. A consumer reading
``result.get("truncate_state")`` got ``None`` on every call and could not
distinguish "the reset was never attempted" from "this field does not exist" —
the same we-checked-versus-we-never-checked collapse the checkpoint reporting
was being fixed for, re-committed one layer up in the type contract.

A release note then repeated the TypedDict's promise ("the payload gained
``wal_frames`` … and ``reclaimed_mb``"), which is how the drift was found: an
auditor traced the value to the sink instead of trusting the declaration.

So this gate is static and runs over the source: the declared key set must equal
the set of keys the implementation actually assigns. It fails in BOTH directions
— a field declared and never populated, and a field populated but undeclared.
"""

from __future__ import annotations

import ast
from pathlib import Path

from trw_mcp.models.typed_dicts import WalCheckpointResultDict

_IMPL = Path(__file__).resolve().parents[1] / "src/trw_mcp/state/_memory_lookups.py"
_FUNC = "maybe_checkpoint_wal"


def _assigned_keys() -> set[str]:
    """String-literal keys the function puts into a returned mapping.

    Covers both shapes the implementation uses: a ``return {...}`` / dict
    literal assigned to the annotated local, and later ``result_dict["k"] = ...``
    subscript writes for the conditional fields.
    """
    tree = ast.parse(_IMPL.read_text(encoding="utf-8"), filename=str(_IMPL))
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == _FUNC
    )
    keys: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            index = node.slice
            if isinstance(index, ast.Constant) and isinstance(index.value, str):
                keys.add(index.value)
    return keys


def test_every_declared_key_is_actually_populated() -> None:
    """The direction that actually broke: declared, documented, never set."""
    declared = set(WalCheckpointResultDict.__annotations__)
    orphans = declared - _assigned_keys()
    assert not orphans, (
        f"WalCheckpointResultDict declares {sorted(orphans)}, which {_FUNC} never assigns. "
        "A total=False TypedDict makes this invisible to mypy and the key silently reads as "
        "None forever. Either populate it or remove it from the declaration — and if it was "
        "moved to the structlog event, say so in the class docstring."
    )


def test_every_populated_key_is_declared() -> None:
    """The other direction: a real field the type contract does not admit, so a
    typed consumer cannot reach it without an ignore."""
    undeclared = _assigned_keys() - set(WalCheckpointResultDict.__annotations__)
    assert not undeclared, f"{_FUNC} returns undeclared key(s) {sorted(undeclared)}"


def test_the_key_extractor_is_not_vacuous() -> None:
    """Non-vacuity partner: if ``_assigned_keys`` silently returned nothing (a
    renamed function, a changed return shape), ``test_every_populated_key_is_declared``
    would pass trivially and the other test would fail with a confusing message
    naming every field at once."""
    keys = _assigned_keys()
    assert len(keys) > 5, f"key extraction found only {keys!r} — the parser has lost the return shape"
    # Anchor on the two fields whose distinction is the whole point of the row.
    assert {"backlog_cleared", "reclaimed"} <= keys


def test_the_trimmed_diagnostics_stay_out_of_the_payload() -> None:
    """Regression for the specific trim. These three are on the structlog event
    by deliberate choice (76 tokens against a 60-token hot-path budget); a future
    edit that re-adds them to the response must also re-raise that budget with a
    measurement, not slip them back in here."""
    declared = set(WalCheckpointResultDict.__annotations__)
    assert declared.isdisjoint({"wal_frames", "reclaimed_mb", "truncate_state"})
