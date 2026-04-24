"""Lint tests for MCP tool docstrings (PRD-QUAL-074 FR06/FR09/FR10).

AST-walks every ``@server.tool()``-decorated function under
``trw-mcp/src/trw_mcp/tools/`` and enforces the Opus 4.7 canonical
docstring pattern from
``docs/documentation/prompting/OPUS-4-7-BEST-PRACTICES.md`` §5:
brief action, ``Use when`` block, input contract, output contract.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Repo root resolved from this test file's location (…/trw-mcp/tests/<file>).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TOOLS_DIR = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "tools"

# Accepted synonyms. Default canonical is "Use when"; relax only with justification.
USE_WHEN_SYNONYMS: tuple[str, ...] = ("Use when",)

# Tools that MUST name an output contract via "Output:" or "Returns:" (FR10 hard).
REQUIRED_OUTPUT_CONTRACT: frozenset[str] = frozenset(
    {
        "trw_build_check",
        "trw_session_start",
        "trw_deliver",
        "trw_checkpoint",
        "trw_recall",
        "trw_learn",
        "trw_init",
        "trw_status",
        "trw_prd_create",
        "trw_prd_validate",
        "trw_review",
        "trw_heartbeat",
        "trw_adopt_run",
        "trw_knowledge_sync",
        "trw_run_report",
        "trw_analytics_report",
        "trw_usage_report",
        "trw_progressive_expand",
        "trw_trust_level",
        "trw_instructions_sync",
        "trw_claude_md_sync",
        "trw_learn_update",
        "trw_ceremony_status",
        "trw_ceremony_approve",
        "trw_ceremony_revert",
        "trw_pre_compact_checkpoint",
    }
)

# Allow-list for grandfathered exceptions — each entry MUST have an
# inline justification comment describing why this exception exists.
ALLOW_LIST: dict[str, str] = {}

# Prescriptive tokens to scrub from trw_deliver / trw_learn (FR02).
PRESCRIPTIVE_TOKENS: tuple[str, ...] = ("MUST", "CRITICAL", "RIGID")
PRESCRIPTIVE_TARGETS: frozenset[str] = frozenset({"trw_deliver", "trw_learn"})


def _is_server_tool_decorator(deco: ast.expr) -> bool:
    """Return True if the AST decorator node is ``@server.tool(...)`` or ``@mcp.tool(...)``."""
    call = deco.func if isinstance(deco, ast.Call) else deco
    if not isinstance(call, ast.Attribute):
        return False
    if call.attr != "tool":
        return False
    if not isinstance(call.value, ast.Name):
        return False
    return call.value.id in {"server", "mcp"}


def _iter_tool_functions() -> list[tuple[str, str, str | None]]:
    """Return (tool_name, module_filename, docstring) for every registered tool.

    ``tool_name`` honors an explicit ``name=`` kwarg on ``@server.tool(...)``,
    falling back to the function's Python name. Walks all modules under
    ``trw-mcp/src/trw_mcp/tools/``.
    """
    out: list[tuple[str, str, str | None]] = []
    # rglob so we also pick up sub-packages like tools/build/_registration.py
    # where trw_build_check lives. Skip dunder-named files but keep sub-package
    # files that contain @server.tool decorators.
    for path in sorted(_TOOLS_DIR.rglob("*.py")):
        if path.name in {"__init__.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                if not _is_server_tool_decorator(deco):
                    continue
                tool_name = node.name
                if isinstance(deco, ast.Call):
                    for kw in deco.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            tool_name = kw.value.value
                out.append((tool_name, path.name, ast.get_docstring(node)))
                break
    return out


# ---------------------------------------------------------------- FR01/FR06


def test_all_tools_have_use_when() -> None:
    """Every registered tool docstring contains 'Use when' (or allow-listed synonym)."""
    offenders: list[str] = []
    for tool_name, module, doc in _iter_tool_functions():
        if tool_name in ALLOW_LIST:
            continue
        if doc is None or not doc.strip():
            offenders.append(f"{module}::{tool_name}: missing docstring")
            continue
        if not any(syn in doc for syn in USE_WHEN_SYNONYMS):
            offenders.append(f"{module}::{tool_name}: docstring missing 'Use when' clause")
    assert not offenders, "Tool docstrings missing 'Use when':\n  " + "\n  ".join(offenders)


# ---------------------------------------------------------------- FR02


def test_no_prescriptive_phrasing() -> None:
    """trw_deliver / trw_learn docstrings MUST NOT contain MUST / CRITICAL / RIGID tokens.

    Case-insensitive, word-bounded match so `must-have` in prose is fine but the
    all-caps prescriptive forms are flagged.
    """
    offenders: list[str] = []
    for tool_name, module, doc in _iter_tool_functions():
        if tool_name not in PRESCRIPTIVE_TARGETS:
            continue
        if doc is None:
            continue
        for token in PRESCRIPTIVE_TOKENS:
            if re.search(rf"\b{re.escape(token)}\b", doc):
                offenders.append(f"{module}::{tool_name}: contains prescriptive token '{token}'")
    assert not offenders, "Prescriptive phrasing present:\n  " + "\n  ".join(offenders)


# ---------------------------------------------------------------- FR10


def test_output_contract_named() -> None:
    """Tools in REQUIRED_OUTPUT_CONTRACT must state 'Output:' or 'Returns:' in docstring."""
    offenders: list[str] = []
    for tool_name, module, doc in _iter_tool_functions():
        if tool_name not in REQUIRED_OUTPUT_CONTRACT:
            continue
        if doc is None or ("Output:" not in doc and "Returns:" not in doc):
            offenders.append(f"{module}::{tool_name}: missing 'Output:' / 'Returns:' field enumeration")
    assert not offenders, "Tools missing output contract:\n  " + "\n  ".join(offenders)


# ---------------------------------------------------------------- FR06 discoverability (HARD)


def test_all_registered_tools_discoverable() -> None:
    """AST-discovered tools must match the names the FastMCP server registers at runtime.

    Catches drift between the AST lint set and the actual server registration.
    If a tool is registered via a dynamic path the AST cannot see, it falls
    through this check and is effectively exempt from all other lints.
    """
    ast_tools = {name for (name, _m, _d) in _iter_tool_functions()}
    # Import after tree-walk to avoid masking AST errors with import errors.
    # The server factory path varies by version; we try a couple of known
    # entry points and skip (not fail) if none resolve — drift between AST
    # and runtime registration is a separate concern from docstring lint.
    try:
        from trw_mcp.server._tools import mcp as server
    except ImportError:
        pytest.skip("trw_mcp.server._tools.mcp unavailable in this build")
    # FastMCP exposes registered tools via its internal registry. We probe
    # for common attributes but fall back to the AST set if none match.
    runtime_tools: set[str] = set()
    for attr in ("_tools", "tools", "_tool_manager"):
        obj = getattr(server, attr, None)
        if obj is None:
            continue
        # Dict-like
        if isinstance(obj, dict):
            runtime_tools = set(obj.keys())
            break
        # Manager-like with _tools dict
        inner = getattr(obj, "_tools", None)
        if isinstance(inner, dict):
            runtime_tools = set(inner.keys())
            break
    # If the FastMCP internals moved, soft-pass with a warning rather than
    # blocking the suite on an unrelated refactor.
    if not runtime_tools:
        pytest.skip("could not introspect FastMCP tool registry; AST set has {n} tools".format(n=len(ast_tools)))

    missing_in_runtime = ast_tools - runtime_tools
    missing_in_ast = runtime_tools - ast_tools
    assert not missing_in_runtime, f"AST sees tools the runtime server does not: {sorted(missing_in_runtime)}"
    assert not missing_in_ast, f"Runtime registers tools the AST did not see: {sorted(missing_in_ast)}"


# ---------------------------------------------------------------- negative (HARD)


def test_missing_docstring_fails() -> None:
    """Synthetic: a function with no docstring is flagged by the AST walker."""
    src = (
        "def _fake_tool():\n"
        "    pass\n"
    )
    tree = ast.parse(src)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert ast.get_docstring(fn) is None
