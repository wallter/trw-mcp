"""Bundled skills and agents call only trw_* tools that exist, with parameters they accept.

A skill that tells an agent to pass a removed parameter makes the call fail at
fastmcp's argument validation. ``trw_learn(source_type=...)`` survived CORE-291
moving ``source_type`` into ``metadata`` (RC dry run 2026-09-23, release gate 11).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
_CALL = re.compile(r"\b(trw_[a-z_]+)\(([^()]*)\)")
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")


def _tool_params() -> dict[str, set[str]]:
    params: dict[str, set[str]] = {}
    for path in (_PACKAGE / "tools").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("trw_"):
                if any(".tool" in ast.unparse(d) for d in node.decorator_list):
                    names = {a.arg for a in (*node.args.args, *node.args.kwonlyargs)} - {"ctx", "self"}
                    params[node.name.removesuffix("_tool")] = names
    return params


def _stale_calls(text: str, params: dict[str, set[str]]) -> list[str]:
    """Every ``trw_x(...)`` in *text* naming a tool that does not exist or a parameter it lacks."""
    stale: list[str] = []
    for match in _CALL.finditer(text):
        name, args = match.group(1), _QUOTED.sub("", match.group(2))
        if name not in params:
            stale.append(f"{name}() is not a tool")
            continue
        stale.extend(
            f"{name}({keyword}=)"
            for keyword in re.findall(r"\b([a-z_][a-z0-9_]*)\s*=", args)
            if keyword not in params[name]
        )
    return stale


def test_the_scan_flags_a_deleted_tool_and_a_removed_parameter_but_not_quoted_text() -> None:
    params = {"trw_learn": {"summary", "metadata"}}
    text = 'trw_learn_update(learning_id="L-1") trw_learn(source_type="human") trw_learn(summary="a b=1")'
    assert _stale_calls(text, params) == ["trw_learn_update() is not a tool", "trw_learn(source_type=)"]


def test_bundled_skills_and_agents_pass_only_parameters_their_tools_accept() -> None:
    params = _tool_params()
    assert "trw_learn" in params, "the tool scan found nothing; the test would pass vacuously"
    stale = [
        f"{path.relative_to(_PACKAGE)}: {finding}"
        for path in sorted(
            [*(_PACKAGE / "data" / "skills").rglob("*.md"), *(_PACKAGE / "data" / "agents").rglob("*.md")]
        )
        for finding in _stale_calls(path.read_text(encoding="utf-8"), params)
    ]
    assert stale == []
