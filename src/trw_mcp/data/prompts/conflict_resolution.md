# Conflict Resolution Prompts

**Version**: 1.0.0
**AARE-F Components**: C10 (Conflict Detection and Resolution)
**Research Basis**: Wave 12 (Conflict Resolution), Finding F9, Finding F10

---

## Purpose

These prompts help detect and resolve requirement conflicts using AARE-F strategies:
- Risk-based resolution (safety wins)
- AHP-TOPSIS weighted scoring
- IBIS structured argumentation
- Negotiation protocols

---

## Prompt 1: Conflict Detection

### Context
Systematically detect conflicts between requirements.

### Prompt Template

```
You are a conflict detection specialist applying AARE-F Component C10. Analyze these requirements for conflicts.

## Requirements Set
{paste requirements}

## Conflict Detection Framework

### Type 1: Direct Contradictions
Definition: Requirement A explicitly contradicts Requirement B
Example: "FR-001: Use REST API" vs "FR-010: Use GraphQL API"
Severity: CRITICAL - Cannot both be true

### Type 2: Resource Conflicts
Definition: Requirements compete for limited resources
Example: "NFR-001: < 50ms latency" vs "NFR-005: Encrypt all data"
Severity: HIGH - Trade-off required

### Type 3: Priority Conflicts
Definition: Equal-priority requirements cannot all be satisfied
Example: All P0 requirements exceed budget/timeline
Severity: HIGH - Prioritization needed

### Type 4: Stakeholder Conflicts
Definition: Different stakeholders want opposite behaviors
Example: Security team vs UX team on login flow
Severity: MEDIUM - Negotiation needed

### Type 5: Temporal Conflicts
Definition: Requirements conflict at different times/phases
Example: "During migration, support both systems" vs "Single system of record"
Severity: MEDIUM - Phasing needed

### Type 6: NFR Trade-offs
Definition: Non-functional requirements in tension
Common pairs:
- Performance vs Security
- Usability vs Security
- Flexibility vs Simplicity
- Reliability vs Cost
Severity: MEDIUM - Balance needed

## Output Format

```yaml
conflict_detection:
  requirements_analyzed: X
  conflicts_found: X

  conflicts:
    - id: CONFLICT-001
      type: direct_contradiction|resource|priority|stakeholder|temporal|nfr_tradeoff
      severity: critical|high|medium|low

      requirements:
        - id: "FR-XXX-001"
          statement: "..."
        - id: "FR-XXX-002"
          statement: "..."

      description: "..."

      impact_if_unresolved: "..."

      initial_resolution_options:
        - option: "..."
          feasibility: high|medium|low
          impact: "..."

  conflict_matrix:
    # Which requirements conflict with which
    FR-001: [FR-005, NFR-003]
    FR-005: [FR-001, NFR-010]

  priority_for_resolution:
    critical: [CONFLICT-001, ...]
    high: [CONFLICT-002, ...]
    medium: [CONFLICT-003, ...]
```
```

---

## Prompt 2: Risk-Based Resolution

### Context
Resolve conflicts using risk-based prioritization (AARE-F preferred for safety-critical conflicts).

### Prompt Template

```
You are a risk analyst resolving requirement conflicts. Apply risk-based resolution strategy.

## Conflict to Resolve
{paste conflict details from detection}

## Risk-Based Resolution Framework (AARE-F C3)

### Step 1: Risk Classification
For each conflicting requirement:
- What's the risk if this requirement is NOT met?
- Who is affected? (Users, business, legal, safety)
- What's the probability of harm?
- What's the severity of harm?

Risk Score = Probability x Severity
- Critical (16-25): Safety/health impact, regulatory violation
- High (9-15): Financial loss, reputation damage
- Medium (4-8): Operational inefficiency
- Low (1-3): Minor inconvenience

### Step 2: Risk-Based Prioritization
Higher-risk requirement wins. Document rationale.

### Step 3: Residual Risk Assessment
After resolution:
- What risk remains for the "losing" requirement?
- What mitigation can reduce residual risk?

## Output Format

```yaml
risk_based_resolution:
  conflict_id: "CONFLICT-XXX"

  risk_assessment:
    requirement_a:
      id: "..."
      risk_if_not_met: "..."
      probability: 1-5
      severity: 1-5
      risk_score: X
      affected_parties: [...]

    requirement_b:
      id: "..."
      risk_if_not_met: "..."
      probability: 1-5
      severity: 1-5
      risk_score: X
      affected_parties: [...]

  resolution:
    winner: "requirement_a|requirement_b"
    rationale: "..."

    modifications_to_winner:
      - "..."

    mitigations_for_loser:
      - mitigation: "..."
        residual_risk: high|medium|low

  documentation:
    decision_date: "..."
    decision_maker: "..."
    evidence_basis: "..."

  CONFLICTS_md_entry: |
    ## CONFLICT-XXX: {Title}
    **Status**: Resolved
    **Resolution**: Risk-based (higher-risk requirement prioritized)
    **Winner**: {requirement_id}
    **Rationale**: {rationale}
    **Residual Risk**: {assessment}
    **Date**: {date}
```
```

---

## Prompt 3: AHP-TOPSIS Weighted Resolution

### Context
Resolve conflicts using multi-criteria decision making when risk levels are similar.

### Prompt Template

```
You are a decision analyst using AHP-TOPSIS for requirement conflict resolution.

## Conflict to Resolve
{paste conflict details}

## Stakeholder Weights
{list stakeholders and their relative importance, or ask to determine}

## AHP-TOPSIS Framework

### Step 1: Define Criteria
Standard criteria for requirements:
1. Business Value (how much does this benefit users/business?)
2. Technical Feasibility (how easy to implement?)
3. Risk Reduction (how much risk does this mitigate?)
4. Strategic Alignment (how well does this fit long-term goals?)
5. Stakeholder Priority (who wants this most?)

### Step 2: Pairwise Comparison (AHP)
Compare criteria importance:
- 1: Equal importance
- 3: Moderate importance
- 5: Strong importance
- 7: Very strong importance
- 9: Extreme importance

### Step 3: Score Alternatives (TOPSIS)
For each requirement option, score 1-10 on each criterion.

### Step 4: Calculate Weighted Score
Weighted Score = Sum(criterion_weight x criterion_score)

## Output Format

```yaml
ahp_topsis_resolution:
  conflict_id: "CONFLICT-XXX"

  criteria_weights:  # From AHP pairwise comparison
    business_value: 0.XX
    technical_feasibility: 0.XX
    risk_reduction: 0.XX
    strategic_alignment: 0.XX
    stakeholder_priority: 0.XX

  alternatives:
    requirement_a:
      id: "..."
      scores:
        business_value: X/10
        technical_feasibility: X/10
        risk_reduction: X/10
        strategic_alignment: X/10
        stakeholder_priority: X/10
      weighted_total: X.XX

    requirement_b:
      id: "..."
      scores:
        business_value: X/10
        technical_feasibility: X/10
        risk_reduction: X/10
        strategic_alignment: X/10
        stakeholder_priority: X/10
      weighted_total: X.XX

  resolution:
    winner: "requirement_a|requirement_b"
    margin: X.XX  # Difference in weighted totals
    confidence: high|medium|low  # Based on margin

  sensitivity_analysis:
    # What weight changes would flip the decision?
    flip_conditions:
      - "If business_value weight > 0.XX, requirement_b wins"

  documentation:
    decision_date: "..."
    criteria_source: "..."
    stakeholders_consulted: [...]
```
```

---

## Prompt 4: IBIS Structured Argumentation

### Context
Resolve complex conflicts ("wicked problems") through structured debate.

### Prompt Template

```
You are a facilitation expert using IBIS (Issue-Based Information System) to resolve a complex requirement conflict.

## Conflict to Resolve
{paste conflict details}

## IBIS Framework

### Structure
- **Issue**: The question to be resolved
- **Position**: A possible answer to the issue
- **Argument**: Support or objection to a position
  - Pro: Supports the position
  - Con: Objects to the position
  - Question: Raises sub-issue

### Process
1. Frame the core issue as a question
2. Identify all positions (possible resolutions)
3. For each position, document pro/con arguments
4. Identify sub-issues raised by arguments
5. Iterate until positions are well-understood
6. Select position with strongest argument balance

## Output Format

```yaml
ibis_resolution:
  conflict_id: "CONFLICT-XXX"

  issue:
    question: "Should we [requirement A approach] or [requirement B approach]?"
    context: "..."

  positions:
    position_a:
      statement: "Implement requirement A"
      arguments:
        pro:
          - argument: "..."
            evidence: "..."
            strength: strong|moderate|weak
          - argument: "..."
            evidence: "..."
            strength: strong|moderate|weak
        con:
          - argument: "..."
            evidence: "..."
            strength: strong|moderate|weak
        sub_issues:
          - "What if [edge case]?"

    position_b:
      statement: "Implement requirement B"
      arguments:
        pro:
          - argument: "..."
            evidence: "..."
            strength: strong|moderate|weak
        con:
          - argument: "..."
            evidence: "..."
            strength: strong|moderate|weak
        sub_issues:
          - "..."

    position_c:  # Often emerges: compromise
      statement: "Hybrid approach"
      arguments:
        pro:
          - argument: "..."
        con:
          - argument: "..."

  argument_balance:
    position_a:
      strong_pros: X
      moderate_pros: X
      strong_cons: X
      moderate_cons: X
      net_score: +/-X

    position_b:
      strong_pros: X
      moderate_pros: X
      strong_cons: X
      moderate_cons: X
      net_score: +/-X

  resolution:
    selected_position: "position_X"
    rationale: "..."
    unresolved_concerns: [...]
    mitigation_for_concerns: [...]

  documentation:
    debate_participants: [...]
    debate_date: "..."
    consensus_level: full|majority|decided_by_authority
```
```

---

## Prompt 5: Conflict Documentation

### Context
Document resolved conflicts for CONFLICTS.md register.

### Prompt Template

```
You are a requirements governance specialist. Document this resolved conflict for the official register.

## Resolution Details
{paste resolution from any method above}

## Documentation Requirements (AARE-F Finding F10)

Per AARE-F Finding F10: "Document conflicts and resolutions - they form valuable decision rationale."

### Required Fields
1. Conflict ID: CONFLICT-{YYYYMMDD}-{SEQ}
2. Title: Brief descriptive title
3. Status: Resolved | Acknowledged | Deferred
4. Severity: Critical | High | Medium | Low
5. Conflicting Requirements: List with IDs
6. Resolution Method: Risk-based | AHP-TOPSIS | IBIS | Negotiation
7. Resolution Summary: What was decided
8. Rationale: Why this resolution
9. Residual Risk: What risk remains
10. Date: When resolved
11. Approver: Who approved

## Output Format

Generate entry for CONFLICTS.md:

```markdown
## CONFLICT-{YYYYMMDD}-{SEQ}: {Title}

**Status**: Resolved
**Severity**: {Critical|High|Medium|Low}
**Resolution Method**: {method}
**Date Resolved**: {date}
**Approved By**: {approver}

### Conflicting Requirements

| ID | Description |
|----|-------------|
| {id} | {description} |
| {id} | {description} |

### Resolution

{What was decided}

### Rationale

{Why this resolution was chosen}

### Evidence

- {Source 1}
- {Source 2}

### Residual Risk

| Risk | Severity | Mitigation |
|------|----------|------------|
| {risk} | {level} | {mitigation} |

### Impact

- **Winner**: {requirement_id} - {modification if any}
- **Modified**: {requirement_id} - {how modified}
- **Deferred**: {requirement_id} - {to when/what condition}

### Related

- Knowledge Entry: {KE-XXX-XXX if applicable}
- PRDs Affected: {list}
```

Also generate YAML for machine parsing:

```yaml
conflicts:
  - id: "CONFLICT-{YYYYMMDD}-{SEQ}"
    title: "..."
    status: resolved
    severity: {level}
    method: {method}
    date: "{date}"
    approver: "{name}"
    requirements: ["{id}", "{id}"]
    resolution: "..."
    rationale: "..."
    residual_risk:
      - risk: "..."
        severity: {level}
        mitigation: "..."
    affected_prds: [...]
    knowledge_entries: [...]
```
```

---

## Resolution Decision Tree

```
Conflict Detected
      |
      v
+------------------+
| Is it safety/    |--Yes--> Risk-Based Resolution
| security related?|
+------------------+
      | No
      v
+------------------+
| Are risk levels  |--Yes--> Risk-Based Resolution
| significantly    |
| different?       |
+------------------+
      | No
      v
+------------------+
| Is it a clear    |--Yes--> AHP-TOPSIS Weighted
| multi-criteria   |
| trade-off?       |
+------------------+
      | No
      v
+------------------+
| Is it a complex  |--Yes--> IBIS Argumentation
| "wicked problem" |
| with many views? |
+------------------+
      | No
      v
+------------------+
| Stakeholder      |--------> Negotiation Protocol
| disagreement?    |
+------------------+
```

---

## Key Requirements to Verify

Before submitting your resolution output, re-read and verify:
1. Every conflict has both requirements fully quoted with their IDs
2. The resolution method matches the decision tree (risk-based for safety, AHP-TOPSIS for trade-offs)
3. Residual risk is documented with specific mitigation strategies
4. Stakeholder impact is assessed for each proposed resolution

Re-read the output format for your chosen resolution method and confirm all required fields are present.

---

## Related Prompts

- [Quality Validation](quality-validation.md) - Detect conflicts during validation
- [PRD Creation](prd-creation.md) - Avoid creating conflicts
- [Requirements Elicitation](requirements-elicitation.md) - Identify conflicts early

---

*Conflict Resolution Prompts v1.0.0*
*AARE-F Framework v1.1.0 Implementation*
