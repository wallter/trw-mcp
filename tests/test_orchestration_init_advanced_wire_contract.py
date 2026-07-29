"""The ``advanced`` bag's accepted keys must reach the model, not just the source.

WHY THIS TEST EXISTS. Collapsing seven flat parameters into one free-form object
removed them from the JSON Schema, where a calling model could previously read
their names and types. A free-form object is also ineligible for constrained
decoding (OpenAI strict mode requires ``additionalProperties: false``; Anthropic
strict tool use is grammar-constrained), so for a bagged parameter the SERVED
documentation plus the rejection error are the ENTIRE contract. If the key list
stops being served, callers guess keys against a validator that refuses them —
strictly worse than the flat parameters the collapse replaced.

THE MECHANISM THIS PINS. FastMCP drops free prose that sits below the
Google-style ``Args:`` header from the tool ``description`` (learning L-sgOm —
why ``Output:``/``See Also:`` had to move above ``Args:``). It does NOT drop the
``Args:`` entries themselves: each entry whose name matches a parameter is
hoisted into that parameter's JSON Schema ``description``, which is served to the
model as part of the tool definition. Verified at the wire 2026-07-27 for
``trw_init.advanced`` and ``trw_checkpoint.shard_id``/``wave_id``.

That hoisting is what publishes this contract, and it is silent when it breaks:
rename the ``Args:`` entry so it no longer matches the parameter, or move the key
list into free prose under ``Args:``, and the list vanishes from the wire with no
error anywhere. These tests read the SERVED tool definition — never the source
docstring — and derive the expected names from ``InitAdvanced.model_fields``, so
the published contract and the enforced one cannot drift apart.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._orchestration_init_advanced import (
    ADVANCED_KEYS,
    InitAdvanced,
    parse_init_advanced,
)

pytestmark = pytest.mark.anyio


async def _served_trw_init() -> tuple[str, dict[str, object]]:
    """Return the description and parameters schema the MCP client receives."""
    from trw_mcp.server._app import mcp

    for tool in await mcp._list_tools():
        if tool.name == "trw_init":
            return tool.description or "", tool.parameters
    raise AssertionError("trw_init is not registered on the server")


def _served_contract_text(description: str, parameters: dict[str, object]) -> str:
    """Concatenate every author-written string the model actually sees."""
    props = parameters.get("properties", {})
    assert isinstance(props, dict)
    param_docs = [str(p["description"]) for p in props.values() if isinstance(p, dict) and "description" in p]
    return "\n".join([description, *param_docs])


async def test_every_accepted_key_is_published_in_the_served_tool_definition() -> None:
    """Each key the validator accepts must be readable by the calling model.

    Derived from the model's own fields: adding a field to ``InitAdvanced``
    without documenting it fails here rather than shipping an unpublished key.
    """
    description, parameters = await _served_trw_init()
    served = _served_contract_text(description, parameters)
    missing = [name for name in InitAdvanced.model_fields if name not in served]
    assert not missing, (
        f"advanced keys accepted by the validator but NOT served to the model: {missing}. "
        "The list is published via the Args: entry hoisted into the parameter schema — "
        "check it still sits under an 'advanced:' entry whose name matches the parameter."
    )


async def test_the_advanced_parameter_carries_the_key_list_itself() -> None:
    """Pin the hoisting mechanism, not just the presence of the words somewhere.

    Without this, the previous test would still pass if the keys leaked into the
    main description while the parameter's own documentation went empty — which
    is the shape that costs description budget and drops on the next edit.
    """
    _description, parameters = await _served_trw_init()
    props = parameters.get("properties", {})
    assert isinstance(props, dict)
    advanced = props.get("advanced")
    assert isinstance(advanced, dict), "trw_init no longer exposes an 'advanced' parameter"
    doc = str(advanced.get("description", ""))
    assert doc, "the advanced parameter's Args: entry was dropped — the bag has no published contract"
    missing = [name for name in InitAdvanced.model_fields if name not in doc]
    assert not missing, f"advanced parameter documentation omits accepted keys: {missing}"


async def test_served_documentation_states_that_unknown_keys_are_refused() -> None:
    """A caller must learn the bag is closed BEFORE guessing a key.

    ``extra='forbid'`` is invisible in a schema that says
    ``additionalProperties: true``, so the refusal behavior has to be stated in
    the prose the model reads.
    """
    description, parameters = await _served_trw_init()
    served = _served_contract_text(description, parameters).lower()
    assert "rejected" in served or "refused" in served


def test_the_rejection_error_republishes_the_accepted_set() -> None:
    """The second half of the contract: a caller that guesses wrong is told the truth.

    This is the recovery path when the served list was missed, so it must name
    the same set the model was shown — both derive from ``ADVANCED_KEYS``.
    """
    from trw_mcp.exceptions import StateError

    with pytest.raises(StateError) as exc:
        parse_init_advanced({"task_roots": "docs"})
    message = str(exc.value)
    for name in ADVANCED_KEYS:
        assert name in message, f"rejection error omits accepted key {name!r}"


def test_accepted_keys_are_derived_from_the_model_never_hand_listed() -> None:
    """``ADVANCED_KEYS`` must track the model so the two cannot diverge."""
    assert set(ADVANCED_KEYS) == set(InitAdvanced.model_fields)
