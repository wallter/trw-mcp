---
name: prd-groom
description: >
  Groom a PRD to sprint-ready quality. Researches codebase,
  drafts missing sections, iterates until validation passes.
  Use: /prd-groom PRD-CORE-020
user-invocable: true
argument-hint: "[PRD-ID or file path]"
allowed-tools: Read, Grep, Glob, Edit, Write, WebSearch, Bash, mcp__trw__trw_recall, mcp__trw__trw_prd_validate, mcp__trw__trw_learn
---

# PRD Grooming Skill

Groom a PRD to sprint-ready quality (>= 0.85 completeness) through systematic research and evidence-based drafting.

## Workflow

1. **Resolve PRD path**: Find the PRD file from `$ARGUMENTS[0]`:
   - If a file path, use directly
   - If a PRD ID (e.g., `PRD-CORE-020`), read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) and search in that directory and its sibling `archive/prds/`

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

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The PRD is good enough at 0.75, close enough to 0.85" | The 0.85 threshold exists because lower scores correlate with implementation gaps | PRDs below 0.85 have 2x more "Open Questions" that become P0 blockers during implementation |
| "I can fabricate this requirement to fill the gap" | Fabricated requirements create false confidence and wrong implementations | Agents implement the fabricated requirement faithfully — wrong code that passes all tests |
| "The traceability matrix can be filled in later" | Traceability is how the lead validates FR coverage during REVIEW | Missing traceability means the lead can't verify implementation — delays delivery by a full review cycle |

## Constraints

- NEVER fabricate requirements not grounded in Background or codebase evidence
- NEVER remove existing substantive content — additive changes only
- ALWAYS preserve PRD ID, frontmatter structure, and section numbering
- ALWAYS use EARS patterns for functional requirements
- ALWAYS include confidence scores on requirements
- If hitting 0.85 requires inventing ungrounded content, stop and document gaps in Open Questions
