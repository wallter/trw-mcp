"""Declared-edge call-chain verifier (PRD-CORE-320 FR02).

DECLARED EDGES ONLY. A wired claim carries an explicit call chain declared by
the claimant in a PRD's traceability "Call chain" column — an ordered list of
fully-qualified symbols, ``entry -> ... -> capability``. This module verifies
exactly the hops it is given: it never infers, searches for, or builds a path
between an entry point and a capability.

That restriction is deliberate, not an oversight. A generalized transitive
reachability/call-graph design was already tried for a related defect class
inside the wiring detector (``scripts/_wiring``, formerly ``trw_mcp.wiring``) and demoted: a ``register_*_tools`` function *is*
called at boot through the registrar tuple, and the tool it registers *is*
reachable from there, so pure reachability cannot distinguish "registered and
callable" from "actually produces anything" — it measured roughly 1-in-8
recall and a 47% false-positive rate on this codebase
(``scripts/_wiring/detector.py:1-8``,
``scripts/tests/wiring/test_repo_wide_run.py``,
``test_no_reachability_analysis_was_built``). This module therefore lives
outside the detector. It shares the detector's deterministic source primitives
(``is_test_path``, ``module_path_for``, ``parse_module``, in
:mod:`trw_mcp.state.validation._source_ast`) but must
never define a call graph or reintroduce the banned names ``call_graph`` /
``CallGraph`` / ``build_call_graph`` / ``reachable_from`` under any spelling.

The entry point is the tool's own registration site: its name is on the
registered surface AND its module defines it under a ``@<server>.tool(...)``
decorator, so a same-named function elsewhere cannot stand in for it. A chain
may instead start ``hook:<stem>`` (FR04): that hop's own verification (a text
match against the hook script plus a run-evidence marker) lives in
:mod:`trw_mcp.state.validation.hook_entry`, apart from this module's
declared-edge verification of every later hop. ``cli:`` entries remain a later
slice.

What counts as an edge. The caller is the registered tool's own definition
(for the entry) or a module-level function (for every later hop). Only calls
in that function's own body count: a call inside a nested function, class or
lambda may never run. A called name resolves through the caller's scopes,
innermost first, and only when exactly one binding exists there: a single
import, or a single module-level ``def``. A name that is also assigned,
rebound, declared global/nonlocal or imported twice is ambiguous and gives no
edge. Every callee must be a module-level function defined in non-test source,
so a call to a symbol that does not exist never verifies. A callee re-exported
through another module does not match its defining module's symbol; that is
reported ``isolated`` (the safe direction).

Scope. This proves a direct call to the callee is written in the caller's own
body on a single static binding. It does not prove the call executes on every
run: a branch, an early return or an exception can still skip it. A wired claim
means "a live production caller exists and names this call site", the
CONSTITUTION's Execution Traceability standard, not "this line always runs".

Fail-closed direction (NFR02): a missing/empty/malformed chain, an unresolvable
symbol, a hop satisfied only inside a test file or an uncalled closure, or an
entry that is not the registered tool's own definition all resolve to
``"isolated"`` with a named reason. This function never raises on malformed
input.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Literal

from trw_mcp.state.validation import hook_entry
from trw_mcp.state.validation._source_ast import is_test_path, module_path_for, parse_module

ChainStatus = Literal["wired", "isolated"]
_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef
#: Bodies that may never run where they are written: nested functions, classes, lambdas, generator
#: expressions (lazy until consumed) and asserts (stripped under ``python -O``). List/set/dict
#: comprehensions run immediately.
_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.GeneratorExp, ast.Assert)


@dataclass(frozen=True)
class ChainVerdict:
    """Result of :func:`verify_chain`.

    ``call_sites`` names one ``"path:line"`` per verified hop, in chain
    order. ``first_unverified_hop`` and ``reason`` are set only when
    ``status`` is ``"isolated"``.
    """

    status: ChainStatus
    call_sites: tuple[str, ...] = ()
    first_unverified_hop: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ToolSite:
    """Where a registered MCP tool is defined: its module and its first source line (first decorator)."""

    module: str
    first_line: int


def registered_tool_sites() -> dict[str, ToolSite]:
    """Each registered MCP tool's defining module and first line, from the registrars themselves.

    Registers every ``_tool_registrars()`` entry on a throwaway FastMCP probe (as
    ``raw_registered_tool_names`` does) and reads each tool's own function, so the
    entry point is the registered callable, never a same-named function elsewhere.
    """
    from fastmcp import FastMCP

    from trw_mcp.server._tools import _run_async, _tool_registrars

    probe = FastMCP("trw-call-chain-probe")
    for registrar in _tool_registrars():
        registrar(probe)
    sites: dict[str, ToolSite] = {}
    for tool in _run_async(probe.list_tools()):
        fn = getattr(tool, "fn", None)
        if fn is None:
            continue
        fn = inspect.unwrap(fn)
        code = getattr(fn, "__code__", None)
        if code is not None:
            sites[tool.name] = ToolSite(fn.__module__, code.co_firstlineno)
    return sites


@dataclass
class _Bindings:
    """The names one scope binds: imports (name -> qualified targets), function/class defs, and everything else."""

    imports: dict[str, set[str]] = field(default_factory=dict)
    defs: dict[str, int] = field(default_factory=dict)
    others: set[str] = field(default_factory=set)


def _own_nodes(scope: ast.AST) -> Iterator[ast.AST]:
    """The nodes of *scope*'s own body, not descending into nested functions, classes or lambdas."""
    stack: list[ast.AST] = list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, _NESTED_SCOPES):
            stack.extend(ast.iter_child_nodes(node))


def _bindings(scope: ast.Module | _FunctionNode) -> _Bindings:
    found = _Bindings()
    if not isinstance(scope, ast.Module):
        args = scope.args
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]
        found.others.update(arg.arg for arg in every if arg is not None)
    for node in _own_nodes(scope):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            found.defs[node.name] = found.defs.get(node.name, 0) + 1
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                if node.level or not node.module:
                    found.others.add(local)  # a relative import: its target is not resolved here
                else:
                    found.imports.setdefault(local, set()).add(f"{node.module}.{alias.name}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else alias.name.split(".")[0]
                found.imports.setdefault(local, set()).add(target)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store | ast.Del):
            found.others.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            found.others.add(node.name)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            found.others.update(node.names)
        elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
            found.others.add(node.name)  # a match capture rebinds the name
        elif isinstance(node, ast.MatchMapping) and node.rest:
            found.others.add(node.rest)
    return found


def _resolve_name(name: str, scopes: Sequence[_Bindings], module: str) -> str | None:
    """*name*'s fully-qualified target through *scopes* (innermost first, module last), or None if ambiguous."""
    for depth, scope in enumerate(scopes):
        imported = scope.imports.get(name)
        defined = scope.defs.get(name, 0)
        if name in scope.others:
            return None
        if imported is None and not defined:
            continue
        if imported is not None and defined:
            return None
        if imported is not None:
            return next(iter(imported)) if len(imported) == 1 else None
        is_module_scope = depth == len(scopes) - 1
        return f"{module}.{name}" if is_module_scope and defined == 1 else None
    return None


def _call_target(call: ast.Call, scopes: Sequence[_Bindings], module: str) -> str | None:
    """A call's fully-qualified target: ``name(...)`` or ``name.attr...(...)``; None when not resolvable."""
    return _qualified(call.func, scopes, module)


def _qualified(expression: ast.expr, scopes: Sequence[_Bindings], module: str) -> str | None:
    """The fully-qualified symbol ``name`` or ``name.attr...`` resolves to; None when not resolvable."""
    attrs: list[str] = []
    target = expression
    while isinstance(target, ast.Attribute):
        attrs.append(target.attr)
        target = target.value
    if not isinstance(target, ast.Name):
        return None
    base = _resolve_name(target.id, scopes, module)
    return None if base is None else ".".join([base, *reversed(attrs)])


def _split_symbol(symbol: object) -> tuple[str, str] | None:
    """Split ``"a.b.c.func"`` into (``"a.b.c"``, ``"func"``); None for anything else."""
    if not isinstance(symbol, str) or "." not in symbol:
        return None
    module, _, name = symbol.rpartition(".")
    if not module or not name.isidentifier() or not all(part.isidentifier() for part in module.split(".")):
        return None
    return module, name


def _source(repo_root: Path, module: str) -> tuple[Path, ast.Module] | None:
    """The non-test source file and parse tree of *module* under *repo_root*."""
    path = module_path_for(repo_root, module, module.split(".", 1)[0])
    if path is None or is_test_path(path):
        return None
    tree = parse_module(path)
    return None if tree is None else (path, tree)


def _module_function(tree: ast.Module, name: str) -> _FunctionNode | None:
    """The module-level function named *name* when its ``def`` is the name's only binding in the module.

    A second ``def``, an import or any assignment of the name (``capability = print``) means a call
    may not reach this function, so there is no single function to verify against.
    """
    found = [
        node for node in tree.body if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
    ]
    bound = _bindings(tree)
    if len(found) != 1 or bound.defs.get(name) != 1 or name in bound.imports or name in bound.others:
        return None
    return found[0]


def _first_line(node: _FunctionNode) -> int:
    return min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])


def _enclosing_scopes(tree: ast.Module, node: _FunctionNode) -> list[_FunctionNode]:
    """The functions enclosing *node*, innermost first."""
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    chain: list[_FunctionNode] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            chain.append(current)
        current = parents.get(current)
    return chain


def _registered_entry(
    repo_root: Path, entry: str, tools: Mapping[str, ToolSite]
) -> tuple[Path, ast.Module, _FunctionNode] | None:
    """The registered tool's own definition that *entry* names, or None."""
    split = _split_symbol(entry)
    if split is None:
        return None
    module, name = split
    site = tools.get(name)
    source = _source(repo_root, module) if site is not None and site.module == module else None
    if site is None or source is None:
        return None
    path, tree = source
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
            and _first_line(node) == site.first_line
        ):
            return path, tree, node
    return None


def _decorator_name(node: ast.expr) -> str:
    """Best-effort decorator source name, for messaging only (never used to resolve identity)."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return "<decorator>"


#: Stop points for generator detection: a nested function, class or lambda's own
#: ``yield`` belongs to THAT callable, not the one being inspected. Unlike
#: ``_NESTED_SCOPES``, this does NOT stop at ``Assert``: a ``yield`` inside an
#: assert's test still makes the enclosing function a generator at compile time
#: (the code object's generator flag is set from a `yield` anywhere in the body,
#: whether or not the assert ever executes under ``-O``) — ``_own_nodes`` skips
#: assert bodies for a different, unrelated reason (they may not run) and must
#: not be reused here (worker-3 review, P2 row (b)).
_GENERATOR_STOP_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _is_generator_function(node: _FunctionNode) -> bool:
    """True when a ``yield``/``yield from`` occurs anywhere in *node*'s own body.

    "Own body" excludes a nested function/class/lambda, but — unlike
    ``_own_nodes`` — DOES look inside an ``assert``'s test/message.
    """
    stack: list[ast.AST] = list(getattr(node, "body", []))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.Yield | ast.YieldFrom):
            return True
        if not isinstance(current, _GENERATOR_STOP_SCOPES):
            stack.extend(ast.iter_child_nodes(current))
    return False


def _callee_block_reason(repo_root: Path, callee: str) -> str:
    """A non-empty reason when *callee* is ambiguous regardless of whether a call site is found.

    A decorated callee (the decorator may wrap or replace it) and a generator-function callee
    (calling it only builds an iterator; the body does not run until consumed) are both isolated
    unconditionally (worker-3 review, P2 b/c). An unresolvable callee returns "" here; the
    existing existence check elsewhere reports it.
    """
    split = _split_symbol(callee)
    if split is None:
        return ""
    source = _source(repo_root, split[0])
    if source is None:
        return ""
    function = _module_function(source[1], split[1])
    if function is None:
        return ""
    if function.decorator_list:
        names = ", ".join(_decorator_name(d) for d in function.decorator_list)
        return f"callee is decorated; the decorator may replace it ({names})"
    if _is_generator_function(function):
        return "generator callee: the call alone does not run it"
    return ""


def _verify_hop(
    repo_root: Path, caller: tuple[Path, ast.Module, _FunctionNode], caller_module: str, callee: str
) -> str | None:
    """A direct, non-test call from *caller*'s own body to the existing *callee*; its ``"path:line"`` or None.

    An ``async def`` callee only counts when the matching ``Call`` is directly ``await``ed
    (worker-3 review, P2 c): an unawaited async call constructs a coroutine but never runs it.
    """
    callee_split = _split_symbol(callee)
    if callee_split is None:
        return None
    callee_source = _source(repo_root, callee_split[0])
    if callee_source is None:
        return None
    callee_function = _module_function(callee_source[1], callee_split[1])
    if callee_function is None:
        return None
    path, tree, function = caller
    enclosing = _enclosing_scopes(tree, function)
    scopes = [_bindings(function), *(_bindings(outer) for outer in enclosing), _bindings(tree)]
    # Every enclosing function scope, not only the caller and the module, can rebind the callee
    # (worker-3 review, P2 a): a closure's outer function may attribute-store or setattr/delattr
    # the same name the caller resolves through.
    scope_nodes: tuple[ast.Module | _FunctionNode, ...] = (function, *enclosing, tree)
    rebound = {
        _qualified(node, scopes, caller_module)
        for scope in scope_nodes
        for node in _own_nodes(scope)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store | ast.Del)
    }
    rebound |= {
        f"{_qualified(node.args[0], scopes, caller_module)}.{node.args[1].value}"
        for scope in scope_nodes
        for node in _own_nodes(scope)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"setattr", "delattr"}
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    }
    if callee in rebound:  # ``leaf.capability = print`` or setattr/delattr replaces the callee for this caller
        return None
    is_async_callee = isinstance(callee_function, ast.AsyncFunctionDef)
    awaited_call_ids = {id(n.value) for n in _own_nodes(function) if isinstance(n, ast.Await)}
    calls = sorted((n for n in _own_nodes(function) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
    for call in calls:
        if _call_target(call, scopes, caller_module) != callee:
            continue
        if is_async_callee and id(call) not in awaited_call_ids:
            continue  # unawaited: a coroutine object was made, the callee's body did not run
        try:
            relative = path.relative_to(repo_root)
        except ValueError:
            relative = path
        return f"{relative}:{call.lineno}"
    return None


def verify_chain(
    repo_root: Path,
    chain: Sequence[str],
    *,
    tools: Mapping[str, ToolSite] | None = None,
    hook_evidence: Collection[str] = (),
) -> ChainVerdict:
    """Verify each declared hop of *chain*, ``entry -> ... -> capability``.

    The entry is either a registered MCP tool's own definition (*tools* defaults
    to :func:`registered_tool_sites`) or ``hook:<stem>`` (FR04; see
    :mod:`trw_mcp.state.validation.hook_entry` for that hop's own soundness
    scope — *hook_evidence* is the set of hook stems whose real-path marker was
    observed in this run, gathered by the caller). ``cli:`` entries are a later
    slice and report ``isolated`` with an "unsupported entry kind" reason. Every
    later symbol must be a module-level function in non-test source, and each
    consecutive pair must be a direct call (see the module docstring). Never
    raises on malformed input.
    """
    if isinstance(chain, str) or not isinstance(chain, Sequence) or not chain:
        return ChainVerdict(status="isolated", reason="missing, empty or malformed call chain")
    if len(chain) < 2:  # an entry alone names no capability, so there is no edge to verify (sol s2 r1)
        return ChainVerdict(
            status="isolated", first_unverified_hop=str(chain[0]), reason="a chain needs an entry and a capability"
        )
    entry = chain[0]
    if isinstance(entry, str) and entry.partition(":")[0] == "cli":
        return ChainVerdict(
            status="isolated",
            first_unverified_hop=entry,
            reason="unsupported entry kind 'cli': CLI verbs are a later slice",
        )
    is_hook_entry = isinstance(entry, str) and entry.partition(":")[0] == "hook"
    # A hook entry's own text (chain[0]) is not a dotted symbol and is checked by
    # hook_entry.verify_hook_hop instead; every OTHER symbol still needs one.
    malformed_pool = chain[1:] if is_hook_entry else chain
    malformed = next((str(symbol) for symbol in malformed_pool if _split_symbol(symbol) is None), None)
    if malformed is not None:
        return ChainVerdict(status="isolated", first_unverified_hop=malformed, reason="malformed chain symbol")
    call_sites: list[str] = []
    caller: tuple[Path, ast.Module, _FunctionNode] | None
    if is_hook_entry:
        stem = entry.partition(":")[2]
        next_hop = chain[1]
        hook_result = hook_entry.verify_hook_hop(repo_root, stem, next_hop, hook_evidence)
        if hook_result.reason:
            return ChainVerdict(
                status="isolated", first_unverified_hop=f"{entry} -> {next_hop}", reason=hook_result.reason
            )
        call_sites.append(hook_result.call_site)
        callee_module, callee_name = next_hop.rpartition(".")[0], next_hop.rpartition(".")[2]
        hook_source = _source(repo_root, callee_module)
        hook_function = _module_function(hook_source[1], callee_name) if hook_source else None
        if hook_source is None or hook_function is None:
            return ChainVerdict(
                status="isolated",
                call_sites=tuple(call_sites),
                first_unverified_hop=next_hop,
                reason="hook's next hop does not exist as a module-level function",
            )
        caller = (hook_source[0], hook_source[1], hook_function)
        remaining = chain[1:]
    else:
        caller = _registered_entry(repo_root, entry, registered_tool_sites() if tools is None else tools)
        if caller is None:
            return ChainVerdict(
                status="isolated",
                first_unverified_hop=entry,
                reason="first symbol is not a registered entry point: not a registered MCP tool's own definition "
                "or a ``hook:<stem>`` entry (CLI verbs are a later slice)",
            )
        remaining = chain
    for caller_symbol, callee in pairwise(remaining):
        caller_module = caller_symbol.rpartition(".")[0]
        block_reason = _callee_block_reason(repo_root, callee)
        if block_reason:
            return ChainVerdict(
                status="isolated",
                call_sites=tuple(call_sites),
                first_unverified_hop=f"{caller_symbol} -> {callee}",
                reason=block_reason,
            )
        site = _verify_hop(repo_root, caller, caller_module, callee)
        if site is None:
            return ChainVerdict(
                status="isolated",
                call_sites=tuple(call_sites),
                first_unverified_hop=f"{caller_symbol} -> {callee}",
                reason=f"no verified direct, non-test call from {caller_symbol} to an existing {callee}",
            )
        call_sites.append(site)
        callee_module, callee_name = callee.rpartition(".")[0], callee.rpartition(".")[2]
        source = _source(repo_root, callee_module)
        function = _module_function(source[1], callee_name) if source else None
        if source is None or function is None:  # _verify_hop proved it exists; kept for the type checker
            return ChainVerdict(
                status="isolated", call_sites=tuple(call_sites), first_unverified_hop=callee, reason="callee vanished"
            )
        caller = (source[0], source[1], function)
    return ChainVerdict(status="wired", call_sites=tuple(call_sites))
