---
name: prd-groom
description: >
  Groom a PRD to sprint-ready quality. Researches codebase,
  drafts missing sections, iterates until validation passes.
  Use: /prd-groom PRD-CORE-020
user-invocable: true
argument-hint: "[PRD-ID or file path]"
allowed-tools: Read, Grep, Glob, Edit, Write, WebSearch, Bash
---

# PRD Grooming Skill

Groom a PRD to sprint-ready quality (>= 0.85 completeness) through systematic research and evidence-based drafting.

## Workflow

1. **Resolve PRD path**: Find the PRD file from `$ARGUMENTS[0]`:
   - If a file path, use directly
   - If a PRD ID (e.g., `PRD-CORE-020`), search in `docs/requirements-aare-f/prds/` and `docs/requirements-aare-f/archive/prds/`

2. **Read and baseline**: Read the PRD file completely. Call `trw_prd_validate(prd_path)` for baseline quality score.

3. **Early exit**: If score >= 85% completeness, report "PRD already sprint-ready" and exit.

4. **Research phase**:
   - Call `trw_recall` with keywords from the PRD Background section
   - Use Grep/Glob to find relevant codebase patterns, interfaces, and data structures
   - Read related PRDs referenced in `traceability.depends_on` and `traceability.enables`

5. **Drafting phase** — for each weak/missing section, follow AARE-F 12-section guidance:
   - **Problem Statement**: Root cause + measurable impact + affected stakeholders
   - **Goals & Non-Goals**: Measurable outcomes; non-goals prevent scope creep
   - **User Stories**: As a [role], I want [capability], so that [benefit] + Given/When/Then
   - **Functional Requirements**: EARS patterns (When/While/If/Where) + confidence scores
   - **Non-Functional Requirements**: Quantitative thresholds with units
   - **Technical Approach**: Architecture decisions with rationale, reference existing patterns
   - **Test Strategy**: Map to requirements, specify unit/integration/e2e split
   - **Rollout Plan**: Phased with rollback criteria
   - **Success Metrics**: Quantitative with baselines and targets
   - **Dependencies & Risks**: Concrete risks with likelihood/impact + mitigation
   - **Open Questions**: Unresolved items needing stakeholder input
   - **Traceability Matrix**: Map requirements to test cases and source files

6. **Validation loop** (max 3 iterations):
   a. Write updated PRD
   b. Call `trw_prd_validate(prd_path)` to check quality
   c. If quality gates pass, exit with success
   d. If < 5% score improvement after 3 iterations, exit (convergence)
   e. Parse validation failures and draft fixes

7. **Completion**: Call `trw_learn(summary="PRD groomed: {PRD-ID} to {score}", tags=["prd-workflow"])`. Report final quality score and any remaining gaps.

## Constraints

- NEVER fabricate requirements not grounded in Background or codebase evidence
- NEVER remove existing substantive content — additive changes only
- ALWAYS preserve PRD ID, frontmatter structure, and section numbering
- ALWAYS use EARS patterns for functional requirements
- ALWAYS include confidence scores on requirements
- If hitting 0.85 requires inventing ungrounded content, stop and document gaps in Open Questions
