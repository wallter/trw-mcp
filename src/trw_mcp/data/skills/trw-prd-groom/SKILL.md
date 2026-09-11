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

Groom a PRD using one selected readiness predicate throughout baseline, early exit,
iteration and completion. The **default target** requires full nonpartial validation,
`valid: true`, and risk-scaled `quality_tier: approved`. `total_score` is diagnostic;
never gate on deprecated `completeness_score`.

The **initial-authoring target** applies only when the embedded ready caller supplies
this invocation's successful creation result bound to the exact output path and
retained initial-authoring provenance. It requires full nonpartial validation and
`valid: true`, with no unresolved substantive blockers; tier is diagnostic, not an
approved-tier prerequisite. Research and author genuine grounded requirements before
review, not filler for scores. Draft status, missing plan, existing ID/path, and
failed/uncertain creation do not select this target or grant editing authority.

Initial-authoring scope also permits the exec-plan owner to draft the plan before review.
A deficient plan may return requirements here within that original scope; new user-scope
uncertainty stops rather than inventing intent.
For successfully created new input, the readiness caller permits up to two NEEDS WORK repair cycles
within the original user scope, directed by independent reviewer findings. Retain creation
provenance; it is not a one-use edit permission. Every revision invalidates old review
and requires full validation plus fresh author-independent review. New scope, BLOCK,
missing evidence, tool/review failure, or exhausted cycles stops. After READY, stop read-only.
Existing-input repair still requires separately authorized scope; without it, stop read-only.
This internal target is not a new public option, tool argument, code execution permission,
or review bypass.
Initial authoring preserves lifecycle/status and version unless separately authorized.

## Workflow

1. **Resolve PRD path**: Find the PRD file from `$ARGUMENTS[0]`:
   - If a file path, use directly
   - If a PRD ID (e.g., `PRD-CORE-020`), read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) and search in that directory and its sibling `archive/prds/`
   - If the caller supplies review findings as refinement context, retain them as targeted repair inputs; verify them
     against the PRD and repository evidence rather than accepting them blindly

2. **Read and baseline**: Read the PRD completely. Call full `trw_prd_validate(prd_path)` and retain
   `validation_partial`, `valid`, `quality_tier`, `total_score`, `sections_expected`, failures, and suggestions.

3. **Early exit**: Exit only when the selected readiness predicate passes.
   For invocation-created input, first substantively assess original intent coverage,
   acceptance criteria, non-goals, unresolved questions and verified implementation/proof
   seams even if the skeleton is already valid. No unresolved substantive blockers may
   remain; do not require pointless rewriting of adequate content.

4. **Research phase**:
   - Call `trw_recall` with keywords from the PRD Background section
   - Use Grep/Glob to find relevant codebase patterns, interfaces, seams, data structures, workflows, and test conventions
   - Read related PRDs referenced in `traceability.depends_on` and `traceability.enables`
   - Infer the project's language/framework/test runner from nearby config and existing files; do not assume Python unless the PRD is actually Python-focused
   - Identify the smallest vertical tracer-bullet path that can prove the feature end-to-end

5. **Drafting phase** — repair only weak or missing entries from the validator's category-specific `sections_expected`:
   - On a review loop-back, address the supplied refinement findings or explain with evidence why a finding is invalid.
   - Ground claims, paths, interfaces, dependencies, and risks in repository or explicit product evidence.
   - Write testable FRs/NFRs with confidence and requirement-appropriate patterns; use EARS only when it improves clarity.
   - Map requirements to implementation seams and project-native verification, including a vertical proof slice when feasible.
   - Preserve preflight decisions and unresolved assumptions instead of smoothing them into prose.

6. **Validation loop** (max 3 iterations):
   a. Write updated PRD
   b. Call `trw_prd_validate(prd_path)` to check quality
   c. If the selected readiness predicate passes, exit with success
   d. For the default target, improvement below 5 `total_score` points stops for convergence.
      For initial authoring or authorized embedded repair, stop on unresolved substantive
      blockers or no evidence-backed progress toward validity, not five-point score movement.
   e. Parse validation failures and draft fixes within the authorized scope

7. **Completion**: Success requires the same selected readiness predicate; otherwise report blocked or exhausted, not ready. Report the selected target, result fields and remaining gaps. Record the outcome in the run artifact. Use `trw_learn` only for a durable
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
- `grep_present: "APIRouter(prefix=" in "src/app/routers/**/*.py"`
- `grep_absent: "APIRouter()" in "src/app/routers/**/*.py"`
```

Suggest assertions for a minority of FRs only — most requirements are not grep/glob-verifiable.

## Constraints

- NEVER fabricate requirements not grounded in Background or codebase evidence
- Preserve substantive content, but correct or remove duplicated and disproven claims when evidence supports the change
- ALWAYS preserve PRD ID, frontmatter structure, and section numbering
- Use EARS patterns only where they improve requirement clarity
- ALWAYS include confidence scores on requirements
- ALWAYS preserve visible uncertainty from PRD drill/preflight decisions; ambiguous requirements belong in Open Questions, not hidden assumptions
- ALWAYS keep the PRD language-agnostic unless the feature itself is language-specific
- SHOULD prefer deep modules (small stable interfaces hiding complexity) and vertical tracer-bullet slices over broad horizontal layer plans
- If readiness would require inventing ungrounded content, stop and document gaps in Open Questions
- If the predicate still fails after 3 iterations or convergence, stop and report the result fields and blockers
