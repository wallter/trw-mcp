# PRD Creation Prompts

**Version**: 1.0.0
**AARE-F Components**: C1 (Traceability), C2 (Governance), C3 (Risk Scaling), C7 (Req-as-Code)
**Research Basis**: Wave 8 (Specification Formats), Wave 9 (LLM Integration)

---

## Purpose

These prompts help create AARE-F compliant Product Requirement Documents from:
- Extracted requirements
- User stories
- Feature requests
- Bug reports

---

## Prompt 1: PRD Generation from Requirements

### Context
Generate a complete PRD from a set of requirements, following the AARE-F template.

### Prompt Template

```
You are a product manager creating an AARE-F compliant PRD. Generate a complete PRD from the following requirements.

## Input Requirements
{paste requirements list}

## PRD Context
- Project: {project name}
- Category: {CORE | QUAL | INFRA | LOCAL | EXPLR | RESEARCH | etc.}
- Sprint Target: {sprint number or "backlog"}

## Generation Guidelines

1. **YAML Frontmatter** (AARE-F Finding F2):
   - Generate unique PRD ID: PRD-{CATEGORY}-{SEQUENCE}
   - Set appropriate priority based on impact
   - Document evidence level and sources
   - Include confidence scores (0.0-1.0)
   - Map traceability links

2. **User Stories** (AARE-F Finding F3):
   - Format: "As a [role], I want [capability] so that [benefit]"
   - Include confidence expectations for AI behaviors
   - Add "Evidence Required" field
   - Document uncertainty notes for non-deterministic behaviors

3. **Functional Requirements**:
   - Use "shall" for mandatory requirements
   - Include acceptance criteria with confidence scores
   - Add testable criteria
   - Link to knowledge entries where applicable
   - Prefer EARS-style wording (`When`, `If`, `While`, `Where`) for FR descriptions

4. **Risk Assessment** (AARE-F C3):
   - Apply risk-based rigor scaling
   - Include residual risk column
   - Document mitigation strategies

5. **Traceability Matrix** (AARE-F C1):
   - Link requirements to source documents
   - Map to implementation files (if known)
   - Reference test files
   - Include knowledge entry links
   - Use backtick-wrapped repo-relative file paths like `src/module/file.py` and `tests/test_module.py`
   - Avoid prose-only implementation cells; include one concrete implementation path and one concrete test path per FR row when grounded

## Output Format

Generate a complete PRD following this structure:

```yaml
---
prd:
  id: PRD-{CATEGORY}-{SEQUENCE}
  title: "{Title}"
  version: "1.0"
  status: draft
  priority: P1

evidence:
  level: moderate
  sources: []

confidence:
  implementation_feasibility: 0.8
  requirement_clarity: 0.8
  estimate_confidence: 0.7

traceability:
  implements: []
  depends_on: []
  enables: []
  conflicts_with: []

metrics:
  success_criteria: []
  measurement_method: []

dates:
  created: {today}
  updated: {today}
  target_completion: null
---

# PRD-{CATEGORY}-{SEQUENCE}: {Title}

[Generate complete PRD sections 1-12 per template]
```

## Quality Criteria

The generated PRD must:
- [ ] Have all 12 required sections
- [ ] Include YAML frontmatter with all fields
- [ ] Have confidence scores on all requirements
- [ ] Include "Evidence Required" on user stories
- [ ] Have testable acceptance criteria
- [ ] Include risk table with residual risk
- [ ] Have complete traceability matrix
- [ ] Use concrete backtick-wrapped implementation and test paths in the traceability matrix when grounded
```

---

## Prompt 2: PRD Enhancement from Existing Document

### Context
Enhance an existing PRD to meet AARE-F v1.1 compliance standards.

### Prompt Template

```
You are a requirements engineer enhancing a PRD for AARE-F v1.1 compliance. Analyze and improve the following PRD.

## Existing PRD
{paste existing PRD content}

## Enhancement Checklist

1. **YAML Frontmatter Check**:
   - [ ] All required fields present?
   - [ ] Evidence level documented?
   - [ ] Confidence scores (0.0-1.0)?
   - [ ] Traceability links?

2. **User Story Enhancement**:
   - [ ] Add "Confidence Expectation" field if missing
   - [ ] Add "Evidence Required" field if missing
   - [ ] Add "Uncertainty Notes" for AI behaviors
   - [ ] Add confidence scores to acceptance criteria

3. **Requirements Enhancement**:
   - [ ] Add confidence score to each requirement
   - [ ] Ensure testable criteria exist
   - [ ] Link to knowledge entries (KE-*)
   - [ ] Use "shall" for mandatory, "should" for recommended

4. **Risk Table Enhancement**:
   - [ ] Add "Residual Risk" column if missing
   - [ ] Ensure probability/impact documented
   - [ ] Document mitigation strategies

5. **Traceability Matrix Enhancement**:
   - [ ] Add "Knowledge Entry Links" subsection
   - [ ] Ensure all requirements have sources
   - [ ] Map to implementation files
   - [ ] Map to test files

## Output Format

Provide:
1. **Compliance Score**: X/10 with breakdown
2. **Issues Found**: List of non-compliant items
3. **Enhanced PRD**: Complete corrected document
4. **Change Summary**: What was modified and why

```yaml
compliance_assessment:
  overall_score: X/10
  issues:
    high_severity:
      - issue: "..."
        fix: "..."
    medium_severity:
      - issue: "..."
        fix: "..."
    low_severity:
      - issue: "..."
        fix: "..."

changes_made:
  - section: "..."
    change: "..."
    rationale: "..."
```
```

---

## Prompt 3: Quick PRD from Feature Request

### Context
Rapidly generate a PRD skeleton from an informal feature request.

### Prompt Template

```
You are a product manager rapidly drafting a PRD from a feature request. Create a draft PRD for review.

## Feature Request
{paste feature request, bug report, or informal description}

## Quick Generation Guidelines

1. **Infer Category**: CORE | QUAL | INFRA | LOCAL | EXPLR | RESEARCH | FIX
2. **Assess Priority**:
   - P0: System broken, blocking
   - P1: Major feature/quality impact
   - P2: Enhancement
   - P3: Nice to have
3. **Extract User Need**: What problem is being solved?
4. **Define Scope**: What's in/out of scope?

## Output Format

Generate a draft PRD with:
- YAML frontmatter (confidence: 0.6-0.7 for draft)
- Problem statement
- Goals/Non-goals
- 1-3 user stories
- 3-5 functional requirements
- Basic risk assessment
- Open questions (things to clarify)

Mark sections needing human input with: `<!-- NEEDS REVIEW: ... -->`

```yaml
---
prd:
  id: PRD-{CATEGORY}-XXX  # XXX = TBD
  title: "{Inferred Title}"
  version: "0.1"
  status: draft
  priority: {inferred}

confidence:
  implementation_feasibility: 0.6  # Draft - needs validation
  requirement_clarity: 0.6
  estimate_confidence: 0.5
---

# PRD-{CATEGORY}-XXX: {Title}

<!-- DRAFT: Generated from feature request. Requires human review. -->

[Generate skeleton PRD]
```
```

---

## Prompt 4: PRD Decomposition

### Context
Break down a large PRD into smaller, implementable PRDs.

### Prompt Template

```
You are a technical product manager decomposing a large PRD into smaller deliverables.

## Large PRD
{paste large PRD}

## Decomposition Guidelines

1. **Identify Natural Boundaries**:
   - Separate user-facing from infrastructure
   - Separate high-risk from low-risk
   - Separate dependencies from dependents

2. **Create Dependency Graph**:
   - Which pieces must come first?
   - What can be parallelized?
   - What creates integration points?

3. **Size Each Sub-PRD**:
   - Target: 1-2 week implementation
   - Include: 3-7 functional requirements
   - Self-contained: Can be tested independently

4. **Maintain Traceability**:
   - Link sub-PRDs to parent
   - Preserve requirement IDs
   - Document split rationale

## Output Format

```yaml
decomposition:
  parent_prd: PRD-XXX-001

  sub_prds:
    - id: PRD-XXX-001a
      title: "..."
      scope: "..."
      requirements: [FR01, FR02, FR03]
      effort: "1 week"
      dependencies: []

    - id: PRD-XXX-001b
      title: "..."
      scope: "..."
      requirements: [FR04, FR05]
      effort: "1 week"
      dependencies: [PRD-XXX-001a]

  dependency_graph: |
    PRD-001a ──► PRD-001b
         │
         └──► PRD-001c

  implementation_order:
    phase_1: [PRD-001a]
    phase_2: [PRD-001b, PRD-001c]  # Can parallelize
```
```

---

## Quality Checklist for Generated PRDs

After generation, verify:

### YAML Frontmatter
- [ ] Unique PRD ID assigned
- [ ] Priority documented with rationale
- [ ] Evidence level and sources listed
- [ ] All confidence scores present (0.0-1.0)
- [ ] Traceability links complete

### Content Quality
- [ ] Problem statement is clear and specific
- [ ] Goals are measurable
- [ ] Non-goals explicitly stated
- [ ] User stories follow standard format
- [ ] All requirements use "shall/should" language
- [ ] Acceptance criteria are testable

### AARE-F Compliance
- [ ] User stories have "Evidence Required"
- [ ] Acceptance criteria have confidence scores
- [ ] Risk table has "Residual Risk" column
- [ ] Traceability matrix has "Knowledge Entry Links"
- [ ] Open questions tagged as blocking/non-blocking

---

## Key Requirements to Verify

Before submitting your PRD, re-read and verify:
1. All 12 AARE-F sections are present with substantive content (not just headers)
2. Every functional requirement has a confidence score (0.0-1.0) and evidence level
3. The traceability matrix links every requirement to source, implementation, and test
4. Non-goals explicitly state what is out of scope and why

Re-read the structure checklist and AARE-F compliance checklist above to confirm completeness.

---

## Related Prompts

- [Requirements Elicitation](requirements-elicitation.md) - Extract requirements first
- [Quality Validation](quality-validation.md) - Validate generated PRDs
- [Conflict Resolution](conflict-resolution.md) - Resolve conflicts between PRDs

---

*PRD Creation Prompts v1.0.0*
*AARE-F Framework v1.1.0 Implementation*
