# /trw-prd-ready

Create or groom a PRD to sprint-ready quality (draft → groom → review → exec plan).

## When to use

- When you have a rough idea that needs to become a formal requirement.
- When a draft PRD needs a verified readiness result before lifecycle approval.
- When a backlog item needs a full exec plan before sprint assignment.
- Before scheduling a PRD for implementation.

## What it does

Invokes the TRW `trw-prd-ready` skill via the MCP server:

1. Reads the target PRD (or creates a new one from your description).
2. Fills in missing sections: problem statement, FRs, non-FRs, test plan.
3. Requires full validation with `validation_partial: false`, `valid: true`, and risk-scaled
   `quality_tier: approved`; `total_score` is reported, not used as a fixed threshold.
4. Produces or updates the execution plan.
5. Reports readiness without inventing a `READY` lifecycle status; lifecycle transitions remain explicit and separate.

## Usage

Type `/trw-prd-ready` followed by the PRD ID or a brief description of the
feature, e.g.:

```
/trw-prd-ready PRD-CORE-137
/trw-prd-ready Add rate limiting to the MCP server
```
