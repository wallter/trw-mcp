"""FR02 — artifact existence. ``NEVER_FIRED`` / ``CONSUMER_ORPHAN`` / ``PRODUCER_ORPHAN``.

This is the only signature that would have caught the ``channels`` specimen:
12,618 LOC, a 27-entry manifest, a full lifecycle, six client adapters, and zero
rendered output. Nothing here inspects source shape — it looks for the file, or
the marker inside the file, and reports what it saw.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from trw_mcp.wiring._source import EXCLUDE_DIRS, contains_text, is_test_path
from trw_mcp.wiring.model import ContractKind, EdgeClass, Finding
from trw_mcp.wiring.registry import ArtifactContract

_SOURCE_SUFFIXES: frozenset[str] = frozenset({".py", ".sh", ".ts", ".tsx", ".toml", ".yaml", ".yml", ".json", ".mjs"})


def _lock_state(repo_root: Path, contract: ArtifactContract) -> str:
    lock_file = contract.detail_value("lock_file")
    if not lock_file:
        return "no lock file declared"
    exists = (repo_root / lock_file).exists()
    return f"lock file {lock_file} {'EXISTS' if exists else 'absent'}"


#: Where a channel's activation gate may legitimately live. `channels/` for a
#: render-time gate; `bootstrap/` because an INSTALL-time artifact is gated by
#: the installer — `distill_artifacts_entitled` governs whether the three
#: explorer subagents are written at all, and it has no business being
#: re-exported into `channels/` just to satisfy this scan.
_GATE_SOURCE_ROOT = "trw-mcp/src/trw_mcp/channels,trw-mcp/src/trw_mcp/bootstrap"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_dormancy_claim(repo_root: Path, contract: ArtifactContract) -> Finding | None:
    """Verify an ``activation_gate:`` dormancy claim instead of trusting it (OQ-02).

    This is what keeps dormancy from becoming the ``seams:`` waiver problem. A
    channel that says "I am off because flag X is off" is only excused if X is a
    real flag name that really exists in the channels package. A renamed or
    invented gate makes the claim itself the finding.

    Scoped to ``status: active`` channels, where the gate is the sole basis for
    the dormancy verdict. A channel already dormant by ``status`` does not need
    its gate field re-litigated here.
    """
    gate = contract.detail_value("activation_gate")
    status = contract.detail_value("status").strip().lower()
    if not gate or status != "active":
        return None

    malformed = not _IDENTIFIER_RE.match(gate)
    grounded = (
        False
        if malformed
        else any(contains_text(path, gate) for path in _iter_repo_sources(repo_root, _GATE_SOURCE_ROOT))
    )
    if grounded:
        return None
    reason = (
        f"activation_gate value {gate!r} is not a flag identifier"
        if malformed
        else f"activation_gate {gate!r} appears nowhere under {_GATE_SOURCE_ROOT}"
    )
    return Finding(
        contract_id=contract.contract_id,
        edge_class=EdgeClass.SCHEMA_DIVERGENCE,
        producer_side=f".trw/channels/manifest.yaml declares activation_gate: {gate}",
        consumer_side=f"{_GATE_SOURCE_ROOT} — the code that would honour the gate",
        evidence=(
            f"the channel excuses itself from producing output by naming a gate, but {reason}. "
            "An unverifiable dormancy claim suppresses the check without anything actually gating "
            "the feature — the exact failure mode a waiver field creates"
        ),
        remedy=(
            f"name the real gate flag for '{contract.contract_id}' in .trw/channels/manifest.yaml, "
            "or clear activation_gate so the channel is checked as active"
        ),
    )


def _check_channel(repo_root: Path, contract: ArtifactContract) -> Finding | None:
    """A channel's artifact is its marker, inside the instruction file it declares."""
    container = repo_root / contract.artifact_container
    marker = contract.detail_value("marker")

    if not marker:
        # A MARKER_REPLACE channel with an empty marker cannot express a render
        # at all: there is no string for it to write and none for a reader to
        # find. The contract is unsatisfiable as declared.
        return Finding(
            contract_id=contract.contract_id,
            edge_class=EdgeClass.NEVER_FIRED,
            producer_side=contract.producer,
            consumer_side=f"{contract.artifact_container} (declared render target)",
            evidence=(
                f"write_strategy={contract.detail_value('write_strategy')} into "
                f"{contract.artifact_container}, but markers.start is EMPTY — the channel declares a "
                "marker-replace contract with no marker, so no render is expressible and none has occurred"
            ),
            remedy=(
                f"fill markers.start/end for '{contract.contract_id}' in .trw/channels/manifest.yaml, "
                "or change its write_strategy to match what it actually does"
            ),
        )

    rendered = container.is_file() and contains_text(container, marker)
    evidence_base = (
        f"target {contract.artifact_container} "
        f"{'exists' if container.is_file() else 'DOES NOT EXIST'}; "
        f"marker {marker!r} {'present' if rendered else 'ABSENT'}; {_lock_state(repo_root, contract)}"
    )

    if not contract.expects_output:
        # Dormancy declared by the manifest itself (status != active). The check
        # is NOT suppressed — it is inverted. A dormant channel that rendered
        # means the manifest's own status field no longer describes reality.
        if rendered:
            return Finding(
                contract_id=contract.contract_id,
                edge_class=EdgeClass.SCHEMA_DIVERGENCE,
                producer_side=contract.producer,
                consumer_side=contract.consumer,
                evidence=(
                    f"manifest declares status={contract.detail_value('status')!r} (not active) "
                    f"yet the channel HAS rendered: {evidence_base}"
                ),
                remedy=(
                    f"the dormancy claim is stale — set status: active for '{contract.contract_id}' in "
                    ".trw/channels/manifest.yaml, or remove the rendered segment"
                ),
            )
        return None

    if rendered:
        return None
    return Finding(
        contract_id=contract.contract_id,
        edge_class=EdgeClass.NEVER_FIRED,
        producer_side=contract.producer,
        consumer_side=f"{contract.artifact_container} (declared render target)",
        evidence=f"channel status=active but has never produced output: {evidence_base}",
        remedy=(
            "wire the render path so this channel writes its marker, or set status to a "
            "non-active value in .trw/channels/manifest.yaml so the manifest stops claiming it delivers"
        ),
    )


def _iter_repo_sources(repo_root: Path, search_roots: str) -> list[Path]:
    """Non-test source files under the comma-separated ``search_roots``.

    Uses a pruning walk: excluded directories are removed from the traversal
    rather than filtered afterwards, so ``node_modules`` and ``.venv`` are never
    descended into. That is the difference between this completing in
    milliseconds and blowing NFR02's 10-second budget on its own.
    """
    found: list[Path] = []
    for raw_root in (part.strip() for part in search_roots.split(",")):
        root = repo_root / raw_root if raw_root else repo_root
        if not root.is_dir():
            continue
        for dir_path, dir_names, file_names in os.walk(root):
            dir_names[:] = sorted(name for name in dir_names if name not in EXCLUDE_DIRS and not name.startswith("."))
            current = Path(dir_path)
            for file_name in sorted(file_names):
                path = current / file_name
                if path.suffix not in _SOURCE_SUFFIXES or is_test_path(path):
                    continue
                found.append(path)
    return found


def _check_sidecar(repo_root: Path, contract: ArtifactContract) -> Finding | None:
    """A consumer whose input artifact nothing in this repository can produce.

    Source-first, on purpose. Keying the verdict on whether a matching file
    happens to sit in ``.trw/`` right now would make the result depend on
    machine-local runtime state: one developer who ran an external tool would
    see the finding clear and everyone else would not (NFR03). Source is
    versioned; ``.trw/`` is not. So the question is "can this repository
    regenerate the artifact?", and any files present go into the evidence.
    """
    pattern = contract.artifact
    token = contract.detail_value("producer_token") or pattern
    consumer_path = repo_root / contract.consumer
    producers = [
        path
        for path in _iter_repo_sources(repo_root, contract.detail_value("search_root"))
        if path != consumer_path and contains_text(path, token) and "wiring" not in path.parts
    ]
    if producers:
        return None
    matches = sorted(repo_root.glob(pattern))
    return Finding(
        contract_id=contract.contract_id,
        edge_class=EdgeClass.CONSUMER_ORPHAN,
        producer_side=f"{contract.producer} — NO producer found in this repository",
        consumer_side=contract.consumer,
        evidence=(
            f"a non-test scan of {contract.detail_value('search_root')} for the producer token {token!r} "
            "found 0 files other than the consumer itself, so nothing in this repository can emit "
            f"{pattern}; glob {pattern!r} currently matches {len(matches)} file(s) on this machine"
        ),
        remedy=(
            f"either land a producer that writes {pattern}, or delete the consumer — "
            "a strict schema mirror for an artifact nobody emits is pure maintenance cost"
        ),
    )


def _check_call_site_producer(repo_root: Path, contract: ArtifactContract) -> Finding | None:
    """A complete implementation whose declared invocation point does not reference it.

    Resolution requires BOTH the symbol and its module token in the same file
    (§_source rule 1): three unrelated features here share the name
    ``meta_tune``, so a bare-symbol match is not evidence of anything.
    """
    symbol = contract.detail_value("symbol")
    module_token = contract.detail_value("module_token")
    search_root = contract.detail_value("search_root")
    if not symbol or not module_token:
        return None

    callers = [
        path
        for path in _iter_repo_sources(repo_root, search_root)
        if contains_text(path, symbol) and contains_text(path, module_token) and path.stem != module_token
    ]
    if callers:
        return None
    return Finding(
        contract_id=contract.contract_id,
        edge_class=EdgeClass.PRODUCER_ORPHAN,
        producer_side=contract.producer,
        consumer_side=f"{contract.consumer} — NO non-test caller found",
        evidence=(
            f"non-test scan of {search_root or repo_root} found 0 files referencing both {symbol!r} "
            f"and its module token {module_token!r} outside the defining module; test files are "
            "excluded because test-only reachability is not production wiring"
        ),
        remedy=(
            f"call {symbol} from the real installer path, or delete it — "
            "a complete implementation with only test callers ships maintenance cost and no behavior"
        ),
    )


def check_existence(repo_root: Path, registry: tuple[ArtifactContract, ...]) -> list[Finding]:
    """Run every artifact-existence classification over the registry."""
    findings: list[Finding] = []
    for contract in registry:
        if contract.kind is ContractKind.CHANNEL_RENDER:
            # Dormancy-claim validation runs for EVERY channel, observable or
            # not: an unverifiable excuse is a finding regardless of whether the
            # channel's artifact happens to be inspectable.
            claim_finding = _check_dormancy_claim(repo_root, contract)
            if claim_finding is not None:
                findings.append(claim_finding)
        if not contract.observable:
            continue
        finding: Finding | None = None
        if contract.kind is ContractKind.CHANNEL_RENDER:
            finding = _check_channel(repo_root, contract)
        elif contract.kind is ContractKind.SIDECAR:
            finding = _check_sidecar(repo_root, contract)
        elif contract.kind is ContractKind.CALL_SITE and contract.detail_value("symbol"):
            finding = _check_call_site_producer(repo_root, contract)
        if finding is not None:
            findings.append(finding)
    return findings
