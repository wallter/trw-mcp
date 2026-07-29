"""FR03 — complete-coverage assertion. A guard covering a subset reports green."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.wiring.checks.coverage import (
    check_coverage,
    discover_mirror_classes,
    registered_mirror_classes,
)
from trw_mcp.wiring.config import DEFAULT_CONFIG
from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import ContractKind, EdgeClass
from trw_mcp.wiring.registry import build_registry

GUARD_SCRIPT = "scripts/check-schema-mirror-parity.py"


def test_mirror_coverage_is_now_complete(repo_root: Path, live_result: DetectorResult) -> None:
    """The parity gap closed as a side effect of removing trw_entity_risk_map.

    This asserted 6 registered of 7 discovered, with `EntityRiskScorePayload`
    named as the single uncovered mirror. It was uncovered for a structural
    reason: a producerless sidecar has no trw-distill source class to compare
    against, so it could never be registered as a MirrorPair. Removing the tool
    (UF-011, 2026-07-29) removed the mirror, and the guard now covers 6 of 6 —
    one removal closed two ledger entries, UF-011 and the PARTIAL_GUARD finding
    it caused here.

    The assertion is inverted rather than deleted: this test now pins that the
    guard is COMPLETE, so a newly added mirror that nobody registers fails here
    instead of quietly reopening the gap.
    """
    registered = registered_mirror_classes(repo_root / GUARD_SCRIPT)
    discovered = discover_mirror_classes(repo_root, max_bytes=DEFAULT_CONFIG.max_source_bytes)

    assert set(discovered) == set(registered), (
        "every mirror model in tools/ must be registered in the parity guard's "
        f"_PAIRS; unregistered: {sorted(set(discovered) - set(registered))}"
    )
    assert discovered, "no mirror models discovered — the scan has rotted"

    assert not [f for f in live_result.findings if f.contract_id == "guard:schema-mirror-parity"], (
        "the parity guard reported a gap again; add the missing pair to _PAIRS"
    )


def test_typed_dict_payloads_are_not_treated_as_mirrors(repo_root: Path) -> None:
    """Only Pydantic ``BaseModel`` subclasses are mirrors — the guard compares JSON Schema.

    ``SubmissionPayload`` and friends are ``TypedDict``s; counting them would
    manufacture uncoverable findings.
    """
    discovered = discover_mirror_classes(repo_root, max_bytes=DEFAULT_CONFIG.max_source_bytes)
    assert "SubmissionPayload" not in discovered


def _synthetic_root(tmp_path: Path, *, mirrors: tuple[str, ...], registered: tuple[str, ...]) -> Path:
    tools = tmp_path / "trw-mcp/src/trw_mcp/tools"
    tools.mkdir(parents=True)
    (tools / "models.py").write_text(
        "from pydantic import BaseModel\n\n" + "\n".join(f"class {name}(BaseModel):\n    pass\n" for name in mirrors),
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "guard.py").write_text(
        "_PAIRS = (\n" + "".join(f"    MirrorPair(mirror_class={name!r}),\n" for name in registered) + ")\n",
        encoding="utf-8",
    )
    return tmp_path


def _mirror_contract(repo_root: Path, guard_relative: str) -> object:
    contract = next(c for c in build_registry(repo_root) if c.kind is ContractKind.MIRROR_PAIR)
    return type(contract)(
        contract_id=contract.contract_id,
        kind=contract.kind,
        producer=contract.producer,
        consumer=contract.consumer,
        artifact=contract.artifact,
        detail=(("guard_script", guard_relative),),
    )


def test_full_coverage_produces_no_finding(repo_root: Path, tmp_path: Path) -> None:
    """A guard that registers every discovered mirror is silent — the check is not vacuous."""
    names = ("AlphaPayload", "BetaPayload")
    root = _synthetic_root(tmp_path, mirrors=names, registered=names)
    findings = check_coverage(
        root,
        _mirror_contract(repo_root, "scripts/guard.py"),  # type: ignore[arg-type]
        max_bytes=DEFAULT_CONFIG.max_source_bytes,
    )
    assert not findings, f"full coverage still produced findings: {[f.key for f in findings]}"


def test_partial_coverage_on_a_synthetic_root_names_the_gap(repo_root: Path, tmp_path: Path) -> None:
    """The same code path fires when one mirror is dropped — proving the check discriminates."""
    root = _synthetic_root(tmp_path, mirrors=("AlphaPayload", "BetaPayload"), registered=("AlphaPayload",))
    findings = check_coverage(
        root,
        _mirror_contract(repo_root, "scripts/guard.py"),  # type: ignore[arg-type]
        max_bytes=DEFAULT_CONFIG.max_source_bytes,
    )
    assert [f.edge_class for f in findings] == [EdgeClass.PARTIAL_GUARD]
    assert "BetaPayload" in findings[0].evidence


def test_missing_guard_script_is_a_finding(repo_root: Path) -> None:
    """A guard that no longer exists is worse than a partial one, not better."""
    contract = next(c for c in build_registry(repo_root) if c.kind is ContractKind.MIRROR_PAIR)
    broken = type(contract)(
        contract_id=contract.contract_id,
        kind=contract.kind,
        producer=contract.producer,
        consumer=contract.consumer,
        artifact=contract.artifact,
        detail=(("guard_script", "scripts/does-not-exist.py"),),
    )
    findings = check_coverage(repo_root, broken, max_bytes=DEFAULT_CONFIG.max_source_bytes)
    assert [f.edge_class for f in findings] == [EdgeClass.NEVER_FIRED]
