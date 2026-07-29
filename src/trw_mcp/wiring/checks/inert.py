"""FR05 — inert-branch check. ``INERT_BRANCH``.

A reachable path that is structurally incapable of producing a result. The
fixture is ``trw-mcp/src/trw_mcp/tools/code_search.py`` line 24::

    return response_to_dict(rank_semantic_chunks(query=query, chunks=(), embedder=None))

``mode="semantic"`` is registered, callable, statically live, listed in four
agents' frontmatter — and permanently empty. **No reachability analysis can see
this, because the branch is reachable.** That is precisely why the signature has
to be structural rather than graph-based.

Three independent conditions must all hold before anything is reported, and each
one exists to kill a specific false-positive family:

1. At least ``inert_min_empty_kwargs`` (default 2) keyword arguments are
   constant-empty literals. One ``x=None`` is an ordinary optional argument.
2. The callee resolves through a **fully-qualified import path** into
   ``trw_mcp``. A bare-symbol match is not evidence: three unrelated features in
   this repository are named ``meta_tune``.
3. **Every** constant-empty argument lands on a parameter the callee declares
   with **no default**. Passing ``None`` to a parameter that already defaults to
   ``None`` is a no-op, not a defect.

An unresolvable callee produces no finding. Under-claiming is the rule
(``test_dynamic_dispatch_prefers_under_claiming``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.wiring._source import iter_python_files, module_path_for, parse_module, resolve_imports
from trw_mcp.wiring.model import EdgeClass, Finding
from trw_mcp.wiring.registry import ArtifactContract

_EMPTY_FACTORIES: frozenset[str] = frozenset({"tuple", "list", "dict", "set", "frozenset"})


def is_constant_empty(node: ast.expr) -> bool:
    """True when ``node`` is a literal that carries no data."""
    if isinstance(node, ast.Constant):
        return node.value is None or node.value == ""
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name) and node.func.id in _EMPTY_FACTORIES and not node.args and not node.keywords
        )
    return False


@dataclass(frozen=True)
class _Signature:
    """Parameter names of a callee, split by whether they carry a default."""

    required: frozenset[str]
    optional: frozenset[str]


def _signature_of(function: ast.FunctionDef | ast.AsyncFunctionDef) -> _Signature:
    args = function.args
    positional = [*args.posonlyargs, *args.args]
    defaulted_count = len(args.defaults)
    required = {arg.arg for arg in positional[: len(positional) - defaulted_count]}
    optional = {arg.arg for arg in positional[len(positional) - defaulted_count :]}
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        (optional if default is not None else required).add(arg.arg)
    return _Signature(required=frozenset(required), optional=frozenset(optional))


def _lookup_signature(package_root: Path, qualified: str, package: str) -> _Signature | None:
    """Resolve ``pkg.mod.func`` to its parameter signature, or ``None``."""
    module_dotted, _, symbol = qualified.rpartition(".")
    module_file = module_path_for(package_root, module_dotted, package)
    if module_file is None:
        return None
    tree = parse_module(module_file)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == symbol:
            return _signature_of(node)
    return None


def _result_is_used(module: ast.Module, call: ast.Call) -> bool:
    """True when the call's value is returned or bound — an ignored result is not a branch."""
    for node in ast.walk(module):
        if isinstance(node, ast.Return | ast.Assign | ast.AnnAssign) and any(child is call for child in ast.walk(node)):
            return True
    return False


def check_inert(
    repo_root: Path,
    contract: ArtifactContract,
    *,
    max_bytes: int,
    min_empty_kwargs: int,
) -> list[Finding]:
    """Scan the declared root for call sites that starve a callee's required inputs."""
    scan_root = repo_root / contract.detail_value("scan_root")
    package = contract.detail_value("package")
    package_root = scan_root.parent
    findings: list[Finding] = []

    for path in iter_python_files(scan_root, max_bytes=max_bytes):
        tree = parse_module(path)
        if tree is None:
            continue
        imports = resolve_imports(tree)
        relative = path.relative_to(repo_root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            empty = [kw for kw in node.keywords if kw.arg and is_constant_empty(kw.value)]
            if len(empty) < min_empty_kwargs:
                continue
            imported = imports.get(node.func.id)
            if imported is None:
                continue  # unresolved callee → no finding, deliberately
            signature = _lookup_signature(package_root, imported.qualified, package)
            if signature is None:
                continue
            starved = {kw.arg for kw in empty if kw.arg in signature.required}
            if len(starved) != len(empty) or not starved:
                continue
            if not _result_is_used(tree, node):
                continue
            findings.append(
                Finding(
                    # Identity is (file, callee), NOT (file, line). A line number
                    # would make the finding key churn on every unrelated edit
                    # above the call site, turning the baseline into noise. The
                    # line lives in the evidence, where a reader needs it.
                    contract_id=f"{contract.contract_id}:{relative}:{imported.qualified.rpartition('.')[2]}",
                    edge_class=EdgeClass.INERT_BRANCH,
                    producer_side=f"{imported.qualified} — declares {', '.join(sorted(starved))} as required",
                    consumer_side=f"{relative}:{node.lineno} calls it and uses the result",
                    evidence=(
                        f"every required input is supplied as a constant-empty literal: "
                        f"{', '.join(f'{kw.arg}=' + ast.unparse(kw.value) for kw in sorted(empty, key=lambda k: k.arg or ''))}. "
                        "The branch is reachable and registered, so no reachability analysis flags it, "
                        "yet it cannot return a result under any input"
                    ),
                    remedy=(
                        f"supply real values at {relative}:{node.lineno}, or remove the branch and the option "
                        "that selects it so callers stop being offered a mode that cannot work"
                    ),
                )
            )
    return findings
