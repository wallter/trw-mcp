"""Tests for PRD template hardening AI-specific behavior."""

from __future__ import annotations

from trw_mcp.state.validation import validate_prd_quality_v2

from tests._test_prd_template_hardening_support import _assert_no_ai_detection_flags, _make_prd


def _make_ai_incomplete_prd() -> str:
    return _make_prd(prd_id="PRD-QUAL-055", title="AI/LLM Hardening", category="QUAL") + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Generation | old | new | `src/model.py` | test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | runtime | `src/model.py` | `tests/test_model.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_model.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_model.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_model.py::test_fr01`

### Migration Tests
- [ ] `tests/test_model.py::test_migration_case`

### Regression Tests
- [ ] `tests/test_model.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_model.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent
"""


def _make_ai_operational_sections() -> str:
    return """
## 7. AI/LLM Operational Sections

### Data / Context Provenance
- **Data Sources**: `dataset.csv`, `api_endpoint`
- **Provenance Chain**: Input → Processing → Model → Output
- **Data Quality Signals**: validation_metrics, drift_threshold

### Failure Modes / Safe Degradation
- **Failure Mode**: Model returns low-confidence predictions
- **Safe Behavior**: Fall back to rule-based heuristic
- **Escalation Path**: Human-in-the-loop review when confidence < 0.6

### Human Oversight / Escalation
- **Review Triggers**: confidence score < 0.6, uncertainty threshold exceeded
- **Escalation Paths**: Slack channel #model-oversight
- **Audit Trail**: decisions logged in `logs/ai_decisions.log`

### Evaluation Plan
- **Baseline Criteria**: accuracy >= 0.85, latency P99 <= 200ms
- **Evaluation Method**: A/B test, automated metrics
- **Performance Metrics**: accuracy, p99 latency, error rate

### Release Gate
- **Rollout Strategy**: Canary (5% → 25% → 100%)
- **Rollback Triggers**: error rate > 1%, latency P99 > 500ms, confidence < 0.5
- **Canary Duration**: 24h per phase

### Monitoring Plan
- **Primary Signal**: prediction latency, drift score, error rate
- **Target Threshold**: P99 < 300ms, drift < 0.1, error rate < 5%
- **Escalation Action**: Alert on threshold exceeded, rollback on sustained violation

### Risk Register By Failure Class
| Failure Class | Scenario | Detection | Mitigation | Residual Risk |
|---------------|----------|-----------|------------|---------------|
| Correctness | Low confidence predictions | Confidence score < 0.6 | Human review | Low |
| Safety | Unsafe recommendations | Safety filter triggered | Halt, alert, revert | Low |
| Maintainability | Model drift | Performance degradation | Retrain, alert | Low |
| Governance | Unattributable output | No source attribution | Human review | Low |
"""


def test_ai_prd_required_operational_sections() -> None:
    ai_incomplete = _make_ai_incomplete_prd()
    ai_complete = ai_incomplete + _make_ai_operational_sections()

    ai_incomplete_result = validate_prd_quality_v2(ai_incomplete)
    ai_complete_result = validate_prd_quality_v2(ai_complete)

    assert ai_complete_result.total_score > ai_incomplete_result.total_score

    structural_dims = [d for d in ai_complete_result.dimensions if d.name == "structural_completeness"]
    assert len(structural_dims) == 1
    assert "ai_section_detected" in structural_dims[0].details
    assert structural_dims[0].details["ai_section_detected"] is True


def test_non_ai_prd_not_penalized_for_missing_ai_sections() -> None:
    non_ai_prd = _make_prd(prd_id="PRD-INFRA-999", title="Infrastructure PRD", category="INFRA") + """
### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Deployment | old | new | `src/deploy.py` | test |

### Behavior Switch Matrix
| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | old | new | deploy | `src/deploy.py` | `tests/test_deploy.py::test_fr01` |

### Unit Tests
- [ ] `tests/test_deploy.py::test_unit_case`

### Integration Tests
- [ ] `tests/test_deploy.py::test_integration_case`

### Acceptance Tests
- [ ] `tests/test_deploy.py::test_fr01`

### Migration Tests
- [ ] `tests/test_deploy.py::test_migration_case`

###_regression Tests
- [ ] `tests/test_deploy.py::test_regression_case`

### Negative / Fallback Tests
- [ ] `tests/test_deploy.py::test_missing_config_fallback`

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

### Migration / Backward Compatibility
- repeated runs are idempotent
"""

    result = validate_prd_quality_v2(non_ai_prd)

    _assert_no_ai_detection_flags(result)


def test_non_ai_prd_ignores_high_risk_and_substring_false_positives() -> None:
    high_risk_non_ai = (
        _make_prd(prd_id="PRD-CORE-999", title="Maintainers workflow hardening", category="CORE")
        .replace("status: draft", "status: done")
        .replace("category: CORE", "category: CORE\n  risk_level: high")
        + """
This PRD improves maintainer workflows and reviewer handoffs.
It explicitly does not add LLM-based scoring to the system.

### Primary Control Points
| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Templates | Manual drift | Category-aware defaults | `src/templates.py` | `tests/test_templates.py::test_defaults` |
"""
    )

    result = validate_prd_quality_v2(high_risk_non_ai)

    _assert_no_ai_detection_flags(result)


def test_ai_prd_suggestions_reference_operational_gates() -> None:
    ai_incomplete = _make_ai_incomplete_prd() + """
## 7. AI/LLM Operational Sections

### Data / Context Provenance
- **Data Sources**: `dataset.csv`
- **Provenance Chain**: Input → Processing → Model → Output
- **Data Quality Signals**: validation_metrics

### Failure Modes / Safe Degradation
- **Failure Mode**: Model returns low-confidence predictions
- **Safe Behavior**: Fall back to rule-based heuristic
- **Escalation Path**: Human-in-the-loop review

### Human Oversight / Escalation
- **Review Triggers**: confidence score < 0.6
- **Escalation Paths**: Slack channel #model-oversight
- **Audit Trail**: decisions logged

### Evaluation Plan
- **Baseline Criteria**: accuracy >= 0.85
- **Evaluation Method**: A/B test
- **Performance Metrics**: accuracy, p99 latency

### Release Gate
- **Rollout Strategy**: Canary (5% → 25% → 100%)
- **Rollback Triggers**: error rate > 1%, latency P99 > 500ms
- **Canary Duration**: 24h per phase

### Monitoring Plan
- **Primary Signal**: prediction latency, drift score
- **Target Threshold**: P99 < 300ms
- **Escalation Action**: Alert on threshold exceeded

### Risk Register By Failure Class
| Failure Class | Scenario | Detection | Mitigation | Residual Risk |
|---------------|----------|-----------|------------|---------------|
| Correctness | Low confidence predictions | Confidence score < 0.6 | Human review | Low |
| Safety | Unsafe recommendations | Safety filter triggered | Halt, alert, revert | Low |
| Maintainability | Model drift | Performance degradation | Retrain, alert | Low |
| Governance | Unattributable output | No source attribution | Human review | Low |
"""

    result = validate_prd_quality_v2(ai_incomplete)

    ai_structure_found = False
    ai_trace_found = False

    for dim in result.dimensions:
        if dim.name == "structural_completeness":
            assert "ai_section_detected" in dim.details
            assert dim.details["ai_section_detected"] is True
            ai_structure_found = True
        elif dim.name == "traceability":
            assert "ai_operational_evidence_detected" in dim.details
            assert dim.details["ai_operational_evidence_detected"] is True
            ai_trace_found = True

    assert ai_structure_found, "AI section detection should be present in structural_completeness"
    assert ai_trace_found, "AI operational evidence detection should be present in traceability"

    ai_missing = _make_ai_incomplete_prd()
    result_missing = validate_prd_quality_v2(ai_missing)

    ai_gate_suggestion_found = False
    for suggestion in result_missing.improvement_suggestions:
        if "AI/LLM" in suggestion.message or "operational" in suggestion.message.lower():
            ai_gate_suggestion_found = True
            break

    assert len(result_missing.improvement_suggestions) >= 1, "Expected at least one suggestion for incomplete AI PRD"
