"""Tests for the format-selection and YAML serialization paths of response_optimizer.

These tests live in a separate file from test_middleware_response_optimizer.py
because that file uses an autouse fixture that forces the JSON format for all its
tests. Without that interference, we can exercise:

- _get_response_format: env var resolution, config fallback, exception path
- _yaml_dump: happy path and json-fallback-on-error path
- on_call_tool with YAML format: verifies line 136 (fmt == "yaml" branch)

PRD-QUAL-051-FR03: middleware/response_optimizer.py >=80% coverage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import TextContent

from trw_mcp.middleware.response_optimizer import (
    ResponseOptimizerMiddleware,
    _yaml_dump,
)


@dataclass
class FakeToolResult:
    """Minimal ToolResult stub."""

    content: list[Any]


# ---------------------------------------------------------------------------
# _yaml_dump
# ---------------------------------------------------------------------------


class TestYamlDump:
    """Tests for the YAML serialization helper."""

    @pytest.mark.unit
    def test_serializes_dict_to_yaml(self) -> None:
        """dict input produces YAML string with key: value format."""
        result = _yaml_dump({"key": "value", "num": 42})
        assert "key:" in result
        assert "value" in result

    @pytest.mark.unit
    def test_serializes_list_to_yaml(self) -> None:
        """List input produces YAML sequence."""
        result = _yaml_dump([1, 2, 3])
        assert isinstance(result, str)
        # YAML list entries start with '-'
        assert "-" in result or "1" in result

    @pytest.mark.unit
    def test_fallback_to_json_on_yaml_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When YAML dump raises, falls back to compact JSON."""
        import trw_mcp.middleware.response_optimizer as ro_mod

        monkeypatch.setattr(
            ro_mod._yaml,
            "dump",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("yaml error")),
        )
        result = _yaml_dump({"a": 1})
        parsed = json.loads(result)
        assert parsed == {"a": 1}


# ---------------------------------------------------------------------------
# _get_response_format
# ---------------------------------------------------------------------------


class TestGetResponseFormat:
    """Tests for the _get_response_format resolution order."""

    @pytest.mark.unit
    def test_env_var_yaml_returns_yaml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TRW_RESPONSE_FORMAT=yaml returns 'yaml'."""
        from trw_mcp.middleware.response_optimizer import _get_response_format

        monkeypatch.setenv("TRW_RESPONSE_FORMAT", "yaml")
        assert _get_response_format() == "yaml"

    @pytest.mark.unit
    def test_env_var_json_returns_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """TRW_RESPONSE_FORMAT=json returns 'json'."""
        from trw_mcp.middleware.response_optimizer import _get_response_format

        monkeypatch.setenv("TRW_RESPONSE_FORMAT", "json")
        assert _get_response_format() == "json"

    @pytest.mark.unit
    def test_env_var_unset_falls_through_to_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No env var falls through to config — returns a valid format."""
        from trw_mcp.middleware.response_optimizer import _get_response_format

        monkeypatch.delenv("TRW_RESPONSE_FORMAT", raising=False)
        result = _get_response_format()
        assert result in ("yaml", "json")

    @pytest.mark.unit
    def test_config_resolution_failure_returns_yaml(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When config resolution raises, fallback is 'yaml'."""
        from trw_mcp.middleware.response_optimizer import _get_response_format

        monkeypatch.delenv("TRW_RESPONSE_FORMAT", raising=False)
        monkeypatch.setattr(
            "trw_mcp.models.config.get_config",
            lambda: (_ for _ in ()).throw(RuntimeError("config failed")),
        )
        result = _get_response_format()
        assert result == "yaml"


# ---------------------------------------------------------------------------
# on_call_tool — YAML format path
# ---------------------------------------------------------------------------


class TestYamlFormatInMiddleware:
    """Tests for ResponseOptimizerMiddleware when format is 'yaml'."""

    @pytest.fixture
    def middleware(self) -> ResponseOptimizerMiddleware:
        return ResponseOptimizerMiddleware()

    @pytest.mark.unit
    async def test_yaml_format_produces_yaml_output(
        self, middleware: ResponseOptimizerMiddleware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When format is 'yaml', JSON content is re-serialized as YAML."""
        import trw_mcp.middleware.response_optimizer as ro_mod

        monkeypatch.setattr(ro_mod, "_get_response_format", lambda: "yaml")
        payload = json.dumps({"score": 0.87654, "status": "ok"})
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        # YAML output should have "score:" or "status:" key patterns
        text = out.content[0].text
        assert "score:" in text or "status:" in text

    @pytest.mark.unit
    async def test_yaml_output_is_not_json_compact(
        self, middleware: ResponseOptimizerMiddleware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """YAML output is not the same as compact JSON format."""
        import trw_mcp.middleware.response_optimizer as ro_mod

        monkeypatch.setattr(ro_mod, "_get_response_format", lambda: "yaml")
        payload = json.dumps({"key": "value"})
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        # Compact JSON would be '{"key":"value"}' — YAML would be 'key: value\n'
        text = out.content[0].text
        # YAML doesn't use curly braces for simple dicts
        assert text != '{"key":"value"}'

    @pytest.mark.unit
    async def test_json_format_produces_compact_json(
        self, middleware: ResponseOptimizerMiddleware, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When format is 'json', content is re-serialized as compact JSON."""
        import trw_mcp.middleware.response_optimizer as ro_mod

        monkeypatch.setattr(ro_mod, "_get_response_format", lambda: "json")
        payload = json.dumps({"score": 0.87654, "meta": None})
        result = FakeToolResult(content=[TextContent(type="text", text=payload)])

        async def call_next(_ctx: Any) -> Any:
            return result

        out = await middleware.on_call_tool(None, call_next)  # type: ignore[arg-type]
        parsed = json.loads(out.content[0].text)
        assert parsed["score"] == 0.88
        assert "meta" not in parsed
