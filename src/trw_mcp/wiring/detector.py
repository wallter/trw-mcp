"""The detector: registry + six observational checks + one self-check.

Nothing here builds a call graph. Measured on this codebase, static reachability
recalls roughly 1 specimen in 8, because the specimens are *reachable* code that
produces nothing — ``register_channel_stats_tools`` IS called at boot through
the registrar tuple, and ``trw_channel_stats`` IS registered and callable.
Reachability cannot distinguish "registered and callable" from "actually
produces anything". Artifact existence can.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.wiring.checks.coverage import check_coverage
from trw_mcp.wiring.checks.existence import check_existence
from trw_mcp.wiring.checks.inert import check_inert
from trw_mcp.wiring.checks.predicate import check_predicate_coverage
from trw_mcp.wiring.checks.schema import check_schema
from trw_mcp.wiring.config import DEFAULT_CONFIG, WiringDetectorConfig
from trw_mcp.wiring.model import ContractKind, Finding
from trw_mcp.wiring.registry import ArtifactContract, build_registry
from trw_mcp.wiring.selfcheck import check_self_invocation


@dataclass(frozen=True)
class DetectorResult:
    """Everything one repo-wide run observed."""

    findings: tuple[Finding, ...]
    registry: tuple[ArtifactContract, ...]
    duration_seconds: float

    @property
    def observable_count(self) -> int:
        return sum(1 for contract in self.registry if contract.observable)

    @property
    def unobservable_count(self) -> int:
        return sum(1 for contract in self.registry if not contract.observable)


def run_detector(repo_root: Path, config: WiringDetectorConfig = DEFAULT_CONFIG) -> DetectorResult:
    """Run every check over ``repo_root`` and return the classified findings.

    Deterministic: no network, no clock-dependent branching, sorted traversal
    everywhere. The elapsed time in the result is reported, never branched on.
    """
    started = time.monotonic()
    registry = build_registry(repo_root)
    findings: list[Finding] = list(check_existence(repo_root, registry))

    for contract in registry:
        if contract.kind is ContractKind.MIRROR_PAIR:
            findings.extend(check_coverage(repo_root, contract, max_bytes=config.max_source_bytes))
        elif contract.kind is ContractKind.EVENT_STREAM:
            findings.extend(check_schema(repo_root, contract, max_bytes=config.max_source_bytes))
        elif contract.kind is ContractKind.GATE_PREDICATE:
            findings.extend(
                check_predicate_coverage(
                    repo_root,
                    contract,
                    floor=config.predicate_coverage_floor,
                    prd_glob=config.prd_glob,
                )
            )
        elif contract.kind is ContractKind.CALL_SITE and contract.detail_value("scan_root"):
            findings.extend(
                check_inert(
                    repo_root,
                    contract,
                    max_bytes=config.max_source_bytes,
                    min_empty_kwargs=config.inert_min_empty_kwargs,
                )
            )
        elif contract.kind is ContractKind.DETECTOR_SELF:
            findings.extend(check_self_invocation(repo_root, contract))

    findings.sort(key=lambda finding: finding.key)
    return DetectorResult(
        findings=tuple(findings),
        registry=registry,
        duration_seconds=time.monotonic() - started,
    )
