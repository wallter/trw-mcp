"""PRD-CORE-347-FR04 / PRD-CORE-348-FR02: the AHR schema and example resources."""

from __future__ import annotations

import json
from typing import Any

from tests.conftest import get_resources_sync
from trw_mcp.handoff import schema_text, validate


def _resources() -> dict[str, Any]:
    from fastmcp import FastMCP

    from trw_mcp.resources.templates import register_template_resources

    srv = FastMCP("test")
    register_template_resources(srv)
    return get_resources_sync(srv)


def test_ahr_schema_resource_returns_packaged_text() -> None:
    resource = _resources()["trw://schemas/ahr/v1"]
    text = resource.fn()
    assert text == schema_text()
    assert json.loads(text)["$id"] == "https://trwframework.com/schemas/ahr/1/ahr.schema.json"
    assert resource.mime_type == "application/schema+json"


def test_ahr_templates_resource_serves_valid_minimal_and_standard_examples() -> None:
    payload = json.loads(_resources()["trw://templates/ahr"].fn())
    assert payload["schema"] == "trw://schemas/ahr/v1"
    assert sorted(payload["examples"]) == ["minimal", "standard"]
    for tier, example in payload["examples"].items():
        assert example["tier"] == tier
        assert validate(example) == []
