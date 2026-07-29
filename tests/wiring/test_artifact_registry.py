"""FR01 — the declared-artifact registry is derived, not authored."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.wiring._manifest import ManifestUnavailableError, load_channel_declarations
from trw_mcp.wiring.model import ContractKind, RegistryError
from trw_mcp.wiring.registry import ArtifactContract, build_registry, contracts_of_kind

# Channel entries from this repo's OWN installed manifest, plus the six
# mirror/sidecar/event/gate/callsite/self core contracts. PRD-CORE-239 FR01 took
# the channel side from 27 to 17. The local manifest briefly carried only 15 —
# it was missing copilot-pretooluse-hint and cursor-pretooluse-hint, the same
# stale-input gap §3b's "Scope gap" section named — and was reconciled against
# the six bundled manifests on 2026-07-28.
MINIMUM_ENTRIES = 23


def test_registry_covers_known_contracts(repo_root: Path) -> None:
    registry = build_registry(repo_root)
    assert len(registry) >= MINIMUM_ENTRIES, (
        f"registry has {len(registry)} entries, expected at least {MINIMUM_ENTRIES}"
    )

    channels = contracts_of_kind(registry, ContractKind.CHANNEL_RENDER)
    assert len(channels) == 17, f"expected 17 channel contracts derived from the manifest, got {len(channels)}"

    for kind in (
        ContractKind.MIRROR_PAIR,
        ContractKind.EVENT_STREAM,
        ContractKind.GATE_PREDICATE,
        ContractKind.DETECTOR_SELF,
    ):
        assert contracts_of_kind(registry, kind), f"no contract registered for {kind.value}"

    # SIDECAR is deliberately absent from that loop. `sidecar:entity-risk-map`
    # was the only contract of that kind, and removing `trw_entity_risk_map`
    # (UF-011, 2026-07-29) took it with them. The KIND remains valid and
    # expressible — the next sidecar consumer registers one — but requiring a
    # live instance would make this test demand that a defect exist, which is
    # the failure mode the NEVER_FIRED specimens already taught us.
    assert not contracts_of_kind(registry, ContractKind.SIDECAR), (
        "a SIDECAR contract was registered again — add it back to the loop above "
        "so the kind is covered by a live instance rather than by this comment"
    )


def test_channel_entries_are_derived_from_the_manifest(repo_root: Path) -> None:
    """OQ-01: entries come from a file the subsystem already maintains.

    A separate hand-filled manifest would recreate the failure this detector
    exists to catch — measured across the corpus, zero PRDs populated the
    optional wiring fields before 2026-07-24.
    """
    declarations = load_channel_declarations(repo_root)
    registry_ids = {contract.contract_id for contract in build_registry(repo_root)}
    for declaration in declarations:
        assert f"channel:{declaration.channel_id}" in registry_ids


def test_detector_registers_itself(repo_root: Path) -> None:
    """FR08: the detector's own entry is in its own registry source."""
    self_contracts = contracts_of_kind(build_registry(repo_root), ContractKind.DETECTOR_SELF)
    assert len(self_contracts) == 1
    assert self_contracts[0].consumer == "trw_mcp.wiring.cli"


def test_malformed_registry_entry_rejected() -> None:
    """Rejected at construction, not skipped at scan time."""
    with pytest.raises(RegistryError, match="producer"):
        ArtifactContract(
            contract_id="broken",
            kind=ContractKind.SIDECAR,
            producer="   ",
            consumer="somebody",
            artifact="something.json",
        )


def test_missing_manifest_fails_loudly(tmp_path: Path) -> None:
    """'the check could not run' must never look like 'the check found nothing'."""
    with pytest.raises(ManifestUnavailableError, match="channel manifest not found"):
        build_registry(tmp_path)


def test_unobservable_contracts_are_registered_not_dropped(repo_root: Path) -> None:
    """Contracts the detector cannot judge stay counted and visible.

    Silently dropping them would let the registry shrink invisibly; keeping them
    marked unobservable makes the coverage gap a reported number instead.
    """
    registry = build_registry(repo_root)
    unobservable = [contract for contract in registry if not contract.observable]
    assert unobservable, "expected some contracts to declare no observable artifact"
    assert all(contract.contract_id for contract in unobservable)


def test_a_kind_with_no_observable_subject_is_disclosed(repo_root: Path) -> None:
    """A check that examined nothing must not read as a check that passed.

    PRD-CORE-239 FR01 removed the 12 marker-replace channels. Every one of the
    15 survivors declares EPHEMERAL_STDOUT / FULL_REWRITE / NONE /
    JSON_KEY_MERGE, so `ChannelDeclaration.is_observable` is False for all of
    them and `check_existence` skips `_check_channel` before it runs. The
    detector's `channel:*` result became an empty loop — and that PASS was cited
    as the verification for the whole removal, in the PRD, the CHANGELOG and the
    defect ledger.

    The fix is disclosure, not a threshold. Asserting `observable > 0` would
    fail permanently, because zero IS the correct post-removal state: no
    surviving channel declares a marker contract. What was wrong was that
    silence and assurance looked identical from outside.

    This test fails if that disclosure is ever dropped while the condition holds.
    """
    from trw_mcp.wiring.baseline import partition
    from trw_mcp.wiring.detector import run_detector
    from trw_mcp.wiring.report import render_report

    result = run_detector(repo_root)
    channel_contracts = [c for c in result.registry if c.kind is ContractKind.CHANNEL_RENDER]
    assert channel_contracts, "no channel contracts at all — the fixture has rotted"

    report = render_report(result, partition(list(result.findings)))

    if not any(c.observable for c in channel_contracts):
        assert "CHECKS THAT EXAMINED NOTHING" in report, (
            "every channel contract is unobservable, so the channel check ran "
            "zero times — the report must say so rather than presenting a clean "
            "result that silently excludes them"
        )
        assert "channel_render" in report
