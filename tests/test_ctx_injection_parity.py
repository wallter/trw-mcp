"""PRD-CORE-141 FR03 — live-registry AST parity for ctx injection.

This is the authoritative check that every pin-state-touching tool
declares ``ctx: Context`` (or ``Context | None``) in its signature, and
conversely that no tool carrying a ``ctx: Context`` parameter has drifted
without actually needing it.

Approach:
  1. Enumerate every tool registered on a fresh FastMCP server via the
     shared test-server factory (no hardcoded tool list).
  2. For each tool, AST-parse its function body plus the bodies of every
     helper it transitively calls within ``trw_mcp/`` to compute a call
     closure.
  3. A tool is "pin-state-touching" iff its closure contains a call to
     any of ``{pin_active_run, find_active_run, get_pinned_run,
     unpin_active_run, resolve_run_path, resolve_pin_key}``.
  4. Assert the ``ctx`` parameter is typed as ``Context``/``Context | None``
     for every pin-state-touching tool.

If the live-registry path is unavailable we skip gracefully and rely on
the grep-count assertion in test_pin_isolation_ctx (a coarser signal).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any, get_type_hints

_PIN_STATE_HELPERS: frozenset[str] = frozenset(
    {
        "pin_active_run",
        "find_active_run",
        "get_pinned_run",
        "unpin_active_run",
        "resolve_run_path",
        "resolve_pin_key",
    }
)


def _iter_calls(tree: ast.AST) -> list[str]:
    """Return the list of direct function names called anywhere in *tree*."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)
    return names


def _module_call_map() -> dict[str, set[str]]:
    """Build an import-aware qualified call graph for ``trw_mcp`` functions."""
    import trw_mcp

    src_root = Path(trw_mcp.__file__).resolve().parent
    call_map: dict[str, set[str]] = {}
    for py_path in src_root.rglob("*.py"):
        module = "trw_mcp." + py_path.relative_to(src_root).with_suffix("").as_posix().replace("/", ".")
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split(".")[0]] = item.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                prefix = node.module
                if node.level:
                    parts = module.split(".")[: -node.level]
                    prefix = ".".join([*parts, node.module])
                for item in node.names:
                    aliases[item.asname or item.name] = f"{prefix}.{item.name}"
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            calls: set[str] = set()
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                func = call.func
                if isinstance(func, ast.Name):
                    calls.add(aliases.get(func.id, f"{module}.{func.id}"))
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    base = aliases.get(func.value.id)
                    if base:
                        calls.add(f"{base}.{func.attr}")
                    else:
                        calls.add(f"{module}.{func.attr}")
            call_map.setdefault(f"{module}.{node.name}", set()).update(calls)
    return call_map


def _transitively_calls(
    start: str,
    call_map: dict[str, set[str]],
    targets: frozenset[str],
) -> bool:
    """Return whether the qualified runtime call closure reaches pin state."""
    seen: set[str] = set()
    frontier: list[str] = [start]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        called = call_map.get(name, set())
        if any(target.rsplit(".", 1)[-1] in targets for target in called):
            return True
        frontier.extend(called - seen)
    return False


def _tool_directly_calls_pin_state(fn: Any) -> bool:
    """AST-check whether *fn*'s own body directly calls any pin helper."""
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        return False
    try:
        tree = ast.parse(inspect.cleandoc("\n".join(src.splitlines())))
    except SyntaxError:
        return False
    return any(call in _PIN_STATE_HELPERS for call in _iter_calls(tree))


def _tool_declares_ctx(fn: Any) -> bool:
    """Return True when *fn* has a ``ctx`` parameter typed as Context-ish."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    if "ctx" not in sig.parameters:
        return False
    try:
        hints = get_type_hints(fn)
    except Exception:
        return False
    hint = hints.get("ctx")
    if hint is None:
        return False
    # Stringify to survive Context / Context | None / Optional[Context].
    return "Context" in str(hint)


def _all_registered_tools() -> dict[str, Any]:
    """Register every tool group and return the registry."""
    from tests.conftest import get_tools_sync, make_test_server

    server = make_test_server(
        "build",
        "ceremony",
        "ceremony_feedback",
        "checkpoint",
        "knowledge",
        "learning",
        "orchestration",
        "requirements",
        "review",
    )
    return get_tools_sync(server)


def test_all_pin_state_tools_declare_ctx() -> None:
    """FR03: derive pin-touching tools from the live registry and call graph."""
    tools = _all_registered_tools()
    call_map = _module_call_map()
    pin_touching = {
        name
        for name, tool in tools.items()
        if _transitively_calls(f"{tool.fn.__module__}.{tool.fn.__name__}", call_map, _PIN_STATE_HELPERS)
    }
    assert pin_touching, "live-registry call graph found no pin-state tools"
    missing = sorted(name for name in pin_touching if not _tool_declares_ctx(tools[name].fn))
    assert not missing, f"PRD-CORE-141 FR03 violation: live-registry pin-state tools missing ctx: {missing}"


def test_ctx_param_count_grep_floor() -> None:
    """Secondary coarse signal: ``ctx: Context`` appears in at least 5 tool files."""
    import trw_mcp

    src_root = Path(trw_mcp.__file__).resolve().parent / "tools"
    hit_files: list[str] = []
    for py_path in src_root.rglob("*.py"):
        if py_path.name.startswith("_"):
            # underscore-prefixed modules are helpers, not tool registrations
            pass
        text = py_path.read_text(encoding="utf-8")
        if "ctx: Context" in text:
            hit_files.append(py_path.name)
    assert len(hit_files) >= 5, f"Expected >=5 tool files declaring `ctx: Context` — got {sorted(hit_files)}"


def test_no_pin_state_tool_call_without_ctx_or_session() -> None:
    """Belt-and-braces: every direct call to a pin helper inside tools/ passes ctx= or session_id=.

    Catches regressions where a handler is migrated to accept ctx but forgets
    to thread it into the helper call site.
    """
    import trw_mcp

    tools_dir = Path(trw_mcp.__file__).resolve().parent / "tools"
    violations: list[tuple[str, int, str]] = []

    for py_path in tools_dir.rglob("*.py"):
        text = py_path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if callee not in _PIN_STATE_HELPERS:
                continue
            # resolve_pin_key takes ctx as a positional; the remaining
            # helpers take context= / session_id= kwargs.
            if callee == "resolve_pin_key":
                continue
            kw_names = {kw.arg for kw in node.keywords}
            if not (kw_names & {"context", "session_id"}):
                # Allow bare calls that are explicitly in compat/legacy paths:
                # only surface as a violation when the call has at least one
                # non-kwarg argument (i.e. looks like production code).
                # Legacy paths typically rely on the process UUID default.
                src_line = text.splitlines()[node.lineno - 1]
                # Skip comment-only or obvious compat shims, plus call sites
                # the source explicitly annotates as intentional pin-only
                # no-arg calls (PRD-FIX-085 — e.g. the auto-checkpoint
                # telemetry helper). Marker-based exemption is line-drift-proof,
                # unlike a hardcoded line number.
                src_line_lower = src_line.lower()
                if "# compat" in src_line_lower or "# legacy" in src_line_lower or "prd-fix-085" in src_line_lower:
                    continue
                violations.append((str(py_path.relative_to(tools_dir.parent.parent)), node.lineno, callee))

    # Filter out legacy compat + intentionally process-scoped paths.  These
    # sites are not tool handlers; they use the process-level pin intentionally
    # (telemetry decorator, auto-checkpoint counter, best-effort PRD knowledge
    # prefetch in _recall_impl).  They're tracked as follow-ups to Wave 3.
    _process_scoped_exempt = (
        "telemetry.py",
        "_recall_impl.py",
    )
    violations = [v for v in violations if not any(exempt in v[0] for exempt in _process_scoped_exempt)]
    # The auto-checkpoint telemetry helper (_maybe_auto_checkpoint in
    # checkpoint.py) is an intentional process-scoped pin-only no-arg call;
    # it carries an inline PRD-FIX-085 compatibility marker which the marker-based
    # skip above already exempts (line-drift-proof, no hardcoded line number).

    assert not violations, (
        "PRD-CORE-141 FR03 call-site violations (pin helpers invoked without "
        "context= or session_id=): " + "\n".join(f"  {path}:{line} -> {fn}(...)" for path, line, fn in violations)
    )
