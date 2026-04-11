---
name: trw-prd-groom
description: >
  Internal phase: Groom a PRD to sprint-ready quality. Researches codebase,
  drafts missing sections, iterates until validation passes.
  Called automatically by /trw-prd-ready and /trw-prd-new. Not intended for direct user invocation.
user-invocable: false
argument-hint: "[PRD-ID or file path]"
---
<!-- ultrathink -->

# PRD Grooming Skill

Groom a PRD to sprint-ready quality (total_score >= 65, REVIEW tier) through
systematic research and evidence-based drafting.

**Important:** a higher score is only useful when it comes from stronger
execution evidence. Treat content density as hygiene; prioritize
implementation-readiness and traceability first.

## Workflow

1. **Resolve PRD path**: Find the PRD file from `$ARGUMENTS[0]`:
   - If a file path, use directly
   - If a PRD ID (e.g., `PRD-CORE-020`), read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) and search in that directory and its sibling `archive/prds/`

2. **Read and baseline**: Read the PRD file completely. Call `trw_prd_validate(prd_path)` for baseline quality score.

3. **Early exit**: If total_score >= 65 (REVIEW tier), report "PRD already sprint-ready" and exit.

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
    - **Technical Approach**: Architecture decisions with rationale, reference existing patterns, and identify primary control points
    - **Test Strategy**: Map to requirements, specify unit/integration/e2e split, and make proof tests explicit
    - **Rollout Plan**: Phased with rollback criteria, release gates, and completion evidence
    - **Success Metrics**: Quantitative with baselines and targets
    - **Dependencies & Risks**: Concrete risks with likelihood/impact + mitigation
    - **Open Questions**: Unresolved items needing stakeholder input
    - **Traceability Matrix**: Map requirements to test cases and source files

6. **Load-bearing evidence checklist** — before optimizing prose, check whether
   the PRD contains the execution details implementers and reviewers actually
   need:
   - primary control points or decision surfaces
   - behavior switch matrix rows for meaningful requirement changes
   - key files / implementation surfaces
   - proof-oriented tests and verification commands
   - migration, rollback, or backward-compatibility handling where relevant
   - completion evidence that defines what "implemented" and "done" mean

7. **Validation loop** (max 3 iterations):
    a. Write updated PRD
    b. Call `trw_prd_validate(prd_path)` to check quality
    c. If quality gates pass, exit with success
    d. If < 5% score improvement after 3 iterations, exit (convergence)
    e. Parse validation failures and draft fixes
    f. When multiple dimensions are weak, improve them in this order:
       `implementation_readiness` -> `traceability` -> `structural_completeness` -> `content_density`

8. **Completion**: Call `trw_learn(summary="PRD groomed: {PRD-ID} to {score}", tags=["prd-workflow"])`. Report final quality score and any remaining gaps.

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The PRD is good enough at 55, close enough to 65" | The total_score >= 65 (REVIEW tier) threshold exists because lower scores correlate with implementation gaps | PRDs below REVIEW tier have 2x more "Open Questions" that become P0 blockers during implementation |
| "I can fabricate this requirement to fill the gap" | Fabricated requirements create false confidence and wrong implementations | Agents implement the fabricated requirement faithfully — wrong code that passes all tests |
| "The traceability matrix can be filled in later" | Traceability is how the lead validates FR coverage during REVIEW | Missing traceability means the lead can't verify implementation — delays delivery by a full review cycle |
| "The validator asked for more content, so more paragraphs must be the answer" | Density is a hygiene signal, not the goal | You can raise the score while keeping the PRD hard to implement |
| "A score bump proves the PRD is now executable" | Score movement is only meaningful when control points, proof tests, and rollout evidence improved | Review churn stays high even though the document looks denser |

## Assertion Suggestions (PRD-CORE-086)

For FRs that reference code patterns, conventions, or structural requirements, suggest executable assertions:

- **Convention FRs** ("All routers must use X"): Suggest `grep_present` for the required pattern and `grep_absent` for the anti-pattern
- **Structure FRs** ("Test file must exist for each module"): Suggest `glob_exists` for the expected files
- **Migration FRs** ("Old pattern X removed"): Suggest `grep_absent` for the deprecated pattern
- **Do NOT suggest assertions for**: Behavioral FRs, performance FRs, UX requirements, or anything that can't be verified with grep/glob

Format assertions as:
```
**Assertions**:
- `grep_present: "APIRouter(prefix=" in "backend/app/routers/**/*.py"`
- `grep_absent: "APIRouter()" in "backend/app/routers/**/*.py"`
```

Only suggest assertions for ~30% of FRs — most requirements are not grep/glob-verifiable.

## Constraints

- NEVER fabricate requirements not grounded in Background or codebase evidence
- NEVER remove existing substantive content — additive changes only
- ALWAYS preserve PRD ID, frontmatter structure, and section numbering
- ALWAYS use EARS patterns for functional requirements
- ALWAYS include confidence scores on requirements
- ALWAYS improve control points, proof tests, key files, and rollback/completion
  semantics before expanding prose for density
- NEVER optimize for score-gaming; if a paragraph does not improve execution
  clarity, traceability, or proof quality, do not add it
- If hitting total_score >= 65 requires inventing ungrounded content, stop and document gaps in Open Questions
- If total_score remains below 45 (DRAFT tier) after 3 iterations, STOP and report to the caller — the feature description likely needs more detail from the user
