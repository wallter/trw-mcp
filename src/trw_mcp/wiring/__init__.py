"""Observation-based wiring detector (PRD-CORE-232 Phase A).

Every existing verification surface in this repository checks that a thing
*exists* and is *well-formed*. Those are exactly the properties a 90%-complete,
never-connected feature already has. This package checks whether a declared
contract *produced anything*.

The governing rule:

    Declaration-based gates fail silently when nobody declares.
    Reachability-based gates report green on registered-but-inert code.
    Artifact-existence gates cannot do either: the file is there or it is not.

Entry point: ``python -m trw_mcp.wiring.cli`` (wired into ``make check``,
enforcing by default, and asserting its own invocation).
"""

from __future__ import annotations

from trw_mcp.wiring.detector import DetectorResult, run_detector
from trw_mcp.wiring.model import ContractKind, EdgeClass, Finding, RegistryError
from trw_mcp.wiring.registry import ArtifactContract, build_registry

__all__ = [
    "ArtifactContract",
    "ContractKind",
    "DetectorResult",
    "EdgeClass",
    "Finding",
    "RegistryError",
    "build_registry",
    "run_detector",
]
