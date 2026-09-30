"""A refusal never repeats what the caller sent (E2E-INC-125, the rule of E2E-VALIDATION-ERROR-ECHO-R2).

R2 closed pydantic's own validation errors. These are the refusals raised INSIDE tools, where a value or a caller's key
name was still quoted back to the client and into the server log.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from trw_mcp._refusal_echo import WITHHELD, key_name

_SECRET = "ghp_" + "a" * 36
_PASTE = "password=hunter2-super-secret"


def test_a_key_is_shown_only_when_it_is_identifier_shaped_short_and_clean() -> None:
    assert key_name("as_of") == "as_of"
    assert key_name("attempt-id.v2") == "attempt-id.v2"
    assert key_name(_SECRET) == WITHHELD
    assert key_name(_PASTE) == WITHHELD
    assert key_name("has space") == WITHHELD
    assert key_name("k" * 41) == WITHHELD
    assert key_name("") == WITHHELD


def test_a_malformed_as_of_is_refused_without_its_value() -> None:
    from trw_mcp.state._memory_recall import _parse_as_of

    with pytest.raises(ValueError, match="ISO-8601") as caught:
        _parse_as_of(_SECRET)

    assert _SECRET not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__, "the chained original holds the value"


def test_the_factory_gate_repeats_no_value_and_withholds_a_secret_shaped_key() -> None:
    from trw_mcp.state._factory_receipt_gate import factory_payload_refusal

    kind = factory_payload_refusal(json.dumps({"factory": 1, "kind": _SECRET, "attempt": "a"}))
    sha = factory_payload_refusal(
        json.dumps(
            {"factory": 1, "kind": "READY", "attempt": "a", "subject_sha": _SECRET, "receipts": {"build": ["build-x"]}}
        )
    )
    keys = factory_payload_refusal(json.dumps({"factory": 1, "kind": "START", "attempt": "a", _SECRET: 1, "nots": 2}))

    assert kind is not None and _SECRET not in kind and "START" in kind  # the accepted kinds are still named
    assert sha is not None and _SECRET not in sha and "40 lowercase hex" in sha
    assert keys is not None and _SECRET not in keys and WITHHELD in keys
    assert "nots" in keys, "an ordinary typo in a field name must stay visible"


def test_an_unknown_option_key_is_named_only_when_clean() -> None:
    from fastmcp.exceptions import ToolError

    from trw_mcp.tools._tool_options import RecallOptions, parse_options

    with pytest.raises(ToolError) as secret:
        parse_options(RecallOptions, {_SECRET: 1})
    with pytest.raises(ToolError) as typo:
        parse_options(RecallOptions, {"min_impac": 1})

    assert _SECRET not in str(secret.value) and WITHHELD in str(secret.value)
    assert "min_impac" in str(typo.value) and "Accepted keys" in str(typo.value)


def test_the_validation_middleware_withholds_a_secret_shaped_extra_key() -> None:
    from trw_mcp.middleware.validation_errors import value_free_message

    class Bag(BaseModel):
        model_config = ConfigDict(extra="forbid")

        known: int = 0

    with pytest.raises(ValidationError) as caught:
        Bag.model_validate({_SECRET: 1})

    message = value_free_message(caught.value)

    assert _SECRET not in message and WITHHELD in message
