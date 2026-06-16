---
name: trw-prd-groom
description: >-
  Refine Product Requirement Documents (PRDs) to sprint-ready quality by researching the codebase, drafting missing sections, and iterating until validation passes. This skill is triggered exclusively by the /trw-prd-ready or /trw-prd-new commands. Do not invoke directly by users.
user-invocable: false
argument-hint: "[PRD-ID or file path]"
---

> Portable adapter note: use the active client instruction file and available MCP tools. If a step mentions a client-specific workflow, use the equivalent tool/manual flow for the current harness.
<!-- ultrathink -->

# PRD Grooming Skill

Groom a PRD to sprint-ready quality (total_score >= 65, REVIEW tier) through systematic research and evidence-based drafting.

## Workflow

1. **Resolve PRD path**: Find the PRD file from `$ARGUMENTS[0]`:
   - If a file path, use directly
   - If a PRD ID (e.g., `PRD-CORE-020`), read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) and search in that directory and its sibling `archive/prds/`

2. **Read and baseline**: Read the PRD file completely. Call `trw_prd_validate(prd_path)` for baseline quality score.

3. **Early exit**: If total_score >= 65 (REVIEW tier), report "PRD already sprint-ready" and exit.

4. **Research phase**:
   - Call `trw_recall` with keywords from the PRD Background section
   - Use Grep/Glob to find relevant codebase patterns, interfaces, seams, data structures, workflows, and test conventions
   - Read related PRDs referenced in `traceability.depends_on` and `traceability.enables`
   - Infer the project's language/framework/test runner from nearby config and existing files; do not assume Python unless the PRD is actually Python-focused
   - Identify the smallest vertical tracer-bullet path that can prove the feature end-to-end

5. **Drafting phase** — for each weak/missing section, follow AARE-F 12-section guidance:
   - **Problem Statement**: Root cause + measurable impact + affected stakeholders
   - **Goals & Non-Goals**: Measurable outcomes; non-goals prevent scope creep
   - **User Stories**: As a [role], I want [capability], so that [benefit] + Given/When/Then
   - **Functional Requirements**: EARS patterns (When/While/If/Where) + confidence scores
   - **Non-Functional Requirements**: Quantitative thresholds with units
   - **Technical Approach**: Architecture decisions with rationale, affected modules/interfaces/seams, deep-module opportunities, and references to existing patterns
   - **Test Strategy**: Map to requirements, specify the project-appropriate test framework/commands, and include vertical-slice coverage in addition to unit/integration/e2e tiers
   - **Rollout Plan**: Phased with rollback criteria
   - **Success Metrics**: Quantitative with baselines and targets
   - **Dependencies & Risks**: Concrete risks with likelihood/impact + mitigation
   - **Open Questions**: Unresolved items needing stakeholder input
   - **Traceability Matrix**: Map requirements to test cases and source files
   - **Decision Tree / Assumptions**: If upstream creation included a drill preflight, preserve resolved decisions and unresolved assumptions instead of smoothing them into prose

6. **Validation loop** (max 3 iterations):
   a. Write updated PRD
   b. Call `trw_prd_validate(prd_path)` to check quality
   c. If quality gates pass, exit with success
   d. If < 5% score improvement after 3 iterations, exit (convergence)
   e. Parse validation failures and draft fixes

7. **Completion**: Report final quality score and any remaining gaps. Use `trw_learn` only for a durable
   requirements-pattern discovery, not for routine "PRD groomed" status.

## Style Guidance

- Use tables when they improve side-by-side comparison (FRs, NFRs, ACs, risk matrices, traceability).
- Use bullets or prose for rationale, uncertainty, and narrative flow; do not convert everything into tables just
  to chase a validator score.
- Preserve meaningful ambiguity as Open Questions instead of padding sections with low-signal rows.

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The PRD is good enough at 55, close enough to 65" | The total_score >= 65 (REVIEW tier) threshold exists because lower scores correlate with implementation gaps | PRDs below REVIEW tier have 2x more "Open Questions" that become P0 blockers during implementation |
| "I can fabricate this requirement to fill the gap" | Fabricated requirements create false confidence and wrong implementations | Agents implement the fabricated requirement faithfully — wrong code that passes all tests |
| "The traceability matrix can be filled in later" | Traceability is how the lead validates FR coverage during REVIEW | Missing traceability means the lead can't verify implementation — delays delivery by a full review cycle |

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
- ALWAYS preserve visible uncertainty from PRD drill/preflight decisions; ambiguous requirements belong in Open Questions, not hidden assumptions
- ALWAYS keep the PRD language-agnostic unless the feature itself is language-specific
- SHOULD prefer deep modules (small stable interfaces hiding complexity) and vertical tracer-bullet slices over broad horizontal layer plans
- If hitting total_score >= 65 requires inventing ungrounded content, stop and document gaps in Open Questions
- If total_score remains below 45 (DRAFT tier) after 3 iterations, STOP and report to the caller — the feature description likely needs more detail from the user
