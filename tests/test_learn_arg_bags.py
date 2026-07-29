"""End-to-end wiring for the ``trw_learn`` / ``trw_learn_update`` argument bags.

``test_learn_arg_bag_contract.py`` owns the PARSER-level contract (unknown-key
rejection, the two rejection shapes, JSON-string acceptance, bare-string list
coercion, the partial-update sentinel). This file deliberately does not repeat
any of that. It covers the half a parser test structurally cannot see:

    the parse can be perfect and the tool body can still drop the value.

PRD-CORE-234 collapsed 11 flat parameters per tool into one object. Each key now
has to survive an extra hop — bag -> model -> local variable -> forwarded kwarg —
and a key that stops being forwarded produces NO error at all: the learning is
stored, looks stored, and is missing a field. That is the "delivered but not
wired" defect class, and it is only observable from the live tool path with a
non-default input, which is what these tests use.

Both accepted wire forms (mapping and JSON string) are driven through the real
registered tool, because the ``dict | str`` union is applied at the tool boundary
rather than by fastmcp.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.tools._learn_arg_bags import LearnMetadata, LearnUpdateFields


def test_the_two_bags_are_not_interchangeable() -> None:
    """``trw_learn`` and ``trw_learn_update`` absorbed DIFFERENT name sets.

    trw_learn deliberately dropped expires/team_origin (0 of 9,229 stored
    entries carried them) and never took ``type`` in its bag, while
    trw_learn_update never took provenance. A later "simplification" that
    unified the two models would silently start accepting — and dropping —
    keys the owning tool has no code to forward, so the split is pinned here
    rather than left to a reviewer to notice.
    """
    metadata_keys = set(LearnMetadata.model_fields)
    update_keys = set(LearnUpdateFields.model_fields)

    assert {"expires", "team_origin", "type"} <= update_keys
    assert not ({"expires", "team_origin", "type"} & metadata_keys)
    assert {"source_identity", "client_profile", "model_id"} <= metadata_keys
    assert not ({"source_identity", "client_profile", "model_id"} & update_keys)


@pytest.mark.parametrize("as_json_string", [False, True])
def test_trw_learn_metadata_keys_reach_the_store_in_both_wire_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json_string: bool
) -> None:
    """Every value set in ``metadata`` arrives at the storage adapter.

    Values are all non-default, so a tool body that stopped forwarding one
    would fail here instead of coincidentally matching the field default.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    captured: dict[str, object] = {}

    def _store(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {
            "learning_id": str(kwargs.get("learning_id", "L-bag")),
            "path": "sqlite://L-bag",
            "status": "recorded",
            "distribution_warning": "",
        }

    monkeypatch.setattr("trw_mcp.tools.learning.adapter_store", _store)
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
    monkeypatch.setattr("trw_mcp.tools.learning.check_and_handle_dedup", lambda *a, **kw: None)
    monkeypatch.setattr("trw_mcp.tools.learning.save_learning_entry", lambda *a, **kw: tmp_path / "e.yaml")
    monkeypatch.setattr("trw_mcp.tools.learning.update_analytics", lambda *a, **kw: None)
    monkeypatch.setattr("trw_mcp.tools.learning.list_active_learnings", lambda *a, **kw: [])
    (tmp_path / ".trw" / "learnings" / "entries").mkdir(parents=True)

    payload: dict[str, object] = {
        "source_identity": "arg-bag-test",
        "client_profile": "codex",
        "model_id": "test-model-id",
        "nudge_line": "check the bag wiring",
        "task_type": "bug-fix",
        "domain": ["mcp"],
        "phase_origin": "IMPLEMENT",
        "phase_affinity": ["IMPLEMENT", "VALIDATE"],
        "protection_tier": "protected",
    }

    learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    result = learn_fn(
        summary="a durable arg-bag finding",
        detail="the bag keys must survive the trip to the store",
        metadata=json.dumps(payload) if as_json_string else payload,
    )

    assert result["status"] == "recorded", result
    for key, expected in payload.items():
        assert captured[key] == expected, f"metadata[{key!r}] did not reach the store"


@pytest.mark.parametrize("as_json_string", [False, True])
def test_trw_learn_update_fields_reach_the_adapter_in_both_wire_forms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json_string: bool
) -> None:
    """Every value set in ``fields`` arrives at the update adapter.

    Includes expires and team_origin specifically: they were removed from
    trw_learn, so trw_learn_update is now the ONLY way to set a TTL or correct
    ownership. If this forwarding breaks there is no other path.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    captured: dict[str, object] = {}

    def _update(_trw_dir: Path, **kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"learning_id": "L-bag", "changes": "updated", "status": "updated"}

    monkeypatch.setattr("trw_mcp.tools.learning.adapter_update", _update)
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")
    monkeypatch.setattr("trw_mcp.state.analytics.find_entry_by_id", lambda *a, **kw: None)
    monkeypatch.setattr("trw_mcp.state.analytics.resync_learning_index", lambda *a, **kw: None)

    payload: dict[str, object] = {
        "type": "incident",
        "confidence": "verified",
        "expires": "2026-12-31",
        "team_origin": "sprint-80",
        "nudge_line": "prefer the bag",
        "task_type": "bug-fix",
        "domain": ["mcp"],
        "phase_origin": "IMPLEMENT",
        "phase_affinity": ["IMPLEMENT"],
        "protection_tier": "protected",
    }

    update_fn = extract_tool_fn(make_test_server("learning"), "trw_learn_update")
    result = update_fn(
        learning_id="L-bag",
        fields=json.dumps(payload) if as_json_string else payload,
    )

    assert result["status"] == "updated", result
    for key, expected in payload.items():
        assert captured[key] == expected, f"fields[{key!r}] did not reach the adapter"


def test_trw_learn_rejects_an_unknown_metadata_key_at_the_tool_boundary() -> None:
    """The loud-failure invariant must hold through the REAL tool.

    A rejection the parser produces but the tool swallows is exactly the
    silent-drop the bag exists to prevent — under Vision Principle 1 a
    silently-dropped field is a poisoned learning.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
    result = learn_fn(
        summary="should never be stored",
        detail="carries a typo'd metadata key",
        metadata={"tpye": "incident"},
    )

    assert result["status"] == "rejected"
    assert result["reason"] == "invalid_metadata"
    assert "learning_id" not in result, "a rejected learning must not report an id"


def test_trw_learn_update_rejects_an_unknown_field_at_the_tool_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typo'd ``fields`` key must stop the update before the adapter runs."""
    from tests.conftest import extract_tool_fn, make_test_server

    calls: list[object] = []

    monkeypatch.setattr(
        "trw_mcp.tools.learning.adapter_update",
        lambda *a, **kw: calls.append(kw) or {"status": "updated", "learning_id": "L-bag"},
    )
    monkeypatch.setattr("trw_mcp.tools.learning.resolve_trw_dir", lambda: tmp_path / ".trw")

    update_fn = extract_tool_fn(make_test_server("learning"), "trw_learn_update")
    result = update_fn(learning_id="L-bag", fields={"protection_teir": "high"})

    assert result["status"] == "invalid"
    assert not calls, "the adapter must not be reached once the bag is rejected"
