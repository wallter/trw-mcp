# Quality Validation Prompts

**Version**: 1.0.0
**AARE-F Components**: C6 (Uncertainty Management), C9 (Observability)
**Research Basis**: Wave 10 (Quality Metrics), Wave 11 (V&V Techniques), Wave 19 (Hallucination Detection)

---

## Purpose

These prompts validate requirements and PRDs against AARE-F quality standards:
- Ambiguity detection
- Completeness assessment
- Consistency validation
- Traceability verification

---

## Prompt 1: PRD Quality Audit

### Context
Perform a comprehensive quality audit of a PRD against AARE-F standards.

### Prompt Template

```
You are a requirements quality auditor applying AARE-F standards. Perform a comprehensive audit of this PRD.

## PRD to Audit
{paste PRD content}

## Audit Framework

### 1. Structure Compliance (25 points)
| Check | Points | Criteria |
|-------|--------|----------|
| YAML Frontmatter | 5 | All required fields present |
| 12 Sections | 10 | All template sections included |
| ID Schema | 5 | Correct PRD-{CAT}-{SEQ} format |
| Version History | 5 | Versioning documented |

### 2. Content Quality (35 points)
| Check | Points | Criteria |
|-------|--------|----------|
| Problem Statement | 5 | Clear, specific, impactful |
| Goals Measurability | 5 | SMART goals |
| User Stories | 10 | Standard format + confidence + evidence |
| Requirements Clarity | 10 | Shall/should, testable, atomic |
| Non-Goals | 5 | Explicit scope boundaries |

### 3. AARE-F Compliance (25 points)
| Check | Points | Criteria |
|-------|--------|----------|
| Confidence Scores | 5 | All requirements have 0.0-1.0 scores |
| Evidence Required | 5 | User stories have evidence field |
| Risk Assessment | 5 | Includes residual risk |
| Traceability Matrix | 5 | Source, impl, test, KE links |
| Knowledge Entry Links | 5 | KE references documented |

### 4. Ambiguity Analysis (15 points)
| Check | Points | Criteria |
|-------|--------|----------|
| Vague Terms | 5 | No "fast", "user-friendly", etc. |
| Quantifiable Criteria | 5 | Measurable where applicable |
| Clear Language | 5 | No "might", "could consider" |

## Output Format

```yaml
audit_results:
  prd_id: "..."
  audit_date: "..."
  auditor: "AI (AARE-F v1.1)"

  scores:
    structure_compliance: X/25
    content_quality: X/35
    aaref_compliance: X/25
    ambiguity_score: X/15
    total: X/100

  grade: A|B|C|D|F  # A>=90, B>=80, C>=70, D>=60, F<60

  findings:
    critical:  # Must fix before approval
      - issue: "..."
        location: "..."
        fix: "..."
    major:  # Should fix
      - issue: "..."
        location: "..."
        fix: "..."
    minor:  # Nice to fix
      - issue: "..."
        location: "..."
        fix: "..."

  ambiguous_terms_found:
    - term: "..."
      location: "..."
      suggestion: "..."

  missing_elements:
    - "..."

  recommendations:
    - priority: P0|P1|P2
      action: "..."
```
```

---

## Prompt 2: Requirements Ambiguity Detector

### Context
Detect and flag ambiguous language in requirements.

### Prompt Template

```
You are a requirements clarity analyst. Analyze these requirements for ambiguity.

## Requirements to Analyze
{paste requirements}

## Ambiguity Detection Rules

### Category 1: Vague Adjectives/Adverbs
Flag: "fast", "quick", "efficient", "user-friendly", "robust", "scalable", "flexible", "easy", "simple", "intuitive", "adequate", "sufficient"

Replace with: Specific metrics (e.g., "< 200ms response time")

### Category 2: Uncertain Language
Flag: "might", "could", "may", "possibly", "generally", "usually", "sometimes", "often", "should consider", "as appropriate"

Replace with: Definitive statements or explicit conditions

### Category 3: Unbounded Scope
Flag: "etc.", "and so on", "including but not limited to", "various", "multiple", "many", "all relevant"

Replace with: Explicit enumeration or defined limits

### Category 4: Implicit Assumptions
Flag: Unstated dependencies, assumed knowledge, hidden requirements

Document: Make assumptions explicit

### Category 5: Passive Voice Hiding Responsibility
Flag: "shall be done", "will be handled", "is expected"

Replace with: Active voice with explicit actor

## Output Format

```yaml
ambiguity_analysis:
  total_requirements: X
  clear_requirements: X
  ambiguous_requirements: X
  ambiguity_rate: X%  # Target: <= 5%

  findings:
    - requirement_id: "..."
      original_text: "..."
      ambiguity_type: "vague_adjective|uncertain|unbounded|implicit|passive"
      flagged_terms: ["..."]
      suggested_revision: "..."
      confidence_in_fix: 0.X

  summary:
    vague_adjectives: X
    uncertain_language: X
    unbounded_scope: X
    implicit_assumptions: X
    passive_voice: X

  quality_gate:
    passed: true|false
    threshold: 5%
    actual: X%
```
```

---

## Prompt 3: Consistency Checker

### Context
Check requirements for internal consistency and conflicts.

### Prompt Template

```
You are a requirements consistency analyst. Check these requirements for conflicts and inconsistencies.

## Requirements Set
{paste all requirements}

## Consistency Checks

### 1. Direct Contradictions
- Requirement A says X, Requirement B says NOT X
- Example: "FR-001: System shall use JSON" vs "FR-005: System shall use XML"

### 2. Implicit Conflicts
- Requirements that cannot both be satisfied
- Example: "NFR-001: < 100ms response" vs "FR-010: Encrypt all data" (encryption adds latency)

### 3. Resource Conflicts
- Requirements competing for same resources
- Example: "FR-001: Use all GPU memory" vs "FR-002: Run multiple models simultaneously"

### 4. Priority Conflicts
- Conflicting requirements with same priority
- All P0 requirements should be achievable together

### 5. Dependency Violations
- A depends on B, but B depends on A (circular)
- A depends on B, but B is lower priority than A

### 6. Terminology Inconsistency
- Same concept with different names
- Different concepts with same name

## Output Format

```yaml
consistency_analysis:
  requirements_analyzed: X
  conflicts_found: X
  consistency_score: X%  # Target: >= 95%

  direct_contradictions:
    - req_a: "..."
      req_b: "..."
      conflict: "..."
      severity: critical|major|minor
      resolution_suggestion: "..."

  implicit_conflicts:
    - requirements: ["...", "..."]
      conflict: "..."
      trade_off: "..."
      resolution_options:
        - option: "..."
          impact: "..."

  dependency_issues:
    - type: circular|priority_mismatch
      requirements: ["...", "..."]
      issue: "..."
      resolution: "..."

  terminology_issues:
    - term_a: "..."
      term_b: "..."
      issue: "same concept, different names"
      recommendation: "Standardize on '...'"

  quality_gate:
    passed: true|false
    threshold: 95%
    actual: X%
```
```

---

## Prompt 4: Completeness Assessment

### Context
Assess whether a requirements set covers all necessary aspects.

### Prompt Template

```
You are a requirements completeness analyst. Assess whether this requirements set is complete.

## Requirements Set
{paste requirements}

## System Context
{describe the system being specified}

## Completeness Checklist

### 1. Functional Completeness
- [ ] All user-facing features specified
- [ ] All system behaviors documented
- [ ] All inputs and outputs defined
- [ ] All error conditions handled
- [ ] All state transitions covered

### 2. Non-Functional Completeness
- [ ] Performance requirements (response time, throughput)
- [ ] Reliability requirements (uptime, MTBF)
- [ ] Scalability requirements (load, growth)
- [ ] Security requirements (auth, encryption, audit)
- [ ] Usability requirements (if applicable)
- [ ] Maintainability requirements

### 3. Interface Completeness
- [ ] User interfaces specified
- [ ] API interfaces specified
- [ ] Data interfaces specified
- [ ] External system interfaces specified

### 4. Constraint Completeness
- [ ] Technical constraints documented
- [ ] Business constraints documented
- [ ] Regulatory constraints documented
- [ ] Resource constraints documented

### 5. Quality Attribute Completeness
- [ ] Testability criteria defined
- [ ] Acceptance criteria for all requirements
- [ ] Success metrics defined
- [ ] Measurement methods specified

## Output Format

```yaml
completeness_assessment:
  overall_score: X%  # Target: >= 85%

  coverage:
    functional:
      score: X%
      covered: [...]
      missing: [...]

    non_functional:
      score: X%
      covered: [...]
      missing: [...]

    interfaces:
      score: X%
      covered: [...]
      missing: [...]

    constraints:
      score: X%
      covered: [...]
      missing: [...]

    quality_attributes:
      score: X%
      covered: [...]
      missing: [...]

  gaps:
    critical:  # Must address
      - gap: "..."
        impact: "..."
        suggested_requirements: [...]
    recommended:  # Should address
      - gap: "..."
        impact: "..."
        suggested_requirements: [...]

  quality_gate:
    passed: true|false
    threshold: 85%
    actual: X%
```
```

---

## Prompt 5: Traceability Verification

### Context
Verify that all requirements have proper traceability links.

### Prompt Template

```
You are a traceability analyst. Verify the traceability coverage of these requirements.

## Requirements with Traceability Data
{paste requirements with source/impl/test links}

## Traceability Verification Checks

### 1. Upstream Traceability (Source)
- Every requirement should trace to a source
- Valid sources: stakeholder interview, document, code analysis, research

### 2. Downstream Traceability (Implementation)
- Implemented requirements should link to code
- Format: `module.py:function` or `class.method`

### 3. Test Traceability
- Requirements should link to test cases
- Format: `test_module.py::test_function`

### 4. Knowledge Entry Links (AARE-F)
- Research-backed requirements should link to KE entries
- Format: KE-{CATEGORY}-{NUMBER}

### 5. Orphan Detection
- Implementation without requirements (scope creep)
- Tests without requirements (over-testing)
- Requirements without tests (under-testing)

## Output Format

```yaml
traceability_verification:
  total_requirements: X

  coverage:
    source_traced: X%  # Target: 100%
    implementation_traced: X%  # Target: 90%+ for implemented
    test_traced: X%  # Target: 90%+
    ke_linked: X%  # Target: 50%+ for research-backed

  issues:
    missing_source:
      count: X
      requirements: [...]

    missing_implementation:
      count: X
      requirements: [...]  # Should be implemented but no link

    missing_tests:
      count: X
      requirements: [...]

    orphan_implementations:
      count: X
      files: [...]  # Code without requirements

    orphan_tests:
      count: X
      files: [...]  # Tests without requirements

  quality_gate:
    source_coverage:
      passed: true|false
      threshold: 100%
      actual: X%
    test_coverage:
      passed: true|false
      threshold: 90%
      actual: X%
```
```

---

## Aggregate Quality Dashboard

Use this prompt to generate an overall quality report:

```
Combine the results of:
1. PRD Quality Audit
2. Ambiguity Analysis
3. Consistency Check
4. Completeness Assessment
5. Traceability Verification

Generate a quality dashboard:

```yaml
quality_dashboard:
  date: "..."
  scope: "..."

  overall_health: GOOD|WARNING|CRITICAL

  metrics:
    prd_audit_score: X/100
    ambiguity_rate: X%
    consistency_score: X%
    completeness_score: X%
    traceability_coverage: X%

  gates_passed: X/5

  priority_actions:
    - priority: P0
      action: "..."
      impact: "..."
    - priority: P1
      action: "..."
      impact: "..."
```
```

---

## Key Requirements to Verify

Before submitting your validation output, re-read and verify:
1. Every finding has a specific location, severity, and actionable fix suggestion
2. The ambiguity rate is calculated as (ambiguous requirements / total requirements)
3. All quality gate thresholds are evaluated with pass/fail determination
4. Improvement recommendations are prioritized (P0/P1/P2) with estimated impact

Re-read each audit section's scoring criteria above to confirm all checks were applied.

---

## Related Prompts

- [Requirements Elicitation](requirements-elicitation.md) - Source of requirements
- [PRD Creation](prd-creation.md) - Create validated PRDs
- [Conflict Resolution](conflict-resolution.md) - Resolve detected conflicts

---

*Quality Validation Prompts v1.0.0*
*AARE-F Framework v1.1.0 Implementation*
