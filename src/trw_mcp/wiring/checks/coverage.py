"""FR03 — complete-coverage assertion. ``PARTIAL_GUARD``.

A guard that covers a subset of what exists reports green while the uncovered
members drift. ``scripts/check-schema-mirror-parity.py`` is the fixture: it
registers four ``MirrorPair`` entries, and the mirrors it does not register can
diverge from their trw-distill sources silently — which has already happened
once (``FileRiskScorePayload``, learning ``L-LVIe``).

Discovery is mechanical and uses the guard script's *own* documented naming
convention: "``<Source>Payload`` mirrors ``<Source>``". Every Pydantic
``BaseModel`` subclass under ``trw-mcp/src/trw_mcp/tools/`` whose name ends in
``Payload`` is a mirror. ``TypedDict`` payload helpers are excluded by the base
check, which is what keeps this from over-firing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from trw_mcp.wiring._source import iter_python_files, parse_module
from trw_mcp.wiring.model import EdgeClass, Finding
from trw_mcp.wiring.registry import ArtifactContract

MIRROR_SUFFIX = "Payload"
MIRROR_ROOT = "trw-mcp/src/trw_mcp/tools"
_MIRROR_BASES: frozenset[str] = frozenset({"BaseModel"})


def discover_mirror_classes(repo_root: Path, *, max_bytes: int) -> tuple[str, ...]:
    """Return every ``*Payload(BaseModel)`` class name under the tools package."""
    discovered: set[str] = set()
    for path in iter_python_files(repo_root / MIRROR_ROOT, max_bytes=max_bytes):
        tree = parse_module(path)
        if tree is None:
            continue
        for node in tree.body:  # top-level classes only — nested helpers are not mirrors
            if not isinstance(node, ast.ClassDef) or not node.name.endswith(MIRROR_SUFFIX):
                continue
            base_names = {base.id for base in node.bases if isinstance(base, ast.Name)}
            base_names |= {base.attr for base in node.bases if isinstance(base, ast.Attribute)}
            if base_names & _MIRROR_BASES:
                discovered.add(node.name)
    return tuple(sorted(discovered))


def registered_mirror_classes(guard_script: Path) -> tuple[str, ...]:
    """Return the ``mirror_class=`` values registered in the guard's ``_PAIRS`` tuple."""
    tree = parse_module(guard_script)
    if tree is None:
        return ()
    registered: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == "mirror_class" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                if isinstance(value, str):
                    registered.add(value)
    return tuple(sorted(registered))


def check_coverage(repo_root: Path, contract: ArtifactContract, *, max_bytes: int) -> list[Finding]:
    """Classify the mirror-parity guard as ``PARTIAL_GUARD`` when it covers a subset."""
    guard_relative = contract.detail_value("guard_script")
    guard_script = repo_root / guard_relative
    if not guard_script.is_file():
        return [
            Finding(
                contract_id=contract.contract_id,
                edge_class=EdgeClass.NEVER_FIRED,
                producer_side=contract.producer,
                consumer_side=contract.consumer,
                evidence=f"guard script {guard_relative} does not exist, so nothing checks mirror parity at all",
                remedy=f"restore {guard_relative} or remove the 'make schema-parity' target that claims to run it",
            )
        ]

    discovered = discover_mirror_classes(repo_root, max_bytes=max_bytes)
    registered = registered_mirror_classes(guard_script)
    uncovered = tuple(name for name in discovered if name not in set(registered))
    if not uncovered:
        return []
    return [
        Finding(
            contract_id=contract.contract_id,
            edge_class=EdgeClass.PARTIAL_GUARD,
            producer_side=f"{guard_relative}::_PAIRS registers {len(registered)} pairs: {', '.join(registered)}",
            consumer_side=f"{MIRROR_ROOT} declares {len(discovered)} mirror models: {', '.join(discovered)}",
            evidence=(
                f"{len(registered)} of {len(discovered)} mirror models are covered; UNCOVERED: "
                f"{', '.join(uncovered)}. An unregistered mirror is treated as already drifted — "
                "nothing compares it to its trw-distill source, so a divergence is invisible until a "
                "consumer breaks (precedent: FileRiskScorePayload, learning L-LVIe)"
            ),
            remedy=(
                f"append a MirrorPair for each of {', '.join(uncovered)} to _PAIRS in {guard_relative}, "
                "or delete the mirror model if its tool no longer surfaces a trw-distill result"
            ),
        )
    ]
