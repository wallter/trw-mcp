"""AARE-F 3.2 PRD contract, cache, template, and tool-boundary regressions."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._test_tools_requirements_support import _get_tools, set_project_root  # noqa: F401


def _mapping(requirement_id: str, method: str = "test") -> dict[str, object]:
    return {
        "requirement_id": requirement_id,
        "acceptance_criteria": ["Given a request, When the behavior runs, Then the asserted value is correct"],
        "method": method,
        "evidence_artifact": "tests/test_behavior.py::test_behavior",
        "pass_condition": "The asserted observable value equals the requirement target",
        "automated": True,
    }


def _contract_prd(
    extra_frontmatter: str = "",
    fr_body: str = "The system shall expose the requested behavior.",
    *,
    risk_level: str = "low",
    template_version: str = "3.2",
) -> str:
    return f"""---
prd:
  id: PRD-CORE-901
  title: AARE-F 3.2 contract
  version: '1.0'
  status: draft
  priority: P2
  category: CORE
risk_level: {risk_level}
template_version: '{template_version}'
traceability:
  implements: [REQ-901]
verification:
  mappings:
    - requirement_id: PRD-CORE-901-FR01
      acceptance_criteria:
        - Given a request, When the behavior runs, Then the asserted value is correct
      method: test
      evidence_artifact: tests/test_behavior.py::test_behavior
      pass_condition: The asserted value equals the requirement target
      automated: true
{extra_frontmatter}---

# PRD-CORE-901: AARE-F 3.2 contract

## 1. Problem Statement
The contract must retain current repository truth.

## 4. Functional Requirements

### PRD-CORE-901-FR01: Observable behavior
**Priority**: Must Have
**Status**: active
{fr_body}
"""


def _validate(tmp_path: Path, content: str, name: str = "prd.md", *, verbose: bool = False) -> dict[str, object]:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return _get_tools()["trw_prd_validate"].fn(prd_path=str(path), verbose=verbose)


def test_cache_rechecks_repository_path_when_file_appears(tmp_path: Path) -> None:
    content = _contract_prd(fr_body="Implementation: `src/dynamic_target.py`\nTest: `tests/test_behavior.py`")

    first = _validate(tmp_path, content)
    assert any(failure["rule"] == "repo_path_exists" for failure in first["failures"])

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "dynamic_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_behavior.py").write_text("def test_behavior():\n    assert True\n", encoding="utf-8")

    second = _validate(tmp_path, content)
    assert second["cache"]["hit"] is True
    assert not any(failure["rule"] == "repo_path_exists" for failure in second["failures"])

    (tmp_path / "src" / "dynamic_target.py").unlink()
    third = _validate(tmp_path, content)
    assert third["cache"]["hit"] is True
    assert any(failure["rule"] == "repo_path_exists" for failure in third["failures"])


def test_cache_rechecks_wiring_reachability_when_test_appears(tmp_path: Path) -> None:
    content = _contract_prd(
        extra_frontmatter="ip_tier: public\n",
        fr_body="wiring_test: tests/test_dynamic_wiring.py::test_dynamic_wiring",
    )
    # Token-bloat W5: assert the FR03 un-truncated wiring set via verbose mode
    # (compact mode dedups messages already echoed in improvement_suggestions).
    first = _validate(tmp_path, content, verbose=True)
    assert any("does not exist" in warning for warning in first["wiring_gate_warnings"])

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_dynamic_wiring.py").write_text(
        "def test_dynamic_wiring():\n    assert True\n",
        encoding="utf-8",
    )
    second = _validate(tmp_path, content, verbose=True)
    assert second["cache"]["hit"] is True
    assert not any("does not exist" in warning for warning in second["wiring_gate_warnings"])


def test_cache_key_changes_for_any_config_change(tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig, reload_config

    content = _contract_prd()
    try:
        reload_config(TRWConfig(validation_density_weight=30.0))
        # Token-bloat W5: cache.key is a verbose-only debug field.
        first = _validate(tmp_path, content, verbose=True)
        reload_config(TRWConfig(validation_density_weight=31.0))
        second = _validate(tmp_path, content, verbose=True)
    finally:
        reload_config(None)

    assert first["cache"]["key"] != second["cache"]["key"]
    assert second["cache"]["hit"] is False


def test_cache_rechecks_seam_expiry_rollover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.state.validation._prd_scoring_wiring as wiring

    class BeforeExpiry(date):
        @classmethod
        def today(cls) -> BeforeExpiry:
            return cls(2026, 7, 9)

    class AfterExpiry(date):
        @classmethod
        def today(cls) -> AfterExpiry:
            return cls(2026, 7, 11)

    content = _contract_prd(
        extra_frontmatter=(
            "ip_tier: public\n"
            "seams:\n"
            "  - kind: deferred\n"
            "    target_prd: PRD-CORE-999\n"
            "    owner: platform\n"
            "    expiry_date: '2026-07-10'\n"
        )
    )
    monkeypatch.setattr(wiring, "date", BeforeExpiry)
    # verbose mode asserts the FR03 un-truncated wiring set (see W5 note above).
    first = _validate(tmp_path, content, verbose=True)
    assert not any("expired" in warning for warning in first["wiring_gate_warnings"])

    monkeypatch.setattr(wiring, "date", AfterExpiry)
    second = _validate(tmp_path, content, verbose=True)
    assert second["cache"]["hit"] is True
    assert any("expired" in warning for warning in second["wiring_gate_warnings"])


@pytest.mark.parametrize("method", ["test", "analysis", "inspection", "demonstration"])
def test_verification_mapping_model_accepts_aaref_methods(method: str) -> None:
    from trw_mcp.models.requirements import VerificationMapping

    mapping = VerificationMapping.model_validate(_mapping("PRD-CORE-001-FR01", method), strict=False)
    assert mapping.method == method


def test_prd_create_round_trips_typed_verification_mappings(tmp_path: Path) -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter

    mappings = [
        _mapping("PRD-CORE-001-FR01"),
        _mapping("PRD-CORE-001-FR02", "inspection"),
        _mapping("PRD-CORE-001-NFR01", "analysis"),
        _mapping("PRD-CORE-001-NFR02", "demonstration"),
        _mapping("PRD-CORE-001-NFR03", "inspection"),
    ]
    result = _get_tools()["trw_prd_create"].fn(
        input_text="Add observable verification behavior",
        category="CORE",
        priority="P1",
        title="Typed Verification",
        verification_mappings=mappings,
    )
    frontmatter = parse_frontmatter(result["content"])
    assert frontmatter["template_version"] == "3.2"
    round_tripped = frontmatter["verification"]["mappings"]
    assert [{key: item[key] for key in mappings[index]} for index, item in enumerate(round_tripped)] == mappings

    validated = _get_tools()["trw_prd_validate"].fn(prd_path=result["output_path"])
    assert validated["verification_mapping_coverage"] == 1.0
    assert not any(failure["rule"] == "verification_mapping_required" for failure in validated["failures"])


def test_current_high_risk_prd_blocks_without_verification_mapping() -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_validation import validate_verification_mappings

    content = _contract_prd(risk_level="high").replace(
        "verification:\n  mappings:\n    - requirement_id: PRD-CORE-901-FR01\n"
        "      acceptance_criteria:\n"
        "        - Given a request, When the behavior runs, Then the asserted value is correct\n"
        "      method: test\n"
        "      evidence_artifact: tests/test_behavior.py::test_behavior\n"
        "      pass_condition: The asserted value equals the requirement target\n"
        "      automated: true\n",
        "verification:\n  mappings: []\n",
    )
    failures, coverage = validate_verification_mappings(
        parse_frontmatter(content),
        content,
        effective_risk_level="high",
    )
    assert coverage == 0.0
    assert any(failure.rule == "verification_mapping_required" and failure.severity == "error" for failure in failures)


@pytest.mark.parametrize("risk_level", ["high", "critical"])
def test_current_high_risk_prd_requires_exact_nfr_mapping(risk_level: str) -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_validation import validate_verification_mappings

    content = (
        _contract_prd(risk_level=risk_level)
        + """

## 5. Non-Functional Requirements

### PRD-CORE-901-NFR01: Bounded latency
The system shall complete the behavior within 200 milliseconds at p99.
"""
    )
    failures, coverage = validate_verification_mappings(
        parse_frontmatter(content),
        content,
        effective_risk_level=risk_level,
    )

    assert coverage == 0.5
    assert not any(
        failure.rule == "verification_mapping_required" and failure.field == "verification:PRD-CORE-901-FR01"
        for failure in failures
    )
    assert any(
        failure.rule == "verification_mapping_required"
        and failure.field == "verification:PRD-CORE-901-NFR01"
        and failure.severity == "error"
        for failure in failures
    )


def test_legacy_high_risk_prd_gets_nonblocking_migration_warning() -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_validation import validate_verification_mappings

    content = _contract_prd(risk_level="high", template_version="2.3").replace(
        "verification:\n  mappings:\n    - requirement_id: PRD-CORE-901-FR01\n"
        "      acceptance_criteria:\n"
        "        - Given a request, When the behavior runs, Then the asserted value is correct\n"
        "      method: test\n"
        "      evidence_artifact: tests/test_behavior.py::test_behavior\n"
        "      pass_condition: The asserted value equals the requirement target\n"
        "      automated: true\n",
        "verification:\n  mappings: []\n",
    )
    failures, _ = validate_verification_mappings(
        parse_frontmatter(content),
        content,
        effective_risk_level="high",
    )
    assert any(failure.rule == "verification_mapping_required" for failure in failures)
    assert all(failure.severity == "warning" for failure in failures)


def test_current_low_risk_prd_reports_nonblocking_mapping_warning() -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_validation import validate_verification_mappings

    content = _contract_prd().replace("verification:\n  mappings:\n", "verification_disabled:\n  mappings:\n")
    failures, coverage = validate_verification_mappings(
        parse_frontmatter(content),
        content,
        effective_risk_level="low",
    )
    assert coverage == 0.0
    assert any(failure.rule == "verification_mapping_required" for failure in failures)
    assert all(failure.severity == "warning" for failure in failures)


def test_implemented_high_risk_mapping_requires_automation_or_reason() -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_validation import validate_verification_mappings

    content = (
        _contract_prd(risk_level="high")
        .replace("status: draft", "status: implemented")
        .replace("      automated: true\n", "")
    )
    failures, _ = validate_verification_mappings(
        parse_frontmatter(content),
        content,
        effective_risk_level="high",
    )
    assert any(
        failure.rule == "implemented_requirement_automation" and failure.severity == "error" for failure in failures
    )


def test_verification_mapping_normalizes_and_rejects_blank_fields() -> None:
    """PRD-QUAL-114-FR01: normalize required strings; reject hollow values."""
    from pydantic import ValidationError

    from trw_mcp.models.requirements import VerificationMapping
    from trw_mcp.state.validation._verification_mappings import validate_verification_mappings

    # Surrounding whitespace is stripped and stored normalized.
    mapping = VerificationMapping.model_validate(
        {
            "requirement_id": "  PRD-CORE-001-FR01  ",
            "acceptance_criteria": ["  Given X, When Y, Then Z  "],
            "method": "test",
            "evidence_artifact": "  tests/test_x.py::test_x  ",
            "pass_condition": "  observed value equals target  ",
            "automated": True,
        },
        strict=False,
    )
    assert mapping.requirement_id == "PRD-CORE-001-FR01"
    assert mapping.acceptance_criteria == ["Given X, When Y, Then Z"]
    assert mapping.evidence_artifact == "tests/test_x.py::test_x"
    assert mapping.pass_condition == "observed value equals target"

    # Whitespace-only required scalars fail with the exact field path.
    for field in ("requirement_id", "evidence_artifact", "pass_condition"):
        payload = _mapping("PRD-CORE-001-FR01")
        payload[field] = "   "
        with pytest.raises(ValidationError) as exc:
            VerificationMapping.model_validate(payload, strict=False)
        assert field in str(exc.value)

    # A whitespace-only acceptance-criterion item is rejected.
    payload = _mapping("PRD-CORE-001-FR01")
    payload["acceptance_criteria"] = ["valid criterion", "   "]
    with pytest.raises(ValidationError) as exc:
        VerificationMapping.model_validate(payload, strict=False)
    assert "acceptance_criteria" in str(exc.value)

    # Optional automation reason: missing stays None; blank supplied is invalid.
    ok = VerificationMapping.model_validate(_mapping("PRD-CORE-001-FR01"), strict=False)
    assert ok.automation_infeasible_reason is None
    payload = _mapping("PRD-CORE-001-FR01")
    payload["automation_infeasible_reason"] = "   "
    with pytest.raises(ValidationError):
        VerificationMapping.model_validate(payload, strict=False)

    # Tool-boundary: a hollow mapping is a schema failure and never counts.
    frontmatter = {
        "template_version": "3.2",
        "status": "draft",
        "verification": {
            "mappings": [{**_mapping("PRD-CORE-901-FR01"), "evidence_artifact": "   "}],
        },
    }
    failures, coverage = validate_verification_mappings(
        frontmatter,
        "### PRD-CORE-901-FR01: Behavior\n",
        effective_risk_level="high",
    )
    assert any(failure.rule == "verification_mapping_schema" for failure in failures)
    assert coverage == 0.0


def test_normalized_mapping_ids_preserve_exact_coverage() -> None:
    """PRD-QUAL-114-FR02: exact normalized duplicate/orphan/missing coverage."""
    from trw_mcp.state.validation._verification_mappings import validate_verification_mappings

    content = "### PRD-CORE-901-FR01: One\n### PRD-CORE-901-FR02: Two\n### PRD-CORE-901-NFR01: Three\n"
    frontmatter = {
        "template_version": "3.2",
        "status": "draft",
        "verification": {
            "mappings": [
                _mapping("PRD-CORE-901-FR01"),  # valid exact FR
                _mapping("  PRD-CORE-901-FR01  "),  # normalized duplicate of FR01
                {**_mapping("PRD-CORE-901-NFR01"), "evidence_artifact": "   "},  # hollow -> not counted
                _mapping("PRD-CORE-901-FR99"),  # orphan (no heading)
            ],
        },
    }
    failures, coverage = validate_verification_mappings(
        frontmatter,
        content,
        effective_risk_level="high",
    )
    rules = [failure.rule for failure in failures]
    assert "verification_mapping_duplicate" in rules
    assert any(
        failure.rule == "verification_mapping_orphan" and "PRD-CORE-901-FR99" in failure.message for failure in failures
    )
    # 3 requirement headings; only FR01 has a valid exact mapping -> 1/3.
    assert coverage == pytest.approx(1 / 3)
    missing = {failure.field for failure in failures if failure.rule == "verification_mapping_required"}
    assert "verification:PRD-CORE-901-FR02" in missing
    assert "verification:PRD-CORE-901-NFR01" in missing


def test_mapping_contract_never_claims_execution_or_pass(tmp_path: Path) -> None:
    """PRD-QUAL-114-FR04: mapping + static output remain plan-only."""
    from trw_mcp.models.requirements import VerificationMapping

    mapping = VerificationMapping.model_validate(_mapping("PRD-CORE-001-FR01"), strict=False)
    dumped = mapping.model_dump()
    assert set(dumped) == {
        "requirement_id",
        "acceptance_criteria",
        "method",
        "evidence_artifact",
        "pass_condition",
        "automated",
        "automation_infeasible_reason",
    }
    forbidden = {"executed", "outcome", "passed", "verifier", "observed_at", "verified", "receipt", "digest"}
    assert not (forbidden & set(dumped))

    # Static validation of a fully-mapped PRD reports plan coverage only.
    result = _validate(tmp_path, _contract_prd())
    assert result["verification_mapping_coverage"] == 1.0
    forbidden_keys = {
        "executed",
        "outcome",
        "passed",
        "verifier",
        "observed_at",
        "verification_receipt",
        "verified_coverage",
    }
    assert not (forbidden_keys & set(result))


def test_method_neutral_and_test_link_coverage_are_distinct(tmp_path: Path) -> None:
    """PRD-QUAL-114-FR05: mapping-plan vs test-link coverage are separate metrics."""
    content = _contract_prd(
        fr_body=(
            "Implementation: src/feature.py\n"
            "Test: tests/test_feature.py::test_feature\n\n"
            "### PRD-CORE-901-FR02: Linked but unmapped\n"
            "**Priority**: Should Have\n"
            "**Status**: active\n"
            "Implementation: src/feature2.py\n"
            "Test: tests/test_feature2.py::test_feature2\n"
        )
    )
    result = _validate(tmp_path, content)
    # Two FR headings; only FR01 carries a method-neutral verification mapping.
    assert result["verification_mapping_coverage"] == 0.5
    # Both FRs carry impl+test links, so the test-link metric is full and distinct.
    # Token-bloat W5: the metric is now surfaced only under its canonical name
    # (measured_traceability_coverage); the duplicate alias is dropped.
    assert result["measured_traceability_coverage"] == 1.0
    assert result["verification_mapping_coverage"] != result["measured_traceability_coverage"]
    assert "implementation_test_link_coverage" not in result


def test_measured_traceability_is_exposed_by_tool(tmp_path: Path) -> None:
    content = _contract_prd(
        fr_body=(
            "Implementation: src/feature.py\n"
            "Test: tests/test_feature.py::test_feature\n\n"
            "### PRD-CORE-901-FR02: Untraced behavior\n"
            "**Priority**: Should Have\n"
            "**Status**: active\n"
            "The system shall expose a second behavior without implementation evidence."
        )
    )
    result = _validate(tmp_path, content)
    assert result["measured_traceability_coverage"] == 0.5


@pytest.mark.parametrize(
    ("category", "expected"),
    [("CORE", 12), ("INFRA", 9), ("FIX", 8), ("RESEARCH", 7)],
)
def test_create_and_validate_report_variant_section_counts(tmp_path: Path, category: str, expected: int) -> None:
    created = _get_tools()["trw_prd_create"].fn(
        input_text=f"Create {category} contract",
        category=category,
        priority="P2",
        title=f"{category} contract",
    )
    assert created["sections_generated"] == expected

    validated = _get_tools()["trw_prd_validate"].fn(prd_path=created["output_path"])
    assert len(validated["sections_expected"]) == expected


def test_lifecycle_and_quality_namespaces_are_explicit(tmp_path: Path) -> None:
    from trw_mcp.models.requirements import PRDLifecycleStatus, PRDQualityTier

    assert PRDLifecycleStatus is not PRDQualityTier
    result = _validate(tmp_path, _contract_prd())
    assert result["prd_status"] == "draft"
    assert result["quality_tier"] in {tier.value for tier in PRDQualityTier}


def test_completeness_warning_uses_zero_to_one_completeness_scale(tmp_path: Path) -> None:
    created = _get_tools()["trw_prd_create"].fn(
        input_text="Create a complete but intentionally sparse template",
        category="CORE",
        priority="P2",
        title="Scale check",
    )
    with patch("trw_mcp.tools.requirements.logger.warning") as warning:
        result = _get_tools()["trw_prd_validate"].fn(prd_path=created["output_path"])

    assert result["completeness_score"] >= 0.85
    assert not any(call.args and call.args[0] == "prd_validate_below_threshold" for call in warning.call_args_list)


def test_deployed_prd_templates_are_byte_identical() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    authoring = repo_root / "trw-mcp" / "src" / "trw_mcp" / "data" / "prd_template.md"
    mirrors = [
        repo_root / "docs" / "requirements-aare-f" / "prds" / "TEMPLATE.md",
        repo_root / "trw-eval" / "trw-mcp-local" / "src" / "trw_mcp" / "data" / "prd_template.md",
    ]
    expected = authoring.read_bytes()
    assert b"AARE-F Framework v3.2.0" in expected
    assert b"verification:" in expected
    assert b"aaref_components:" not in expected
    assert b"conflicts_with:" not in expected
    assert all(mirror.read_bytes() == expected for mirror in mirrors)


def test_template_checklist_defers_dynamic_counts_and_gates_to_runtime() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    template = (repo_root / "trw-mcp" / "src" / "trw_mcp" / "data" / "prd_template.md").read_text()
    assert "All runtime category-required sections present" in template
    assert "configured tier required for the next lifecycle phase" in template
    assert "Feature: 12" not in template
    assert ">= 65" not in template


def test_raw_template_is_machine_opted_into_blocking_high_risk_contract() -> None:
    from trw_mcp.state.prd_utils import parse_frontmatter
    from trw_mcp.state.validation._prd_validation import validate_verification_mappings

    repo_root = Path(__file__).resolve().parents[2]
    template = (repo_root / "trw-mcp" / "src" / "trw_mcp" / "data" / "prd_template.md").read_text()
    derived = (
        template.replace("{CATEGORY}", "CORE")
        .replace("{SEQUENCE}", "999")
        .replace("{CAT}", "CORE")
        .replace("{SEQ}", "999")
        .replace("{Title}", "Template Derived")
    )
    frontmatter = parse_frontmatter(derived)
    assert frontmatter["template_version"] == "3.2"

    failures, coverage = validate_verification_mappings(
        frontmatter,
        derived,
        effective_risk_level="high",
    )
    assert coverage == 0.0
    assert any(failure.rule == "verification_mapping_required" and failure.severity == "error" for failure in failures)


# ---------------------------------------------------------------------------
# PRD-QUAL-119-FR02: activation-gate ownership and exact external outcome
# ---------------------------------------------------------------------------


def test_prd_qual_119_fr02() -> None:
    """FR02 acceptance: Given repository, evidenced external, unverified
    external, and closed gate fixtures, When evaluated, Then outcomes are
    respectively incomplete, externally_blocked, unknown, and eligible."""
    from trw_mcp.models.requirements import ActivationGate, ActivationGateOwnership

    repo_open = ActivationGate(gate_id="wire_default_path", ownership=ActivationGateOwnership.REPOSITORY_CONTROLLABLE)
    assert repo_open.completion_effect() == "incomplete"

    evidenced_external = ActivationGate(
        gate_id="pypi_publish",
        ownership=ActivationGateOwnership.EXTERNAL_RELEASE,
        evidence_receipt="receipt-2026-07-11-release-queue",
    )
    assert evidenced_external.completion_effect() == "externally_blocked"

    unverified_external = ActivationGate(gate_id="vendor_api", ownership=ActivationGateOwnership.EXTERNAL_SYSTEM)
    assert unverified_external.completion_effect() == "unknown"

    # Whitespace-only evidence is not evidence.
    hollow = ActivationGate(
        gate_id="vendor_api2", ownership=ActivationGateOwnership.EXTERNAL_SYSTEM, evidence_receipt="   "
    )
    assert hollow.completion_effect() == "unknown"

    closed = ActivationGate(gate_id="operator_signoff", ownership=ActivationGateOwnership.OPERATOR_DECISION, open=False)
    assert closed.completion_effect() == "eligible"

    # Operator decision pending WITH a recorded receipt is externally blocked.
    operator_pending = ActivationGate(
        gate_id="operator_signoff2",
        ownership=ActivationGateOwnership.OPERATOR_DECISION,
        evidence_receipt="decision-queue-entry-42",
    )
    assert operator_pending.completion_effect() == "externally_blocked"


def test_prd_qual_119_fr02_frontmatter_contract_roundtrip() -> None:
    """activation_gates is part of the typed PRD frontmatter contract and
    round-trips through model validation (wiring: the AARE-F 3.2 contract
    consumes the field, not just the class)."""
    from trw_mcp.models.requirements import PRDFrontmatter

    fm = PRDFrontmatter.model_validate(
        {
            "id": "PRD-CORE-001",
            "title": "T",
            "activation_gates": [
                {"gate_id": "g1", "ownership": "repository_controllable"},
                {"gate_id": "g2", "ownership": "external_release", "open": False},
            ],
        },
        strict=False,
    )
    assert [g.gate_id for g in fm.activation_gates] == ["g1", "g2"]
    assert fm.activation_gates[0].completion_effect() == "incomplete"
    assert fm.activation_gates[1].completion_effect() == "eligible"


def test_prd_qual_120_fr02(tmp_path) -> None:
    """FR02 acceptance: Given receipts and a PRD snapshot, When derived, Then
    each accepted requirement has current proof or a typed blocker and
    existence-only evidence cannot pass."""
    from pathlib import Path

    from trw_mcp.state.acceptance_manifest import ReceiptEvidence, derive_manifest

    prd = Path(tmp_path) / "PRD-CORE-050.md"
    prd.write_text(
        "---\nprd:\n  id: PRD-CORE-050\n  title: T\nverification:\n  mappings:\n"
        "  - requirement_id: PRD-CORE-050-FR01\n    acceptance_criteria: [c]\n"
        "    method: test\n    evidence_artifact: t.py::t\n    pass_condition: p\n---\n",
        encoding="utf-8",
    )
    # Content-bound proof -> accepted with the receipt recorded.
    accepted = derive_manifest(prd, {"PRD-CORE-050-FR01": ReceiptEvidence("r1", "sha256:" + "c" * 64)})
    requirement = accepted.requirements[0]
    assert str(requirement.state) == "accepted" and requirement.receipt_id == "r1"

    # Existence-only evidence (a receipt id with no content binding) CANNOT pass.
    existence_only = derive_manifest(prd, {"PRD-CORE-050-FR01": ReceiptEvidence("r1", "just-exists")})
    requirement = existence_only.requirements[0]
    assert str(requirement.state) == "blocked" and requirement.blocker == "existence_only_evidence"

    # Absent proof -> typed blocker, never an implicit pass.
    absent = derive_manifest(prd, {})
    requirement = absent.requirements[0]
    assert str(requirement.state) == "unknown" and requirement.blocker == "no_receipt_recorded"


# --------------------------------------------------------------------------- #
# PRD-CORE-218-FR08: SurfaceDelta readiness and subtraction gate
# --------------------------------------------------------------------------- #


def _surface_rules(result: dict[str, object]) -> set[str]:
    failures = result["failures"]
    assert isinstance(failures, list)
    return {str(failure["rule"]) for failure in failures if str(failure["rule"]).startswith("core218_surface_delta")}


def _delta_block(*, additions: list[str], removals: list[str], exception: bool, expiry: str = "2027-01-01") -> str:
    lines = [
        "surface_delta:",
        f"  additions: [{', '.join(additions)}]",
        f"  removals: [{', '.join(removals)}]",
        "  default_exposure: standard",
        "  migration: migrate first-party callers",
        "  owner: platform",
        "  measured_benefit: reduces selection load",
        "  reevaluation: '2026-12-01'",
    ]
    if exception:
        lines.append("  exception_owner: operator")
        lines.append(f"  exception_expiry: '{expiry}'")
    return "\n".join(lines) + "\n"


def test_prd_core_218_fr08(tmp_path: Path) -> None:
    # 1. Declares a public-surface change but ships no SurfaceDelta -> fails readiness.
    expanding = _contract_prd(extra_frontmatter="public_surface: true\n")
    r1 = _validate(tmp_path, expanding, name="expanding.md")
    assert "core218_surface_delta_required" in _surface_rules(r1)
    assert r1["valid"] is False

    # 2. A valid net-growth delta WITH an approved, unexpired exception passes the gate.
    growth = _contract_prd(extra_frontmatter=_delta_block(additions=["a", "b"], removals=[], exception=True))
    r2 = _validate(tmp_path, growth, name="growth.md")
    assert _surface_rules(r2) == set()

    # 3. Net reduction passes without any exception.
    reduction = _contract_prd(extra_frontmatter=_delta_block(additions=["a"], removals=["x", "y"], exception=False))
    r3 = _validate(tmp_path, reduction, name="reduction.md")
    assert _surface_rules(r3) == set()

    # 4. Net growth with an EXPIRED exception fails.
    expired = _contract_prd(
        extra_frontmatter=_delta_block(additions=["a", "b"], removals=[], exception=True, expiry="2020-01-01")
    )
    r4 = _validate(tmp_path, expired, name="expired.md")
    assert "core218_surface_delta_exception_expired" in _surface_rules(r4)
    assert r4["valid"] is False

    # 5. Net growth with NO exception fails (subtraction/budget gate).
    unbudgeted = _contract_prd(
        extra_frontmatter=_delta_block(additions=["a", "b", "c"], removals=["x"], exception=False)
    )
    r5 = _validate(tmp_path, unbudgeted, name="unbudgeted.md")
    assert "core218_surface_delta_net_growth" in _surface_rules(r5)

    # 6. A declared delta missing required fields fails, naming the gap.
    incomplete = _contract_prd(
        extra_frontmatter=(
            "surface_delta:\n"
            "  additions: [a, b]\n"
            "  removals: []\n"
            "  default_exposure: standard\n"
            "  migration: migrate\n"
            "  owner: platform\n"  # measured_benefit + reevaluation intentionally absent
        )
    )
    r6 = _validate(tmp_path, incomplete, name="incomplete.md")
    assert "core218_surface_delta_required" in _surface_rules(r6)

    # 7. A PRD that declares NO surface change is unaffected by the gate.
    untouched = _contract_prd()
    r7 = _validate(tmp_path, untouched, name="untouched.md")
    assert _surface_rules(r7) == set()
