"""FR01 — the declared-artifact registry.

A contract is a claim that *something produces something*. The registry names
both sides and the observable output, so the checks never have to infer intent
from source shape. Entries are derived from declarations that already exist
(``.trw/channels/manifest.yaml``, the ``_PAIRS`` tuple in
``scripts/check-schema-mirror-parity.py``) plus a small hand-written core for
the contracts that have no machine-readable declaration anywhere.

Malformed entries are rejected at construction (``RegistryError``), never
skipped at scan time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from trw_mcp.wiring._manifest import ChannelDeclaration, load_channel_declarations
from trw_mcp.wiring.model import ContractKind, RegistryError


@dataclass(frozen=True)
class ArtifactContract:
    """One registered contract edge.

    Attributes:
        contract_id: stable identity, ``<kind-prefix>:<name>``.
        kind: which checker owns it.
        producer: the side responsible for writing/defining. ``file:symbol``,
            a command, or a manifest id. Never empty.
        consumer: the side that reads/depends on it. Never empty.
        artifact: the observable output — a repo-relative path, glob, or the
            marker string expected inside ``artifact_container``.
        artifact_container: file the artifact is expected to appear *inside*
            (for marker- and record-shaped artifacts).
        expects_output: ``False`` inverts the assertion — the contract is still
            checked, but producing output is what would be surprising. This is
            OQ-02's mechanism and it is never a suppression: an entry with
            ``expects_output=False`` is checked on every run.
        observable: ``False`` records a contract the detector deliberately
            cannot judge. It contributes to registry coverage and to the report
            summary, never to findings — under-claiming beats guessing.
        detail: extra checker-specific payload (marker text, tokens, symbols).
    """

    contract_id: str
    kind: ContractKind
    producer: str
    consumer: str
    artifact: str
    artifact_container: str = ""
    expects_output: bool = True
    observable: bool = True
    detail: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("contract_id", "producer", "consumer", "artifact"):
            if not str(getattr(self, name)).strip():
                raise RegistryError(
                    f"ArtifactContract.{name} must be non-empty; a contract that does not name "
                    f"both sides and its artifact cannot produce an actionable finding. Entry: {self.contract_id!r}"
                )

    def detail_value(self, key: str) -> str:
        """Return the ``detail`` value for ``key``, or ``''``."""
        for existing_key, value in self.detail:
            if existing_key == key:
                return value
        return ""


def _channel_contract(declaration: ChannelDeclaration) -> ArtifactContract:
    return ArtifactContract(
        contract_id=f"channel:{declaration.channel_id}",
        kind=ContractKind.CHANNEL_RENDER,
        producer=(
            f"trw-mcp/src/trw_mcp/channels/ render path (manifest entry "
            f"'{declaration.channel_id}', client={declaration.client})"
        ),
        consumer=declaration.target_file or f"{declaration.client} instruction surface (no file declared)",
        artifact=declaration.marker or declaration.lock_file or f"<no artifact declared for {declaration.channel_id}>",
        artifact_container=declaration.target_file,
        expects_output=not declaration.declares_dormant,
        observable=declaration.is_observable,
        detail=(
            ("marker", declaration.marker),
            ("lock_file", declaration.lock_file),
            ("status", declaration.status),
            ("client", declaration.client),
            ("write_strategy", declaration.write_strategy),
            ("activation_gate", declaration.activation_gate),
        ),
    )


# ---------------------------------------------------------------------------
# Core contracts with no machine-readable declaration of their own.
# Each cites the file:line that establishes it, so the entry is auditable.
# ---------------------------------------------------------------------------

_CORE_CONTRACTS: tuple[ArtifactContract, ...] = (
    ArtifactContract(
        contract_id="producer:trw-distill-cold-start-seed",
        kind=ContractKind.CALL_SITE,
        producer="trw-distill/trw_distill/installer_hook.py::cold_start_seed",
        consumer="the trw-distill installer path (declared: invoked after wheel install)",
        artifact="trw_distill.installer_hook.cold_start_seed",
        detail=(
            ("symbol", "cold_start_seed"),
            ("module_token", "installer_hook"),
            ("search_root", "trw-distill"),
        ),
    ),
    ArtifactContract(
        contract_id="event:session-deliver-marker",
        kind=ContractKind.EVENT_STREAM,
        producer="trw-mcp/src/trw_mcp/tools/_delivery_build_gates.py::write_session_deliver_marker",
        consumer="trw-mcp/src/trw_mcp/data/hooks/lib-trw.sh (has_recent_session_deliver)",
        artifact="trw_deliver_complete",
        artifact_container="session-events.jsonl",
        detail=(("reader_file", "trw-mcp/src/trw_mcp/data/hooks/lib-trw.sh"),),
    ),
    ArtifactContract(
        contract_id="guard:schema-mirror-parity",
        kind=ContractKind.MIRROR_PAIR,
        producer="scripts/check-schema-mirror-parity.py::_PAIRS",
        consumer="make schema-parity",
        artifact="complete coverage of every trw-mcp mirror model",
        detail=(("guard_script", "scripts/check-schema-mirror-parity.py"),),
    ),
    ArtifactContract(
        contract_id="gate:prd-core-190-wiring",
        kind=ContractKind.GATE_PREDICATE,
        producer="trw-mcp/src/trw_mcp/state/validation/_prd_scoring_wiring.py::check_wiring_gate (READ-ONLY)",
        consumer="docs/requirements-aare-f/prds/*.md",
        artifact="activation rate of the gate over its declared domain",
    ),
    ArtifactContract(
        contract_id="callsite:inert-required-inputs",
        kind=ContractKind.CALL_SITE,
        producer="trw-mcp/src/trw_mcp/**/*.py call sites",
        consumer="callees whose required inputs are passed as constant-empty literals",
        artifact="a non-empty result from a reachable branch",
        detail=(("scan_root", "trw-mcp/src/trw_mcp"), ("package", "trw_mcp")),
    ),
    ArtifactContract(
        contract_id="self:wiring-detector-invocation",
        kind=ContractKind.DETECTOR_SELF,
        producer="Makefile 'check' target",
        consumer="trw_mcp.wiring.cli",
        artifact="wiring-gate",
        artifact_container="Makefile",
    ),
)


def build_registry(repo_root: Path) -> tuple[ArtifactContract, ...]:
    """Build the full contract registry for ``repo_root``.

    Raises:
        ManifestUnavailableError: the channel manifest is missing/unparseable.
        RegistryError: a derived entry is malformed.
    """
    channels = tuple(_channel_contract(d) for d in load_channel_declarations(repo_root))
    contracts = (*channels, *_CORE_CONTRACTS)
    seen: set[str] = set()
    for contract in contracts:
        if contract.contract_id in seen:
            raise RegistryError(f"duplicate contract_id in registry: {contract.contract_id!r}")
        seen.add(contract.contract_id)
    return contracts


def contracts_of_kind(registry: tuple[ArtifactContract, ...], kind: ContractKind) -> tuple[ArtifactContract, ...]:
    """Return the registry subset owned by one checker."""
    return tuple(contract for contract in registry if contract.kind is kind)
