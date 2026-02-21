# Requirements Elicitation Prompts

**Version**: 1.0.0
**AARE-F Components**: C1 (Traceability), C2 (Governance)
**Research Basis**: Wave 7 (Elicitation Techniques), Wave 9 (LLM Integration)

---

## Purpose

These prompts help extract, analyze, and structure requirements from various sources including:
- Stakeholder interviews
- Existing documentation
- Code analysis
- User feedback

---

## Prompt 1: Requirements Extraction from Documentation

### Context
Extract structured requirements from unstructured documentation such as design docs, meeting notes, or technical specifications.

### Prompt Template

```
You are a requirements engineer applying the AARE-F framework. Analyze the following documentation and extract structured requirements.

## Documentation to Analyze
{paste documentation here}

## Extraction Guidelines

1. **Identify Requirements Types**:
   - Functional (FR): What the system must DO
   - Non-Functional (NFR): How the system must PERFORM
   - Architecture (AR): How the system must be STRUCTURED
   - Security (SEC): How the system must be PROTECTED

2. **For Each Requirement, Provide**:
   - **ID**: {PREFIX}-{CATEGORY}-{NUMBER}
   - **Title**: Concise name (3-7 words)
   - **Description**: Clear statement of the requirement
   - **Priority**: P0 (critical) | P1 (high) | P2 (medium) | P3 (low)
   - **Source**: Where in the document this was found
   - **Confidence**: 0.0-1.0 (how certain this is a valid requirement)
   - **Ambiguity Notes**: Any unclear aspects

3. **Quality Criteria**:
   - Each requirement should be atomic (single testable item)
   - Use "shall" for mandatory, "should" for recommended
   - Avoid vague terms: "fast", "user-friendly", "robust"
   - Include measurable criteria where possible

## Output Format

```yaml
requirements:
  - id: FR-CORE-001
    title: "..."
    description: "The system shall..."
    priority: P1
    source: "Section 2.3, paragraph 1"
    confidence: 0.85
    ambiguity_notes: "..."
    testable_criteria: "..."
```

## Validation
After extraction, flag any requirements with:
- Confidence < 0.7 (needs human clarification)
- Missing testable criteria
- Potential conflicts with other requirements
```

---

## Prompt 2: Stakeholder Interview Analysis

### Context
Analyze interview transcripts or notes to extract implicit and explicit requirements.

### Prompt Template

```
You are a requirements analyst applying AARE-F framework. Analyze this stakeholder interview and extract requirements.

## Interview Content
{paste interview transcript or notes}

## Analysis Framework

1. **Identify Stakeholder Needs**:
   - Explicit needs (directly stated)
   - Implicit needs (inferred from context)
   - Emotional needs (frustrations, desires)

2. **Map to Requirement Types**:
   - User Story: "As a [role], I want [capability] so that [benefit]"
   - Functional Requirement: "The system shall..."
   - Quality Attribute: Performance, reliability, usability

3. **Confidence Assessment**:
   - Direct quote: 0.9-1.0
   - Paraphrased statement: 0.7-0.9
   - Inference from context: 0.5-0.7
   - Speculation: < 0.5 (flag for validation)

4. **Extract Supporting Evidence**:
   - Direct quotes with attribution
   - Context that supports the interpretation

## Output Format

```yaml
stakeholder:
  role: "..."
  date: "..."

user_stories:
  - id: US-001
    story: "As a [role], I want [capability] so that [benefit]"
    confidence: 0.85
    evidence: "Quote: '...'"
    uncertainty_notes: "..."

requirements:
  - id: FR-XXX-001
    title: "..."
    description: "..."
    priority: P1
    confidence: 0.80
    source: "Interview with [stakeholder], [date]"

follow_up_questions:
  - "Clarification needed on..."
  - "Validate assumption that..."
```

---

## Prompt 3: Code-to-Requirements Reverse Engineering

### Context
Extract implicit requirements from existing code when documentation is missing.

### Prompt Template

```
You are a requirements analyst reverse-engineering requirements from code. Apply AARE-F principles to extract implicit requirements.

## Code to Analyze
{paste code snippet or module}

## Analysis Framework

1. **Functional Requirements**:
   - What does this code DO?
   - What inputs does it accept?
   - What outputs does it produce?
   - What side effects does it have?

2. **Non-Functional Requirements**:
   - Error handling patterns -> Reliability requirements
   - Validation logic -> Data integrity requirements
   - Logging/monitoring -> Observability requirements
   - Performance optimizations -> Performance requirements

3. **Architecture Requirements**:
   - Module dependencies -> Integration requirements
   - Data structures -> Data model requirements
   - API contracts -> Interface requirements

4. **Confidence Scoring**:
   - Explicit in code (assertions, contracts): 0.9+
   - Implicit in implementation: 0.7-0.9
   - Inferred from patterns: 0.5-0.7

## Output Format

```yaml
code_analysis:
  file: "..."
  functions_analyzed: [...]

inferred_requirements:
  - id: FR-XXX-001
    title: "..."
    description: "The system shall..."
    evidence: "Inferred from function X at line Y"
    confidence: 0.75
    validation_needed: "Confirm with original developer"

architectural_constraints:
  - "Module X depends on Y"
  - "Data must be in format Z"

gaps_identified:
  - "No error handling for case X"
  - "Missing validation for input Y"
```

---

## Prompt 4: Requirements Gap Analysis

### Context
Compare existing requirements against implementation to identify gaps.

### Prompt Template

```
You are a requirements analyst performing gap analysis. Compare documented requirements against implementation status.

## Requirements Document
{paste requirements}

## Implementation Status
{paste implementation details, code references, or test coverage}

## Analysis Framework

1. **Coverage Analysis**:
   - Fully implemented requirements
   - Partially implemented requirements
   - Not implemented requirements
   - Implemented but undocumented features

2. **Traceability Check** (AARE-F C1):
   - Requirements with implementation links
   - Requirements without implementation links
   - Implementation without requirement links (potential scope creep)

3. **Quality Assessment**:
   - Requirements testability
   - Requirements clarity
   - Requirements completeness

## Output Format

```yaml
gap_analysis:
  date: "..."

  coverage:
    fully_implemented:
      count: X
      ids: [...]
    partially_implemented:
      count: X
      items:
        - id: FR-XXX-001
          missing: "..."
    not_implemented:
      count: X
      ids: [...]

  traceability:
    linked: X%
    unlinked_requirements: [...]
    orphan_implementations: [...]

  recommendations:
    - priority: P0
      action: "..."
      rationale: "..."
```

---

## Quality Checklist

After using any elicitation prompt, validate:

- [ ] All requirements have unique IDs
- [ ] All requirements have confidence scores
- [ ] Low-confidence items (< 0.7) flagged for human review
- [ ] Ambiguous terms identified
- [ ] Sources documented for traceability
- [ ] Potential conflicts noted
- [ ] Follow-up questions listed

---

## Key Requirements to Verify

Before submitting your extraction output, re-read and verify:
1. Every extracted requirement has an ID, priority level, and confidence score
2. Implicit requirements are explicitly documented with their supporting assumptions
3. Follow-up questions cover any ambiguous or incomplete requirements
4. All requirements use "shall/should" language with testable acceptance criteria

Re-read the output format section above and confirm all required fields are present.

---

## Related Prompts

- [PRD Creation](prd-creation.md) - Create PRDs from extracted requirements
- [Quality Validation](quality-validation.md) - Validate extracted requirements
- [Conflict Resolution](conflict-resolution.md) - Resolve conflicting requirements

---

*Requirements Elicitation Prompts v1.0.0*
*AARE-F Framework v1.1.0 Implementation*
