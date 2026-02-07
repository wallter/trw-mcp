---
# PRD Metadata (LLM-Parseable)
# Research basis: AARE-F Framework v1.1.0
# AARE-F Components: C1 (Traceability), C2 (Governance), C7 (Req-as-Code)
# Findings: F2 (LLM-parseable), F3 (confidence), F7 (metrics), F19 (traceability)

prd:
  id: PRD-{CATEGORY}-{SEQUENCE}
  title: "{Title}"
  version: "1.0"
  status: draft  # draft | review | approved | implemented | deprecated
  priority: P1   # P0 (critical) | P1 (high) | P2 (medium) | P3 (low)

# AARE-F Component mapping (which framework components this PRD addresses)
aaref_components: []  # C1, C2, C3, C4, C5, C6, C7, C8, C9, C10

# Evidence and confidence (AARE-F Finding F3: confidence expectations for AI systems)
evidence:
  level: moderate  # strong | moderate | limited | theoretical
  sources: []      # List of source documents, knowledge entries, research waves

confidence:
  implementation_feasibility: 0.8  # 0.0-1.0
  requirement_clarity: 0.8         # 0.0-1.0
  estimate_confidence: 0.7         # 0.0-1.0
  test_coverage_target: 0.85       # 0.0-1.0 (NEW: expected test coverage)

# Traceability (AARE-F C1, Finding F19: GPS of compliance-by-design)
traceability:
  implements: []       # Knowledge entries implemented (e.g., KE-FRAME-002)
  depends_on: []       # Other PRDs this depends on
  enables: []          # Downstream PRDs enabled by this
  conflicts_with: []   # Document in CONFLICTS.md with resolution

# Success metrics (AARE-F C9, Finding F7: automated quality metrics)
metrics:
  success_criteria: []
  measurement_method: []

# Service Level Objectives (AARE-F C9: Observability)
slos: []  # List of SLO definitions with targets

dates:
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  target_completion: null

# Wave linkage (if derived from research)
wave_source: null  # e.g., "Wave 25" or "KE-SYNTH-010"

# Quality gate thresholds (AARE-F C6: Uncertainty Management)
quality_gates:
  ambiguity_rate_max: 0.05      # <= 5%
  completeness_min: 0.85        # >= 85%
  traceability_coverage_min: 0.90  # >= 90%
---

# PRD-{CATEGORY}-{SEQUENCE}: {Title}

**Quick Reference**:
- **Status**: Draft | Review | Approved | Implemented
- **Priority**: P0 | P1 | P2 | P3
- **Evidence**: Strong | Moderate | Limited | Theoretical
- **Implementation Confidence**: 0.8

---

## 1. Problem Statement

### Background
{Brief context explaining why this feature/fix is needed}

### Problem
{Clear statement of the problem being solved}

### Impact
{Who is affected and how}

---

## 2. Goals & Non-Goals

### Goals
- [ ] {Goal 1 - specific, measurable}
- [ ] {Goal 2}

### Non-Goals
- {What this PRD explicitly does NOT address}

---

## 3. User Stories

### US-001: {User Story Title}
**As a** {role}
**I want** {capability}
**So that** {benefit}

**Confidence Expectation**: high | medium | low
<!-- For AI systems: What level of consistency is expected for this behavior? -->

**Evidence Required**: {What evidence validates this story is complete}

**Uncertainty Notes**: {Known unknowns, especially for AI/LLM behaviors}
<!-- Document any non-deterministic behaviors expected -->

**Acceptance Criteria**:
- [ ] Given {context}, When {action}, Then {outcome} `[confidence: 0.95]`
- [ ] Given {context}, When {action}, Then {outcome} `[confidence: 0.80]`

---

## 4. Functional Requirements

### PRD-{CAT}-{SEQ}-FR01: {Requirement Title}
**Priority**: Must Have | Should Have | Nice to Have
**Description**: {Detailed description}
**Acceptance**: {Testable criteria}
**Dependencies**: {Other requirements this depends on}
**Confidence**: 0.9 <!-- How certain is this requirement well-defined? -->

### PRD-{CAT}-{SEQ}-FR02: {Requirement Title}
...

---

## 5. Non-Functional Requirements

### PRD-{CAT}-{SEQ}-NFR01: Performance
- {Response time targets}
- {Throughput targets}

### PRD-{CAT}-{SEQ}-NFR02: Reliability
- {Uptime targets}
- {Error handling requirements}

### PRD-{CAT}-{SEQ}-NFR03: Security
- {Security requirements}

---

## 6. Technical Approach

### Architecture Impact
{How this affects existing architecture}

### Key Files
| File | Changes |
|------|---------|
| `path/to/file.py` | {Description of changes} |

### API Changes
{New or modified APIs}

---

## 7. Test Strategy

### Unit Tests
- [ ] {Test case 1}
- [ ] {Test case 2}

### Integration Tests
- [ ] {Integration test 1}

### Acceptance Tests
- [ ] {Maps to AC from user stories}

---

## 8. Rollout Plan

### Phase 1: Development
- {Tasks}

### Phase 2: Testing
- {Tasks}

### Phase 3: Release
- {Tasks}

### Rollback Plan
{How to revert if issues arise}

---

## 9. Success Metrics

| Metric | Target | Measurement Method | Confidence |
|--------|--------|-------------------|------------|
| {Metric 1} | {Target} | {How measured} | 0.9 |

---

## 10. Dependencies & Risks

### Dependencies
| ID | Description | Status | Blocking |
|----|-------------|--------|----------|
| DEP-001 | {Dependency} | Resolved/Pending | Yes/No |

### Risks
| ID | Risk | Probability | Impact | Mitigation | Residual Risk |
|----|------|-------------|--------|------------|---------------|
| RISK-001 | {Risk} | Low/Med/High | Low/Med/High | {Mitigation} | Low/Med/High |

---

## 11. Open Questions

- [ ] {Question 1} `[blocking: yes/no]`
- [ ] {Question 2} `[blocking: yes/no]`

---

## 12. Traceability Matrix

<!-- Finding F19: Bidirectional traceability is foundational -->

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | {KE-XXX or wave} | `module.py:line` | `test_module.py` | Pending |
| FR02 | {source} | {impl location} | {test location} | Pending |

### Knowledge Entry Links
- **Implements**: {List knowledge entries this PRD implements}
- **Informs**: {List knowledge entries that informed this PRD}

---

## Appendix

### Related PRDs
- PRD-XXX-001: {Related PRD}

### Conflict Resolution
<!-- If this PRD conflicts with others, document resolution -->
- See CONFLICTS.md#{CONFLICT-ID} for resolution details

### References
- {Link to relevant docs}
- {Link to knowledge catalogue entries}

---

## Quality Checklist (AARE-F Compliance)

Before submitting this PRD for review, verify:

### Structure (AARE-F C7: Req-as-Code)
- [ ] YAML frontmatter complete with all required fields
- [ ] All 12 sections present
- [ ] Unique PRD ID assigned
- [ ] Version documented

### Content Quality (AARE-F C2: Governance)
- [ ] Problem statement is clear and specific
- [ ] Goals are measurable (SMART)
- [ ] Non-goals explicitly stated
- [ ] User stories follow standard format

### Confidence & Evidence (AARE-F C6: Uncertainty)
- [ ] All requirements have confidence scores (0.0-1.0)
- [ ] User stories have "Evidence Required" field
- [ ] Acceptance criteria have confidence scores
- [ ] Evidence level documented with sources

### Traceability (AARE-F C1: Traceability)
- [ ] Source traceability complete
- [ ] Knowledge entry links documented
- [ ] Implementation files identified (if known)
- [ ] Test files identified (if known)

### Risk Management (AARE-F C3: Risk-Based Rigor)
- [ ] Risk table has "Residual Risk" column
- [ ] Mitigation strategies documented
- [ ] Dependencies tracked with blocking status

### Quality Gates
- [ ] Ambiguity rate <= 5% (no vague terms)
- [ ] Completeness >= 85% (all required sections)
- [ ] Traceability >= 90% (linked requirements)

---

*Template version: 2.1 (AARE-F v1.1.0 Enhanced)*
*Research basis: AARE-F Framework v1.1.0*
*Prompts: docs/requirements-aare-f/prompts/prd-creation.md*
