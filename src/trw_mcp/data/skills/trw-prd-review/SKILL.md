---
name: trw-prd-review
description: >
  Internal phase: Review a PRD for quality, returning a structured READY/NEEDS WORK/BLOCK
  verdict with per-dimension scores. Read-only — never modifies files.
  Called automatically by /trw-prd-ready and /trw-prd-new. Not intended for direct user invocation.
user-invocable: false
argument-hint: "[PRD-ID or file path]"
context: fork
agent: trw-requirement-reviewer
---
<!-- ultrathink -->

# PRD Review Skill

Performs a comprehensive quality review of a PRD using the trw-requirement-reviewer agent.

## How It Works

This skill forks execution to the `trw-requirement-reviewer` agent, which performs a read-only 5-dimension quality assessment:

1. **Structure** — AARE-F section completeness and formatting
2. **Content Quality** — substantive depth vs. placeholder content
3. **Requirements Quality** — EARS compliance, confidence scores, testability
4. **Evidence & Confidence** — source citations, confidence calibration
5. **Traceability** — bidirectional links to code and tests

## Input

Pass a PRD ID or file path as the argument:
- `/trw-prd-review PRD-CORE-020`
- `/trw-prd-review path/to/PRD-CORE-020.md`

To resolve a PRD ID to a file path, read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`).

## Output

The agent returns a structured review with:
- Per-dimension scores (0-100%)
- Overall verdict: **READY** / **NEEDS WORK** / **BLOCK**
- Specific findings with severity and recommendations
- Suggested next actions

## Execution Plan Readiness Advisory

When the verdict is **READY** and this skill was invoked standalone (not as part of `/trw-prd-ready` pipeline), include an advisory note:

> "This PRD is sprint-ready. Use `/trw-prd-ready {PRD-ID}` to generate an execution plan, or proceed directly to implementation."

When invoked as part of the `/trw-prd-ready` pipeline, return only the structured verdict and findings — omit the advisory.

## Notes

- This skill is read-only — it never modifies the PRD file
- Uses fork mode to keep the review output out of the main conversation context
- The trw-requirement-reviewer agent runs on the default model with project memory
