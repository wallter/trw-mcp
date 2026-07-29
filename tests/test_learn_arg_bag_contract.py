"""The published contract for the ``metadata=`` / ``fields=`` argument bags.

WHY THIS FILE EXISTS. Collapsing rarely-set parameters into one object
parameter reclaims schema that every client pays for in every session, but it
moves the field names OUT of the machine-readable schema. Three things follow,
and each one is asserted below:

1. **The docstring is the only published contract.** A ``dict[str, object]``
   parameter serializes as a bare object; the calling model never sees
   ``nudge_line`` or ``phase_affinity`` in the schema. If the accepted-key list
   drifts out of the docstring, the bag has NO contract at all — not a schema,
   not prose — and the model is guessing keys against an ``extra="forbid"``
   validator that rejects them. That is strictly worse than the flat
   parameters the bag replaced.

2. **The text clients receive is not the source docstring, and it arrives in
   TWO places.** FastMCP parses docstrings with griffe: the tool-level
   ``description`` has the Google-style ``Args:`` block stripped out, but each
   ``Args:`` entry is re-bound to *that parameter's* JSON-Schema ``description``
   and IS published. So an accepted-key list is a real published contract in
   either position, and these tests deliberately assert against the UNION of
   the two rather than pinning one placement — a future move of the key list
   between the summary and ``Args:`` is a wording choice, not a contract break,
   and the test should not fight it. (``Output:``/``See Also:`` are different:
   they have no parameter to bind to, so they genuinely are lost below
   ``Args:`` and must stay above it.) Everything here is read from a real
   ``list_tools()`` call, never from ``__doc__``.

3. **The rejection error is the type system, delivered one round-trip late.**
   A free-form object parameter is ineligible for constrained decoding, so a
   typo cannot be prevented at sampling time — it can only be caught at
   validation time. The error must therefore name the accepted set so a caller
   can self-correct, and an unknown key must never be silently dropped: a
   learning stored with a quietly-discarded field looks stored and is wrong,
   and under Vision Principle 1 a poisoned learning compounds exactly as
   efficiently as a good one.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._learn_arg_bags import (
    LearnMetadata,
    LearnUpdateFields,
    parse_learn_metadata,
    parse_learn_update_fields,
)

pytestmark = pytest.mark.unit


async def _served_text(tool_name: str) -> str:
    """Return every word a client receives for ``tool_name``, whitespace-normalized.

    That is the tool-level description PLUS every parameter's schema
    ``description``, because griffe splits one docstring across both. Joining
    them is what makes these assertions placement-agnostic; normalizing
    whitespace makes them survive the re-wrapping griffe applies.
    """
    from trw_mcp.server._app import mcp

    for tool in await mcp._list_tools():
        dumped = tool.model_dump(exclude_none=True)
        if dumped.get("name") != tool_name:
            continue
        parts = [str(dumped.get("description") or "")]
        properties = (dumped.get("parameters") or {}).get("properties") or {}
        parts.extend(str(spec.get("description") or "") for spec in properties.values() if isinstance(spec, dict))
        return " ".join(" ".join(parts).split())
    raise AssertionError(f"{tool_name} is not registered")


@pytest.mark.parametrize(
    ("tool_name", "bag_model", "bag_param"),
    [
        ("trw_learn", LearnMetadata, "metadata"),
        ("trw_learn_update", LearnUpdateFields, "fields"),
    ],
)
async def test_every_accepted_bag_key_appears_in_what_the_client_receives(
    tool_name: str,
    bag_model: type[LearnMetadata] | type[LearnUpdateFields],
    bag_param: str,
) -> None:
    """The served text names every key the bag accepts — no silent drift.

    Adding a field to the model without documenting it makes that field
    undiscoverable: a dict-typed parameter publishes no field names in the
    schema by construction, so if the prose does not name it either, nothing
    does.
    """
    served = await _served_text(tool_name)
    missing = sorted(key for key in bag_model.model_fields if key not in served)
    assert not missing, (
        f"{tool_name}({bag_param}=...) accepts keys that nothing the client "
        f"receives ever names: {missing}. A dict-typed parameter publishes no "
        f"field names in the schema, so the prose is the only contract a calling "
        f"model can read. Document them in the summary or under Args: (either is "
        f"served), or remove them from the model."
    )


@pytest.mark.parametrize("tool_name", ["trw_learn", "trw_learn_update"])
async def test_the_client_is_told_that_unknown_bag_keys_are_rejected(
    tool_name: str,
) -> None:
    """The served text warns that the bag is strict.

    Without this, a caller reasonably assumes extras are ignored and discovers
    otherwise only when a whole learning is refused.
    """
    served = await _served_text(tool_name)
    assert "unknown keys are rejected" in served, (
        f"{tool_name} must state that unknown bag keys are rejected — a caller "
        f"who expects lenient extras will be surprised by a refused write. "
        f"Served text was: {served!r}"
    )


def test_unknown_metadata_key_is_rejected_and_the_error_names_the_accepted_set() -> None:
    """A typo fails loudly and the message is enough to self-correct."""
    values, rejection = parse_learn_metadata({"tpye": "incident"})
    assert rejection is not None, "an unknown metadata key must not be silently dropped"
    assert rejection["status"] == "rejected"
    message = str(rejection["message"])
    assert "tpye" in message, "the error must name the offending key"
    for accepted in LearnMetadata.model_fields:
        assert accepted in message, f"the error must list the accepted key {accepted!r}"
    # The rejected parse must not leak partially-applied caller values.
    assert values == LearnMetadata()


def test_unknown_update_field_is_rejected_with_the_update_error_shape() -> None:
    """``trw_learn_update`` rejects in its own documented shape, not trw_learn's.

    The two tools have different published output contracts; unifying them here
    would silently break every caller that branches on the result.
    """
    values, rejection = parse_learn_update_fields({"protection_teir": "high"})
    assert rejection is not None
    assert set(rejection) == {"error", "status"}
    assert rejection["status"] == "invalid"
    for accepted in LearnUpdateFields.model_fields:
        assert accepted in rejection["error"]
    assert values == LearnUpdateFields()


def test_a_valid_bag_round_trips_every_accepted_key() -> None:
    """Every documented key is actually settable — the list is not aspirational."""
    payload: dict[str, object] = {
        "source_identity": "impl-agent",
        "client_profile": "claude-code",
        "model_id": "opus-5",
        "consolidated_from": ["L-aaaa"],
        "assertions": [{"kind": "file_exists", "value": "src/x.py"}],
        "nudge_line": "check the shared index before committing",
        "task_type": "coding",
        "domain": ["mcp"],
        "phase_origin": "implement",
        "phase_affinity": ["implement"],
        "protection_tier": "high",
    }
    assert set(payload) == set(LearnMetadata.model_fields), (
        "this test must exercise every accepted key; update it alongside the model"
    )
    values, rejection = parse_learn_metadata(payload)
    assert rejection is None
    for key, expected in payload.items():
        assert getattr(values, key) == expected


def test_a_json_string_bag_is_accepted() -> None:
    """Clients that stringify structured arguments still land their values.

    fastmcp 3.2.4 does no JSON pre-parsing, so a client that sends the object as
    a string would otherwise have every rare field silently unset.
    """
    values, rejection = parse_learn_metadata('{"task_type": "coding", "domain": "mcp"}')
    assert rejection is None
    assert values.task_type == "coding"
    # A bare string for a list-typed key is the one mis-shape worth coercing.
    assert values.domain == ["mcp"]


def test_a_non_object_bag_is_rejected_rather_than_ignored() -> None:
    """A scalar or a malformed JSON string must not degrade to "no metadata"."""
    for bad in ("not json at all", "[1, 2, 3]", 7):
        _values, rejection = parse_learn_metadata(bad)  # type: ignore[arg-type]
        assert rejection is not None, f"{bad!r} must be rejected, not ignored"


def test_an_absent_bag_key_stays_the_partial_update_sentinel() -> None:
    """Omitting a key means "do not touch", never "clear it".

    ``model_fields_set`` distinguishes absence from an explicit null, which the
    flat signature could not express. Both map to None here on purpose:
    redefining an explicit null as "clear this field" would be a destructive
    behaviour change hiding inside a signature refactor.
    """
    values, rejection = parse_learn_update_fields({"task_type": "coding"})
    assert rejection is None
    assert "task_type" in values.model_fields_set
    assert values.domain is None
    assert "domain" not in values.model_fields_set

    explicit_null, rejection = parse_learn_update_fields({"domain": None})
    assert rejection is None
    assert explicit_null.domain is None
    assert "domain" in explicit_null.model_fields_set


def test_every_metadata_key_reaches_execute_learn() -> None:
    """An accepted key that is never forwarded is the poisoned-learning case.

    The bag validates and the call returns "recorded", so the caller believes
    the field landed. Nothing downstream ever sees it. That is worse than the
    typo case this module's ``extra="forbid"`` catches, because it is SILENT —
    and under Vision Principle 1 a learning that looks stored and is wrong
    compounds exactly as efficiently as a good one.

    Asserted against the model's own field list, so adding a field to
    ``LearnMetadata`` without wiring it through fails here rather than shipping.
    """
    import trw_mcp.tools._learn_impl as learn_impl
    from tests.conftest import extract_tool_fn, make_test_server

    captured: dict[str, object] = {}

    def _spy(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"learning_id": "L-test", "path": "sqlite://L-test", "status": "recorded"}

    original = learn_impl.execute_learn
    learn_impl.execute_learn = _spy  # type: ignore[assignment]
    try:
        learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
        learn_fn(
            summary="s",
            detail="d",
            metadata={
                "source_identity": "impl-agent",
                "client_profile": "claude-code",
                "model_id": "opus-5",
                "consolidated_from": ["L-aaaa"],
                "assertions": [{"kind": "file_exists", "value": "src/x.py"}],
                "nudge_line": "check the shared index before committing",
                "task_type": "coding",
                "domain": ["mcp"],
                "phase_origin": "implement",
                "phase_affinity": ["implement"],
                "protection_tier": "high",
            },
        )
    finally:
        learn_impl.execute_learn = original  # type: ignore[assignment]

    unforwarded = sorted(key for key in LearnMetadata.model_fields if key not in captured)
    assert not unforwarded, (
        f"metadata keys accepted by LearnMetadata but never passed to "
        f"execute_learn: {unforwarded}. The caller would be told 'recorded' "
        f"while the value was silently discarded."
    )
    assert captured["nudge_line"] == "check the shared index before committing"
    assert captured["domain"] == ["mcp"]
    assert captured["protection_tier"] == "high"
    # Provenance keys are explicit overrides here, so auto-detection must NOT
    # have replaced them.
    assert captured["client_profile"] == "claude-code"
    assert captured["model_id"] == "opus-5"


def test_omitted_provenance_keys_still_auto_detect() -> None:
    """Omitting client_profile/model_id must auto-detect, not send "".

    ``None`` is the auto-detect signal and ``""`` means "deliberately blank";
    collapsing the two would silently strip provenance from every learning
    written without an explicit profile.
    """
    import trw_mcp.tools._learn_impl as learn_impl
    from tests.conftest import extract_tool_fn, make_test_server

    captured: dict[str, object] = {}

    def _spy(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {"learning_id": "L-test", "path": "sqlite://L-test", "status": "recorded"}

    original = learn_impl.execute_learn
    learn_impl.execute_learn = _spy  # type: ignore[assignment]
    try:
        learn_fn = extract_tool_fn(make_test_server("learning"), "trw_learn")
        learn_fn(summary="s", detail="d")
    finally:
        learn_impl.execute_learn = original  # type: ignore[assignment]

    from trw_mcp.state.source_detection import detect_client_profile, detect_model_id

    assert captured["client_profile"] == detect_client_profile()
    assert captured["model_id"] == detect_model_id()
