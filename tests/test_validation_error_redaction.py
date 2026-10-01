"""E2E-VALIDATION-ERROR-ECHO: a schema-invalid tool call never echoes a secret back in the error text.

fastmcp/pydantic put the raw ``input_value`` of a failed field into the error message, so a secret pasted into
the wrong argument landed in the client transcript. The MCP error boundary now redacts it.
"""

from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

_KEY = "sk-ant-api03-" + "Zq7" * 30 + "-AbCdEf"  # an Anthropic-shaped key, never a real one


async def _call(name: str, arguments: dict[str, object]) -> str:
    from trw_mcp.server._app import create_app
    from trw_mcp.server._tools import _tool_registrars

    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as client:
        with pytest.raises(ToolError) as raised:
            await client.call_tool(name, arguments)
    return str(raised.value)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        pytest.param("trw_checkpoint", {"heartbeat": _KEY}, id="str-in-a-bool-field"),
        pytest.param("trw_checkpoint", {"message": "m", "blocked_decision": _KEY}, id="str-in-an-object-field"),
    ],
)
async def test_a_secret_in_an_invalid_field_is_not_echoed_in_the_error(tool: str, arguments: dict[str, object]) -> None:
    text = await _call(tool, arguments)
    assert _KEY not in text
    assert "sk-ant-api03" not in text
    field = next(iter(k for k in arguments if arguments[k] == _KEY))
    assert field in text  # the caller still learns WHICH argument was wrong


async def test_the_server_log_warning_carries_no_input_value(caplog: pytest.LogCaptureFixture) -> None:
    """fastmcp also logs 'Invalid arguments for tool' with each error's raw input; that copy is stripped too."""
    import logging

    fastmcp_logger = logging.getLogger("fastmcp.server.server")
    propagate = fastmcp_logger.propagate
    fastmcp_logger.propagate = True  # fastmcp may stop propagation to root; caplog listens on root
    try:
        with caplog.at_level(logging.WARNING, logger="fastmcp.server.server"):
            await _call("trw_checkpoint", {"heartbeat": _KEY})
    finally:
        fastmcp_logger.propagate = propagate
    warnings = [r.getMessage() for r in caplog.records if "Invalid arguments for tool" in r.getMessage()]
    assert warnings, "fastmcp's warning was captured"
    assert all("sk-ant-api03" not in w for w in warnings)
    assert all("heartbeat" in w and "bool_parsing" in w for w in warnings)


def test_a_validation_error_without_a_pydantic_cause_is_withheld() -> None:
    import asyncio

    from fastmcp.exceptions import ValidationError as FastMCPValidationError

    from trw_mcp.middleware.validation_errors import ValidationErrorRedactionMiddleware

    async def call_next(_context: object) -> object:
        raise FastMCPValidationError(f"bad value {_KEY}")

    with pytest.raises(ToolError) as raised:
        asyncio.run(ValidationErrorRedactionMiddleware().on_call_tool(object(), call_next))  # type: ignore[arg-type]
    assert "sk-ant-api03" not in str(raised.value)
    assert raised.value.__cause__ is None


def test_a_secret_used_as_a_dict_key_in_the_error_path_is_redacted() -> None:
    from pydantic import BaseModel

    from trw_mcp.middleware.validation_errors import value_free_message

    class Args(BaseModel):
        options: dict[str, int]

    try:
        Args.model_validate({"options": {_KEY: "not-an-int"}})
    except Exception as exc:  # pydantic.ValidationError
        text = value_free_message(exc)  # type: ignore[arg-type]
    assert "sk-ant-api03" not in text
    assert text.splitlines()[1].startswith("options.<key>: ")
    assert "int_parsing" in text


def test_the_log_filter_drops_values_and_redacts_a_secret_dict_key() -> None:
    import logging

    from trw_mcp.middleware.validation_errors import _DropInputValues

    detail = [{"type": "int_parsing", "loc": ("options", _KEY), "msg": "bad", "input": _KEY, "ctx": {"x": _KEY}}]
    record = logging.LogRecord(
        "fastmcp.server.server", logging.WARNING, "f", 1, "Invalid arguments for tool %r: %s", ("t", detail), None
    )
    assert _DropInputValues().filter(record)
    text = record.getMessage()
    assert "sk-ant-api03" not in text
    assert "int_parsing" in text
    assert "'options'" in text


def test_a_custom_validator_message_quoting_the_input_is_dropped_not_redacted() -> None:
    """VALIDATION-ECHO-VALIDATOR-MSG: a validator's message can quote a personal value no detector recognises."""
    from pydantic import BaseModel, field_validator

    from trw_mcp.middleware.validation_errors import _value_free_entry, value_free_message

    class Args(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def _check(cls, value: str) -> str:
            raise ValueError(f"unknown name {value!r}")

    for person in (_KEY, "Jane Q. Public"):
        try:
            Args.model_validate({"name": person})
        except Exception as exc:  # pydantic.ValidationError
            text = value_free_message(exc)  # type: ignore[arg-type]
            entry = _value_free_entry(exc.errors()[0])  # type: ignore[attr-defined]
        for shown in (text, str(entry)):
            assert person not in shown
            assert "unknown name" not in shown
            assert "invalid value" in shown
        assert "name: invalid value [type=value_error]" in text  # path and type stay, so the caller can find the field
        assert entry["type"] == "value_error"


def test_an_assertion_message_is_dropped_too() -> None:
    from pydantic import BaseModel, field_validator

    from trw_mcp.middleware.validation_errors import value_free_message

    class Args(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def _check(cls, value: str) -> str:
            assert value == "x", f"{value} is not allowed"
            return value

    try:
        Args.model_validate({"name": "Jane Q. Public"})
    except Exception as exc:  # pydantic.ValidationError
        text = value_free_message(exc)  # type: ignore[arg-type]
    assert "Jane" not in text
    assert "name: invalid value [type=assertion_error]" in text


def test_a_builtin_message_that_quotes_nothing_is_kept() -> None:
    from pydantic import BaseModel

    from trw_mcp.middleware.validation_errors import value_free_message

    class Args(BaseModel):
        count: int

    try:
        Args.model_validate({"count": "Jane Q. Public"})
    except Exception as exc:  # pydantic.ValidationError
        text = value_free_message(exc)  # type: ignore[arg-type]
    assert "Jane" not in text
    assert "count: Input should be a valid integer" in text
    assert "[type=int_parsing]" in text


def test_a_personal_dict_key_is_rendered_as_a_placeholder_in_both_the_message_and_the_log() -> None:
    """VALIDATION-ECHO-KEY-PERSONAL: an identifier-shaped personal key passes the secret detector, so it is never shown."""
    import logging

    from pydantic import BaseModel

    from trw_mcp.middleware.validation_errors import _DropInputValues, value_free_message

    class Args(BaseModel):
        options: dict[str, int]

    try:
        Args.model_validate({"options": {"jane_public": "not-an-int"}})
    except Exception as exc:  # pydantic.ValidationError
        text = value_free_message(exc)  # type: ignore[arg-type]
    assert "jane_public" not in text
    assert text.splitlines()[1].startswith("options.<key>: ")

    detail = [{"type": "int_parsing", "loc": ("options", "jane_public"), "msg": "bad", "input": "x"}]
    record = logging.LogRecord(
        "fastmcp.server.server", logging.WARNING, "f", 1, "Invalid arguments for tool %r: %s", ("t", detail), None
    )
    assert _DropInputValues().filter(record)
    logged = record.getMessage()
    assert "jane_public" not in logged
    assert "'options'" in logged
    assert "<key>" in logged


def test_the_argument_name_and_a_list_index_stay_readable() -> None:
    from pydantic import BaseModel

    from trw_mcp.middleware.validation_errors import value_free_message

    class Args(BaseModel):
        items: list[int]

    try:
        Args.model_validate({"items": [1, "x"]})
    except Exception as exc:  # pydantic.ValidationError
        text = value_free_message(exc)  # type: ignore[arg-type]
    assert text.splitlines()[1].startswith("items.1: ")


def test_a_message_that_quotes_a_discriminator_tag_is_dropped_too() -> None:
    from typing import Annotated, Literal

    from pydantic import BaseModel, Field

    from trw_mcp.middleware.validation_errors import value_free_message

    class A(BaseModel):
        kind: Literal["a"]

    class B(BaseModel):
        kind: Literal["b"]

    class Args(BaseModel):
        x: Annotated[A | B, Field(discriminator="kind")]

    try:
        Args.model_validate({"x": {"kind": "Jane Q. Public"}})
    except Exception as exc:  # pydantic.ValidationError
        text = value_free_message(exc)  # type: ignore[arg-type]
    assert "Jane" not in text
    assert "x: invalid value [type=union_tag_invalid]" in text
