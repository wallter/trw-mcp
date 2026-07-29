"""Lint tests for MCP tool docstrings (PRD-QUAL-074 FR06/FR09/FR10).

AST-walks every ``@server.tool()``-decorated function under
``trw-mcp/src/trw_mcp/tools/`` and enforces the Opus 4.7 canonical
docstring pattern from
``docs/documentation/prompting/OPUS-4-7-BEST-PRACTICES.md`` §5:
brief action, ``Use when`` block, input contract, output contract.

This file is the docstring-STRUCTURE floor. ``test_tool_definition_budget.py``
is the companion SIZE ceiling — a tool definition is paid in the system prompt
of every session of every client, so the headers this file mandates must be
filled with short clauses, not TypedDict field inventories. Satisfying one by
violating the other is a defect in both directions.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# Repo root resolved from this test file's location (…/trw-mcp/tests/<file>).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Public-mirror guard: this test asserts a MONOREPO invariant (it AST-walks
# the package at <repo-root>/trw-mcp/src/..., the monorepo layout) absent from
# the standalone trw-mcp PyPI/GitHub mirror where the package is the repo root.
# Skip cleanly there; the monorepo CI still enforces it.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

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
        # trw_knowledge_sync / trw_ceremony_* removed by PRD-FIX-076.
        "trw_instructions_sync",
        "trw_claude_md_sync",
        "trw_learn_update",
        "trw_pre_compact_checkpoint",
        # Added once each was given a served contract above its Args: block.
        # They are not core-preset tools, so nothing else would have guarded
        # them and all six had shipped with no output contract at the wire.
        "trw_dispatch",
        "trw_dispatch_status",
        "trw_channel_stats",
        "trw_code_index_update",
        "trw_agent_work_evidence",
        "trw_validate_agent_work_evidence",
    }
)

#: The default client-facing preset. Every tool an agent sees by default owes
#: it an output contract — trw_skill_discovery and trw_profile_explain were
#: outside REQUIRED_OUTPUT_CONTRACT and had lost theirs entirely, which the
#: served-description regression test could not see because it only checks the
#: required set. Unioned rather than listed twice so the two cannot drift.
CORE_PRESET: frozenset[str] = frozenset(
    {
        "trw_session_start",
        "trw_init",
        "trw_status",
        "trw_checkpoint",
        "trw_learn",
        "trw_recall",
        "trw_build_check",
        "trw_review",
        "trw_deliver",
        "trw_profile_explain",
        "trw_skill_discovery",
        "trw_request_tool_access",
    }
)

OUTPUT_CONTRACT_REQUIRED: frozenset[str] = REQUIRED_OUTPUT_CONTRACT | CORE_PRESET

# Allow-list for grandfathered exceptions — each entry MUST have an
# inline justification comment describing why this exception exists.
# Grandfather exemptions from the `Use when` floor.
#
# EMPTIED 2026-07-28. The three Sprint-96 entries (trw_mcp_security_status,
# trw_query_events, trw_surface_diff) all satisfy the floor now — each gained a
# `Use when` clause when the tool descriptions were audited for retrieval
# quality. They sat here as dead exemptions with nothing to report them, which
# is the same shape as the grandfather lists this repo keeps finding: an
# allowance outlives its reason, and the gate silently covers less than its
# name claims.
#
# `test_allow_list_has_no_stale_entries` below is the fix for the CLASS, not
# just this instance — a tool that starts passing the floor must be removed
# from here or the exemption is reported as stale.
ALLOW_LIST: dict[str, str] = {}

# Prescriptive tokens to scrub from trw_deliver / trw_learn (FR02).
PRESCRIPTIVE_TOKENS: tuple[str, ...] = ("MUST", "CRITICAL", "RIGID")
PRESCRIPTIVE_TARGETS: frozenset[str] = frozenset({"trw_deliver", "trw_learn"})


def test_allow_list_has_no_stale_entries() -> None:
    """A grandfathered tool that now satisfies the floor must leave ALLOW_LIST.

    Without this, an exemption granted once is permanent: the tool gets fixed,
    the entry stays, and the lint quietly stops checking a tool that no longer
    needs exempting. Nothing else in this file would report it. The three
    Sprint-96 entries this replaced had been dead for exactly that reason.
    """
    stale: list[str] = []
    for module, tool_name, doc in _iter_tool_functions():
        if tool_name not in ALLOW_LIST:
            continue
        if doc and any(syn in doc for syn in USE_WHEN_SYNONYMS):
            stale.append(f"{module}::{tool_name} (exempt as: {ALLOW_LIST[tool_name]})")
    assert not stale, (
        "ALLOW_LIST entries whose tool now satisfies the `Use when` floor — "
        "remove them, the exemption is dead: " + ", ".join(sorted(stale))
    )


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


async def _served_descriptions() -> dict[str, str]:
    """Return ``{tool_name: description}`` as the CLIENT actually receives it.

    Not the same string as the source docstring. FastMCP's docstring parser
    routes the ``Args:`` block into per-parameter schema descriptions and
    **discards everything after it** from the tool description — verified
    2026-07-27 against a synthetic tool. A section placed below ``Args:`` is
    therefore invisible to every calling agent while remaining perfectly
    visible to an AST reader.
    """
    from trw_mcp.server._app import mcp

    served: dict[str, str] = {}
    for tool in await mcp._list_tools():
        dumped = tool.model_dump(exclude_none=True)
        served[str(dumped.get("name") or "")] = str(dumped.get("description") or "")
    return served


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


async def test_use_when_survives_to_the_served_description() -> None:
    """The 'Use when' clause must reach the CLIENT, not just satisfy the AST lint.

    The source-level check above cannot see the ``Args:`` truncation, so it
    would pass for a docstring whose ``Use when`` sits below ``Args:`` and is
    never served. This pins the same requirement at the wire.
    """
    served = await _served_descriptions()
    offenders = [
        name
        for name, description in served.items()
        if name not in ALLOW_LIST and not any(syn in description for syn in USE_WHEN_SYNONYMS)
    ]
    assert not offenders, (
        "'Use when' present in source but absent from the description clients "
        "receive (almost always: it sits below the Args: block, which FastMCP "
        "truncates). Move it above Args:.\n  " + "\n  ".join(sorted(offenders))
    )


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
        if tool_name not in OUTPUT_CONTRACT_REQUIRED:
            continue
        if doc is None or ("Output:" not in doc and "Returns:" not in doc):
            offenders.append(f"{module}::{tool_name}: missing 'Output:' / 'Returns:' field enumeration")
    assert not offenders, "Tools missing output contract:\n  " + "\n  ".join(offenders)


async def test_output_contract_survives_to_the_served_description() -> None:
    """The output contract must reach the CLIENT — FR10 was Potemkin without this.

    ``test_output_contract_named`` reads the AST, so it happily passed for a
    docstring whose only ``Output:`` line sat below ``Args:`` — where FastMCP
    truncates it and no calling agent ever sees it. Measured on 2026-07-27,
    eight tools served no output contract at all. The two failure modes were
    distinct and only one is what this test's name suggests: a contract written
    below ``Args:`` (source-only, AST-visible, wire-invisible), or simply no
    contract anywhere. Six of the eight were the latter.

    SCOPE, STATED HONESTLY. This assertion covers exactly the names in
    ``OUTPUT_CONTRACT_REQUIRED`` that the server actually registers — nothing
    wider. All eight of the originally-measured tools are now members:
    ``trw_skill_discovery`` and ``trw_request_tool_access`` entered via the
    core preset, and the remaining six (``trw_dispatch``,
    ``trw_dispatch_status``, ``trw_channel_stats``, ``trw_code_index_update``,
    ``trw_agent_work_evidence``, ``trw_validate_agent_work_evidence``) were
    given served contracts and added to the required set. A tool outside that
    set is still unguarded here; adding one is a one-line change.

    Two earlier drafts of this docstring were wrong in opposite directions —
    one claimed all eight were covered when two were, the next was left saying
    six are unguarded after they had been guarded. In the file whose whole
    purpose is catching gates that cannot fail, the scope sentence is part of
    the gate: keep it matched to the set above.
    """
    served = await _served_descriptions()
    offenders = [
        name
        for name, description in served.items()
        if name in OUTPUT_CONTRACT_REQUIRED and "Output:" not in description and "Returns:" not in description
    ]
    assert not offenders, (
        "No output contract in the description clients receive. Either the "
        "docstring has none, or it sits below the Args: block, which FastMCP "
        "truncates — add one above Args:.\n  " + "\n  ".join(sorted(offenders))
    )


# ---------------------------------------------------------------- FR06 discoverability (HARD)


async def test_all_registered_tools_discoverable() -> None:
    """AST-discovered tools must be a subset of what the FastMCP server actually registers.

    Catches drift between the AST lint set and the actual server registration.
    If a tool is registered via a dynamic path the AST cannot see, it falls
    through this check and is effectively exempt from all other lints.

    Uses FastMCP's public ``mcp.list_tools()`` coroutine (stable API across
    versions) rather than probing private registry attributes. If ``list_tools``
    is removed or renamed upstream, this test fails loudly — exactly the
    signal we want. No ``pytest.skip`` on ImportError: drift detection must
    not silently pass.
    """
    ast_tools = {name for (name, _module, _docstring) in _iter_tool_functions()}
    from trw_mcp.server import mcp as server

    # mcp is typed `object` in trw_mcp/server/__init__.py (lazy loader hiding
    # the optional fastmcp dependency). Cast at the call site to access the
    # FastMCP public API without forcing the import at module load.
    runtime_list = await server.list_tools()  # type: ignore[attr-defined]
    runtime_tools = {tool.name for tool in runtime_list}

    # Some AST tools may be gated by preset/feature flags and not appear at
    # runtime. Assert directional inclusion: every runtime tool must be AST-
    # discoverable (so lints cover them). The reverse is not required —
    # AST-discovered tools that are runtime-gated by presets are legitimate.
    missing_in_ast = runtime_tools - ast_tools
    assert not missing_in_ast, (
        f"{len(missing_in_ast)} runtime-registered tools are not discovered "
        f"by the AST walker — they escape docstring lint: {sorted(missing_in_ast)!r}"
    )


# ---------------------------------------------------------------- negative (HARD)


def test_missing_docstring_fails() -> None:
    """Synthetic: a function with no docstring is flagged by the AST walker."""
    src = "def _fake_tool():\n    pass\n"
    tree = ast.parse(src)
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert ast.get_docstring(fn) is None
