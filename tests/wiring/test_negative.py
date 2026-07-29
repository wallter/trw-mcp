"""Negative / fallback behaviour: every failure mode must be loud, never silent.

The single most dangerous outcome for this detector is not a missed defect — it
is a run that could not check anything and reported success. Every scenario here
asserts the difference between "found nothing" and "could not look".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.wiring._manifest import ManifestUnavailableError, load_channel_declarations
from trw_mcp.wiring.checks.existence import check_existence
from trw_mcp.wiring.cli import EXIT_INPUT_ERROR, main
from trw_mcp.wiring.model import ContractKind, EdgeClass, RegistryError
from trw_mcp.wiring.registry import ArtifactContract, build_registry


def test_missing_manifest_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ManifestUnavailableError, match="hard failure, not a skip"):
        load_channel_declarations(tmp_path)
    assert main(["--repo-root", str(tmp_path)]) == EXIT_INPUT_ERROR


def test_malformed_manifest_fails_loudly(tmp_path: Path) -> None:
    channels = tmp_path / ".trw/channels"
    channels.mkdir(parents=True)
    (channels / "manifest.yaml").write_text("channels: []\n", encoding="utf-8")
    with pytest.raises(ManifestUnavailableError, match="declares no 'channels' list"):
        load_channel_declarations(tmp_path)


def test_unparseable_manifest_fails_loudly(tmp_path: Path) -> None:
    channels = tmp_path / ".trw/channels"
    channels.mkdir(parents=True)
    (channels / "manifest.yaml").write_text("channels: [unclosed\n", encoding="utf-8")
    with pytest.raises(ManifestUnavailableError, match="could not be parsed"):
        load_channel_declarations(tmp_path)


def test_malformed_registry_entry_rejected() -> None:
    """Rejection happens at construction, not at scan time."""
    for missing in ("contract_id", "producer", "consumer", "artifact"):
        kwargs = {
            "contract_id": "x",
            "kind": ContractKind.SIDECAR,
            "producer": "p",
            "consumer": "c",
            "artifact": "a",
        }
        kwargs[missing] = ""
        with pytest.raises(RegistryError, match=missing):
            ArtifactContract(**kwargs)  # type: ignore[arg-type]


def test_duplicate_contract_ids_rejected(repo_root: Path) -> None:
    """Two entries with the same id would let one silently mask the other's finding."""
    registry = build_registry(repo_root)
    assert len({c.contract_id for c in registry}) == len(registry)


def test_dormancy_claim_must_be_verifiable(repo_root: Path, tmp_path: Path) -> None:
    """OQ-02: a dormancy declaration naming a nonexistent gate is itself a finding.

    This is the property that stops "dormant by flag" becoming the ``seams:``
    waiver problem, where a field nobody fills in silently disables a check.
    """
    contract = next(
        c
        for c in build_registry(repo_root)
        if c.kind is ContractKind.CHANNEL_RENDER and c.detail_value("activation_gate")
    )
    bogus = ArtifactContract(
        contract_id="channel:bogus-gate",
        kind=ContractKind.CHANNEL_RENDER,
        producer=contract.producer,
        consumer=contract.consumer,
        artifact=contract.artifact,
        artifact_container=contract.artifact_container,
        observable=False,
        detail=(("activation_gate", "flag_that_does_not_exist_anywhere"), ("status", "active")),
    )
    findings = check_existence(repo_root, (bogus,))
    assert [f.edge_class for f in findings] == [EdgeClass.SCHEMA_DIVERGENCE]
    assert "unverifiable dormancy claim" in findings[0].evidence


def test_verifiable_dormancy_claim_is_accepted(repo_root: Path) -> None:
    """``cc03_hook_enabled`` is a real, default-off flag — the claim checks out.

    Dormancy is a registry *attribute*, checked on every run, not a suppression.
    """
    contract = next(c for c in build_registry(repo_root) if c.contract_id == "channel:cc-03-pretooluse-hint")
    assert contract.detail_value("activation_gate") == "cc03_hook_enabled"
    findings = [f for f in check_existence(repo_root, (contract,)) if f.contract_id == contract.contract_id]
    assert not findings, [f.render() for f in findings]


def test_unobservable_contract_never_produces_a_finding(repo_root: Path) -> None:
    """Under-claiming: a contract the detector cannot judge stays silent, not guessed."""
    unobservable = tuple(
        c for c in build_registry(repo_root) if not c.observable and c.kind is ContractKind.CHANNEL_RENDER
    )
    assert unobservable, "fixture drift: expected some unobservable channel contracts"
    findings = check_existence(repo_root, unobservable)
    assert all(f.edge_class is EdgeClass.SCHEMA_DIVERGENCE for f in findings), (
        "an unobservable contract produced an artifact-existence finding"
    )
