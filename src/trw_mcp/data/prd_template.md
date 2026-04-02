---
# PRD Metadata (LLM-Parseable)
# Research basis: AARE-F Framework v1.1.0
# AARE-F Components: C1 (Traceability), C2 (Governance), C7 (Req-as-Code)
# Findings: F2 (LLM-parseable), F3 (confidence), F7 (metrics), F19 (traceability), F24 (implementation completeness)

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
**Status**: active
**Description**: {Detailed description}
**Acceptance**: {Testable criteria}
**Dependencies**: {Other requirements this depends on}
**Confidence**: 0.9 <!-- How certain is this requirement well-defined? -->

**Assertions** (optional):
<!-- Machine-verifiable assertions for this FR. Only add for convention/structure FRs. -->
<!-- - `grep_present: "pattern" in "target/glob/**/*.py"` -->
<!-- - `grep_absent: "anti_pattern" in "target/**/*.py"` -->
<!-- - `glob_exists: "path/to/expected/file.py"` -->
<!-- - `glob_absent: "path/to/removed/file.py"` -->

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

### Primary Control Points
List the runtime surfaces that MUST change for this PRD to be truly implemented.

| Surface | Current Behavior | Required Change | Code Path | Proof |
|---------|------------------|-----------------|-----------|-------|
| Generation | {What creates artifacts today} | {What must create/update now} | `path/to/source.py` | {How you will verify it} |
| Config / Discovery | {What points clients to behavior today} | {What must point to new behavior} | `path/to/config.ext` | {Config assertion or integration test} |
| Sync / Update | {What keeps it current after bootstrap} | {What must update later runs} | `path/to/sync.py` | {Update-project / deliver proof} |
| Migration | {Legacy state that exists} | {How it transitions safely} | `path/to/migrate.py` | {Migration fixture / idempotency proof} |

### Behavior Switch Matrix
For each requirement, show the concrete behavior change and the executable proof.

| Requirement | Old Behavior | New Behavior | Trigger | Code Path | Proof Test |
|-------------|--------------|--------------|---------|-----------|------------|
| FR01 | {Old behavior} | {New behavior} | {init/update/runtime action} | `path/to/file.py` | `tests/test_feature.py::test_fr01` |

### Key Files
| File | Changes |
|------|---------|
| `path/to/file.py` | {Description of changes} |
| `path/to/test_file.py` | {Tests or validation wiring affected} |

### API Changes
{New or modified APIs}

---

## 7. AI/LLM Operational Sections

### Data / Context Provenance
{Data sources and provenance chain for AI/LLM/agentic behavior}
- **Data Sources**: List datasets, APIs, or external sources used
- **Provenance Chain**: Input → Processing → Model → Output
- **Data Quality Signals**: Validation metrics, drift detection thresholds

### Failure Modes / Safe Degradation
{Fallback behavior when AI/LLM/agentic systems fail}
- **Failure Mode**: What happens when the AI system fails?
- **Safe Behavior**: Expected fallback behavior (e.g., rule-based heuristic, cached response)
- **Escalation Path**: Human-in-the-loop review when confidence is low

### Human Oversight / Escalation
{Human review requirements for AI decisions}
- **Review Triggers**: Confidence scores, uncertainty thresholds, high-stakes decisions
- **Escalation Paths**: How to escalate to human reviewer
- **Audit Trail**: What decisions are logged for later review

### Evaluation Plan
{How AI/LLM/agentic behavior is evaluated and baselined}
- **Baseline Criteria**: Acceptable performance thresholds (accuracy, latency, reliability)
- **Evaluation Method**: A/B test, user study, automated metrics
- **Performance Metrics**: Key metrics to track (e.g., p99 latency, error rate)

### Release Gate
{Rollout conditions and rollback triggers for AI/LLM features}
- **Rollout Strategy**: Canary (5% → 25% → 100%), phased over time
- **Rollback Triggers**: Error rate > X%, latency P99 > Yms, confidence < Z
- **Canary Duration**: Minimum time per phase before next increment

### Monitoring Plan
{Live monitoring signals and triggers for AI/LLM features}
- **Primary Signal**: Key metric to monitor (prediction latency, drift score, error rate)
- **Target Threshold**: Acceptable value or action triggers
- **Escalation Action**: What happens when threshold exceeded (scale, alert, rollback)

### Risk Register By Failure Class
{Failure modes specific to AI/LLM/agentic behavior}
| Failure Class | Scenario | Detection | Mitigation | Residual Risk |
|---------------|----------|-----------|------------|---------------|
| Correctness | Low confidence predictions | Confidence score < 0.6 | Human review | {Low/Med/High} |
| Safety | Unsafe recommendations | Safety filter triggered | Halt, alert, revert | {Low/Med/High} |
| Maintainability | Model drift | Performance degradation | Retrain, alert | {Low/Med/High} |
| Governance | Unattributable output | No source attribution | Human review | {Low/Med/High} |

---

## 8. Test Strategy

### Unit Tests
- [ ] {Test case 1}
- [ ] {Test case 2}

### Integration Tests
- [ ] {Integration test 1}

### Acceptance Tests
- [ ] {Maps to AC from user stories}
- [ ] `FR01` -> `tests/test_feature.py`

### Migration Tests
- [ ] {Legacy state is migrated to the new state}
- [ ] {Migration is idempotent on repeated runs}

### Regression Tests
- [ ] {Exact prior failure mode stays fixed}
- [ ] {Adjacent code path remains correct}

### Negative / Fallback Tests
- [ ] {Missing config / absent file / partial state fails safely}
- [ ] {Fallback behavior is explicit and tested}

### Completion Evidence (Definition of Done)
- [ ] Generated artifacts exist at the intended paths
- [ ] Runtime/config consumers reference the new behavior
- [ ] Sync/update flows keep the new behavior current
- [ ] Legacy state migrates or is explicitly declared out of scope
- [ ] Acceptance proof exists for each user story / FR
- [ ] Regression and fallback coverage exist for the changed control points

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

### Migration / Backward Compatibility
- {State what legacy surfaces remain}
- {State how mixed old/new state is handled}
- {State whether repeated update/init runs are idempotent}

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

Use backtick-wrapped repo-relative file paths so validation can count them:

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | user request or prior PRD | `src/module/file.py` | `tests/test_module.py` | Pending |
| FR02 | `PRD-CORE-001` | `src/module/other.py:42` | `tests/test_other.py` | Pending |

---

<!-- Finding F19: Bidirectional traceability is foundational -->

| Requirement | Source | Implementation | Test | Status |
|-------------|--------|----------------|------|--------|
| FR01 | {KE-XXX or wave} | `module.py:line` | `test_module.py` | Pending |
| FR02 | {source} | {impl location} | {test location} | Pending |

### Knowledge Entry Links
- **Implements**: {List knowledge entries this PRD implements}
- **Informs**: {List knowledge entries that informed this PRD}

---

<!-- FIX category variant sections (retained when category=FIX) -->

## 13. Root Cause Analysis

### Root Cause
{Identify the root cause of the defect or incident being fixed}

### Contributing Factors
- {Factor 1 — e.g., missing validation, race condition, stale cache}
- {Factor 2}

### Fix Verification
- [ ] Root cause addressed (not just symptoms)
- [ ] Regression test covers the exact failure mode
- [ ] Related code paths reviewed for similar issues

---

## 14. Rollback Plan

{Standalone rollback procedure — steps to revert this fix if it introduces new issues}

- [ ] Database migration reversible (if applicable)
- [ ] Feature flag available for kill switch (if applicable)
- [ ] Rollback tested in staging

---

<!-- RESEARCH category variant sections (retained when category=RESEARCH) -->

## 15. Background & Prior Art

{Summary of existing research, prior attempts, and relevant literature}

- {Prior art 1 — link or description}
- {Prior art 2}

---

## 16. Research Questions

- [ ] RQ1: {Primary research question} `[blocking: yes]`
- [ ] RQ2: {Secondary research question} `[blocking: no]`

---

## 17. Methodology

### Approach
{Describe the research methodology — experiments, surveys, benchmarks, etc.}

### Data Sources
- {Source 1}
- {Source 2}

### Evaluation Criteria
- {Criterion 1 — how findings will be assessed}

---

## 18. Findings

{Document research findings here — updated as research progresses}

| Finding | Evidence | Confidence | Impact |
|---------|----------|------------|--------|
| {Finding 1} | {Evidence} | High/Med/Low | {Impact on project} |

---

## 19. Recommendations

{Actionable recommendations derived from findings}

- [ ] Recommendation 1: {Description} `[priority: P1]`
- [ ] Recommendation 2: {Description} `[priority: P2]`

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
- [ ] All category-required sections present (Feature: 12, FIX: 14, INFRA: 12, Research: 17)
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
- [ ] Primary control points named for generation, config, sync, and migration surfaces
- [ ] Behavior Switch Matrix populated with proof tests

### Risk Management (AARE-F C3: Risk-Based Rigor)
- [ ] Risk table has "Residual Risk" column
- [ ] Mitigation strategies documented
- [ ] Dependencies tracked with blocking status

### Quality Gates
- [ ] Ambiguity rate <= 5% (no vague terms)
- [ ] V2 total_score >= 65 (REVIEW tier minimum)
- [ ] Traceability >= 90% (linked requirements)
- [ ] Completion Evidence proves generated, referenced, updated, migrated, and tested surfaces

---

*Template version: 2.3 (AARE-F v1.1.0 Enhanced — implementation-readiness hardening)*
*Research basis: AARE-F Framework v1.1.0*
*Prompts: docs/requirements-aare-f/prompts/prd-creation.md*
