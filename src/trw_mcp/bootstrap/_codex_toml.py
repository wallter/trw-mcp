"""TOML parsing/serialization helpers — extracted from _codex.py for module-size compliance.

Belongs to the ``_codex.py`` facade. Re-exported there for back-compat with
internal callers (``_codex.py`` is the only consumer; no external imports).

Self-contained TOML helpers for the Codex bootstrap path:
- ``_parse_codex_toml`` — read a Codex TOML config into a typed dict
- ``_toml_key`` — quote a key when required by TOML grammar
- ``_toml_value`` — render a TOML literal (bool, str, int, float, list)
- ``_toml_dumps`` — serialize a dict-of-dicts into TOML text
"""

from __future__ import annotations

import json
import sys
from typing import cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from trw_mcp.models.typed_dicts import CodexConfigDict


def _parse_codex_toml(content: str) -> CodexConfigDict:
    """Parse Codex TOML config into a dict."""
    return cast("CodexConfigDict", tomllib.loads(content))


def _toml_key(key: str) -> str:
    """Render a TOML key, quoting only when required."""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if key and all(char in allowed for char in key):
        return key
    return json.dumps(key)


def _toml_value(value: object) -> str:
    """Render a TOML literal for the subset used by Codex bootstrap."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        if all(isinstance(item, dict) for item in value):
            inline_tables: list[str] = []
            for item in value:
                dict_item = cast("dict[str, object]", item)
                parts = [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in dict_item.items()]
                inline_tables.append("{ " + ", ".join(parts) + " }")
            return "[\n  " + ",\n  ".join(inline_tables) + ",\n]"
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _toml_dumps(data: dict[str, object]) -> str:
    """Serialize the Codex config structure to TOML without external deps."""
    lines: list[str] = []

    def emit_table(table: dict[str, object], prefix: str | None = None) -> None:
        scalar_items: list[tuple[str, object]] = []
        child_tables: list[tuple[str, dict[str, object]]] = []

        for key, value in table.items():
            if isinstance(value, dict):
                child_tables.append((key, cast("dict[str, object]", value)))
            else:
                scalar_items.append((key, value))

        if prefix is not None:
            lines.append(f"[{prefix}]")
        for key, value in scalar_items:
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        if prefix is not None and (scalar_items or child_tables):
            lines.append("")

        for key, child in child_tables:
            child_prefix = _toml_key(key) if prefix is None else f"{prefix}.{_toml_key(key)}"
            emit_table(child, child_prefix)

    emit_table(data)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
