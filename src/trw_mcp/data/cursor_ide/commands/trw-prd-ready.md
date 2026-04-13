# /trw-prd-ready

Create or groom a PRD to sprint-ready quality (draft → groom → review → exec plan).

## When to use

- When you have a rough idea that needs to become a formal requirement.
- When a PRD is in DRAFT state and needs to be groomed to READY.
- When a backlog item needs a full exec plan before sprint assignment.
- Before scheduling a PRD for implementation.

## What it does

Invokes the TRW `trw-prd-ready` skill via the MCP server:

1. Reads the target PRD (or creates a new one from your description).
2. Fills in missing sections: problem statement, FRs, non-FRs, test plan.
3. Validates quality gates (no vague FRs, no missing acceptance criteria).
4. Produces or updates the execution plan.
5. Sets status to READY when all gates pass.

## Usage

Type `/trw-prd-ready` followed by the PRD ID or a brief description of the
feature, e.g.:

```
/trw-prd-ready PRD-CORE-137
/trw-prd-ready Add rate limiting to the MCP server
```
