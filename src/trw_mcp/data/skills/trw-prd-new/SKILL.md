---
name: prd-new
description: >
  Create a new PRD from a feature description. Generates AARE-F
  skeleton, runs initial validation, reports quality score.
  Use: /prd-new "Add rate limiting to the API"
user-invocable: true
argument-hint: "[feature description]"
allowed-tools: Read, Grep, Glob, Write, Bash, mcp__trw__trw_recall, mcp__trw__trw_prd_create, mcp__trw__trw_prd_validate
---

# PRD Creation Skill

Create a new PRD from a feature description using the TRW framework's AARE-F requirements process.

## Workflow

1. **Search context**: Call `trw_recall` with keywords from `$ARGUMENTS` to find related learnings and prior work.

2. **Check existing PRDs**: Read `INDEX.md` in the PRD parent directory (read `prds_relative_path` from `.trw/config.yaml`, default: `docs/requirements-aare-f/prds`) to verify no duplicate PRD exists for this feature. Check the draft PRD list and recently completed PRDs.

3. **Generate PRD**: Call `trw_prd_create(input_text="$ARGUMENTS")` to generate an AARE-F-compliant PRD skeleton. The tool auto-increments the sequence number per category.

4. **Read generated PRD**: Read the generated PRD file to confirm it was created successfully.

5. **Validate**: Call `trw_prd_validate(prd_path)` to get the initial quality score. New PRDs typically score as SKELETON or DRAFT tier.

6. **Report**: Output a summary:
   - PRD ID and file path
   - Quality score and tier
   - Sections that need work
   - Suggested next step: `/prd-groom {PRD-ID}` to bring it to sprint-ready quality, or `/prd-review {PRD-ID}` for a quality assessment

## Notes

- Default category is CORE. If the feature is clearly a bug fix, use FIX; if infrastructure, use INFRA; if quality, use QUAL.
- The generated PRD is a skeleton — it needs grooming (`/prd-groom`) to reach sprint-ready quality (>= 0.85 completeness).
