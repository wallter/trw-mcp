# Traceability Analysis Prompts

**Version**: 1.0.0
**AARE-F Components**: C1 (End-to-End Traceability Infrastructure)
**Research Basis**: Wave 8, Wave 13-15 (Safety Standards), Finding F11-F13, Finding F19

---

## Purpose

These prompts help analyze, improve, and maintain requirement traceability:
- Upstream traceability (source -> requirement)
- Downstream traceability (requirement -> implementation -> test)
- Impact analysis
- Coverage reporting

---

## Prompt 1: Traceability Gap Analysis

### Context
Identify gaps in traceability coverage across the requirements catalogue.

### Prompt Template

```
You are a traceability analyst applying AARE-F Component C1. Analyze the traceability coverage of this requirements set.

## Requirements with Current Traceability
{paste requirements including any existing traceability links}

## Traceability Requirements (AARE-F C1)

### Upstream Links (Source Traceability)
Every requirement MUST trace to at least one source:
- Stakeholder interview
- Source document (with section reference)
- Code analysis (reverse engineering)
- Research wave (KE entry)
- Regulatory requirement

### Downstream Links (Implementation Traceability)
Implemented requirements SHOULD trace to:
- Implementation file(s): `module.py:function`, `component.ts:method`, or `handler.go:Function`
- Test file(s): `test_module.py::test_function`, `component.test.ts::testCase`, or `handler_test.go::TestFunction`

### Knowledge Entry Links (Research Traceability)
Research-backed requirements SHOULD link to:
- Knowledge entries: `KE-{CATEGORY}-{NUMBER}`
- Research waves: `Wave {N}`

## Output Format

```yaml
traceability_gap_analysis:
  date: "..."
  scope: "{requirements set name}"

  summary:
    total_requirements: X
    fully_traced: X  # Has source, impl, test
    partially_traced: X  # Missing some links
    untraced: X  # No traceability at all

  coverage_metrics:
    source_coverage: X%  # Target: 100%
    implementation_coverage: X%  # Target: 90%+
    test_coverage: X%  # Target: 90%+
    ke_coverage: X%  # Target: 50%+ for research-backed

  gaps:
    missing_source:
      count: X
      requirements:
        - id: "..."
          title: "..."
          suggested_sources: ["..."]

    missing_implementation:
      count: X
      requirements:
        - id: "..."
          title: "..."
          status: "implemented"  # Should have impl link
          likely_files: ["..."]  # Based on naming

    missing_tests:
      count: X
      requirements:
        - id: "..."
          title: "..."
          suggested_test_location: "..."

    missing_ke_links:
      count: X
      requirements:
        - id: "..."
          title: "..."
          likely_ke_entries: ["..."]  # Based on content

  orphans:
    implementations_without_requirements:
      - file: "..."
        functions: ["..."]
        suggested_requirement: "..."

    tests_without_requirements:
      - file: "..."
        tests: ["..."]
        suggested_requirement: "..."

  recommendations:
    priority_1:  # Critical gaps
      - action: "..."
        requirements: [...]

    priority_2:  # Important gaps
      - action: "..."
        requirements: [...]

  quality_gate:
    source_coverage:
      threshold: 100%
      actual: X%
      passed: true|false
    implementation_coverage:
      threshold: 90%
      actual: X%
      passed: true|false
    test_coverage:
      threshold: 90%
      actual: X%
      passed: true|false
```
```

---

## Prompt 2: Impact Analysis

### Context
Analyze the impact of changing a requirement on downstream artifacts.

### Prompt Template

```
You are performing impact analysis for a requirement change. Identify all affected artifacts.

## Requirement Being Changed
{paste requirement with current traceability}

## Proposed Change
{describe the change}

## Impact Analysis Framework (AARE-F C1)

### Step 1: Direct Dependencies
What directly implements this requirement?
- Implementation files
- Test files
- Configuration files

### Step 2: Indirect Dependencies
What depends on the direct dependencies?
- Modules importing affected files
- Tests using affected modules
- Documentation referencing the requirement

### Step 3: Cross-Requirement Impact
What other requirements are affected?
- Requirements depending on this one
- Requirements this one enables
- Requirements that may conflict after change

### Step 4: Knowledge Base Impact
- KB patterns based on this requirement
- Research entries that informed this requirement

## Output Format

```yaml
impact_analysis:
  requirement_id: "..."
  requirement_title: "..."
  proposed_change: "..."
  analysis_date: "..."

  direct_impact:
    implementation_files:
      - file: "..."
        lines_affected: "..."
        change_type: modify|delete|add
        effort: low|medium|high

    test_files:
      - file: "..."
        tests_affected: [...]
        change_type: modify|delete|add

    config_files:
      - file: "..."
        change_needed: "..."

  indirect_impact:
    dependent_modules:
      - module: "..."
        reason: "imports affected file"
        change_needed: true|false

    affected_documentation:
      - file: "..."
        sections: [...]

  cross_requirement_impact:
    dependent_requirements:
      - id: "..."
        relationship: depends_on|enabled_by
        impact: "..."
        change_needed: true|false

    potential_conflicts:
      - id: "..."
        conflict_description: "..."
        resolution_needed: true|false

  knowledge_base_impact:
    patterns_affected:
      - pattern_id: "..."
        change_needed: "..."

    ke_entries_affected:
      - entry_id: "..."
        update_needed: true|false

  summary:
    files_affected: X
    tests_affected: X
    requirements_affected: X
    estimated_effort: low|medium|high
    risk_level: low|medium|high

  recommendations:
    - action: "..."
      priority: P0|P1|P2
      rationale: "..."
```
```

---

## Prompt 3: Traceability Matrix Generation

### Context
Generate a comprehensive traceability matrix from requirements.

### Prompt Template

```
You are generating a traceability matrix for compliance and audit purposes.

## Requirements Set
{paste requirements}

## Implementation References
{paste file list or code structure}

## Test References
{paste test file list}

## Matrix Requirements (AARE-F C1)

### Columns
1. Requirement ID
2. Requirement Title
3. Source Document(s)
4. Implementation File(s)
5. Test File(s)
6. Knowledge Entry(ies)
7. Status
8. Coverage %

### Row Completeness
- Green: All columns filled
- Yellow: Missing 1-2 links
- Red: Missing 3+ links or no source

## Output Format

Generate in both Markdown and YAML:

### Markdown Format

```markdown
# Traceability Matrix

**Generated**: {date}
**Requirements**: {count}
**Coverage**: {overall %}

| ID | Title | Source | Implementation | Tests | KE | Status | Coverage |
|----|-------|--------|----------------|-------|----|----|---------|
| FR-001 | ... | doc.md:S2 | module.py:fn (or component.ts:fn) | test.py::test (or component.test.ts::test) | KE-001 | Impl | 100% |
| FR-002 | ... | interview | - | - | - | Pending | 25% |
```

### YAML Format

```yaml
traceability_matrix:
  metadata:
    generated: "..."
    total_requirements: X
    overall_coverage: X%

  requirements:
    - id: "FR-001"
      title: "..."
      status: implemented|pending|deprecated
      coverage: X%

      upstream:
        sources:
          - type: document
            reference: "doc.md:section"
          - type: interview
            reference: "stakeholder, date"

      downstream:
        implementation:
          - file: "module.py"  # or component.ts, handler.go, service.rb, etc.
            location: "function_name"
            lines: "10-25"
        tests:
          - file: "test_module.py"  # or component.test.ts, handler_test.go, etc.
            tests: ["test_function"]

      knowledge:
        entries: ["KE-FRAME-002"]
        waves: [25]

  coverage_summary:
    by_status:
      implemented:
        count: X
        avg_coverage: X%
      pending:
        count: X
        avg_coverage: X%

    by_type:
      functional:
        count: X
        source_coverage: X%
        impl_coverage: X%
        test_coverage: X%
```
```

---

## Prompt 4: Traceability Link Validation

### Context
Validate that traceability links are accurate and current.

### Prompt Template

```
You are validating traceability links for accuracy. Check that all links point to existing, relevant artifacts.

## Traceability Data
{paste traceability matrix or requirements with links}

## Validation Checks

### 1. Source Validation
- Does the referenced document/section exist?
- Does the reference still support this requirement?
- Has the source been updated since linking?

### 2. Implementation Validation
- Does the referenced file exist? (any language: `.py`, `.ts`, `.go`, `.rs`, etc.)
- Does the function/class still exist?
- Does the implementation still match the requirement?

### 3. Test Validation
- Does the test file exist?
- Does the test function still exist?
- Does the test actually test this requirement?

### 4. Knowledge Entry Validation
- Does the KE entry exist?
- Is the KE entry still relevant?
- Has the KE entry been superseded?

## Output Format

```yaml
traceability_validation:
  date: "..."
  links_validated: X
  valid_links: X
  invalid_links: X
  stale_links: X

  validation_results:
    valid:
      - requirement_id: "..."
        link_type: source|impl|test|ke
        target: "..."
        status: valid

    invalid:
      - requirement_id: "..."
        link_type: source|impl|test|ke
        target: "..."
        issue: "file_not_found|function_removed|content_mismatch"
        suggested_fix: "..."

    stale:
      - requirement_id: "..."
        link_type: source|impl|test|ke
        target: "..."
        issue: "source_updated|implementation_changed"
        last_validated: "..."
        needs_review: true

  broken_links_by_type:
    source: X
    implementation: X
    test: X
    knowledge_entry: X

  recommendations:
    - priority: P0
      action: "Fix broken link..."
      requirements: [...]

    - priority: P1
      action: "Review stale links..."
      requirements: [...]
```
```

---

## Prompt 5: Traceability Report for Audit

### Context
Generate a compliance-ready traceability report for audit purposes.

### Prompt Template

```
You are generating an audit-ready traceability report per AARE-F standards and safety regulations.

## Requirements Scope
{paste requirements or describe scope}

## Audit Context
- Regulation/Standard: {e.g., ISO 26262, DO-178C, EU AI Act}
- Audit Type: {internal, external, certification}
- Date: {audit date}

## Report Requirements

### Executive Summary
- Total requirements in scope
- Traceability coverage metrics
- Compliance status
- Key gaps and risks

### Detailed Traceability
- Full matrix with all links
- Gap identification
- Validation status

### Evidence Package
- Source document references
- Implementation evidence
- Test evidence
- Change history

## Output Format

```markdown
# Traceability Audit Report

**Report ID**: TRACE-AUDIT-{YYYYMMDD}
**Scope**: {scope description}
**Standard**: {regulation/standard}
**Date**: {date}
**Prepared By**: {preparer}

## Executive Summary

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Requirements in Scope | X | - | - |
| Source Traceability | X% | 100% | PASS/FAIL |
| Implementation Traceability | X% | 90% | PASS/FAIL |
| Test Traceability | X% | 90% | PASS/FAIL |
| Bidirectional Coverage | X% | 90% | PASS/FAIL |

### Compliance Statement

{Statement of compliance or non-compliance with identified gaps}

### Key Findings

1. **Finding 1**: {description}
   - Impact: {impact}
   - Remediation: {action}

## Detailed Analysis

### Coverage by Requirement Type

| Type | Count | Source | Impl | Test | Overall |
|------|-------|--------|------|------|---------|
| Functional | X | X% | X% | X% | X% |
| Non-Functional | X | X% | X% | X% | X% |

### Gap Analysis

[Detailed gap listing]

### Validation Results

[Link validation summary]

## Evidence Index

| Requirement | Source Evidence | Impl Evidence | Test Evidence |
|-------------|-----------------|---------------|---------------|
| FR-001 | doc.md (SHA: xxx) | module.py:10 (or component.ts:10) | test.py::test (or component.test.ts::test) |

## Appendices

### A: Full Traceability Matrix
### B: Validation Logs
### C: Change History
```
```

---

## Quality Thresholds (AARE-F C1)

| Metric | Target | Critical Threshold |
|--------|--------|-------------------|
| Source Traceability | 100% | >= 95% |
| Implementation Traceability | 90%+ | >= 80% |
| Test Traceability | 90%+ | >= 80% |
| Bidirectional Coverage | 90%+ | >= 80% |
| Link Validation Pass Rate | 100% | >= 95% |
| Impact Analysis Time | < 5s | < 30s |

---

## Key Requirements to Verify

Before submitting your traceability analysis, re-read and verify:
1. Coverage percentages are calculated for all four dimensions (source, implementation, test, KE)
2. Every orphan (implementation without requirements, tests without requirements) is listed
3. Missing links are flagged with specific requirement IDs, not just counts
4. Quality gate pass/fail is determined against stated thresholds

Re-read the verification checks section above and confirm all check categories were evaluated.

---

## Related Prompts

- [Requirements Elicitation](requirements-elicitation.md) - Establish source traceability
- [PRD Creation](prd-creation.md) - Include traceability in PRDs
- [Quality Validation](quality-validation.md) - Validate traceability coverage

---

*Traceability Analysis Prompts v1.0.0*
*AARE-F Framework v1.1.0 Implementation*
