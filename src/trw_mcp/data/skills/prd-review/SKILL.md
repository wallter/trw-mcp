---
name: prd-review
description: >
  Review a PRD for quality, returning a structured READY/NEEDS WORK/BLOCK
  verdict with per-dimension scores. Read-only — never modifies files.
  Use: /prd-review PRD-CORE-020
user-invocable: true
argument-hint: "[PRD-ID or file path]"
context: fork
agent: requirement-reviewer
---

# PRD Review Skill

Performs a comprehensive quality review of a PRD using the requirement-reviewer agent.

## How It Works

This skill forks execution to the `requirement-reviewer` agent, which performs a read-only 5-dimension quality assessment:

1. **Structure** — AARE-F section completeness and formatting
2. **Content Quality** — substantive depth vs. placeholder content
3. **Requirements Quality** — EARS compliance, confidence scores, testability
4. **Evidence & Confidence** — source citations, confidence calibration
5. **Traceability** — bidirectional links to code and tests

## Input

Pass a PRD ID or file path as the argument:
- `/prd-review PRD-CORE-020`
- `/prd-review path/to/PRD-CORE-020.md`

To resolve a PRD ID to a file path, read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`).

## Output

The agent returns a structured review with:
- Per-dimension scores (0-100%)
- Overall verdict: **READY** / **NEEDS WORK** / **BLOCK**
- Specific findings with severity and recommendations
- Suggested next actions

## Execution Plan Readiness Advisory

When the verdict is **READY**, include an advisory note:

> "This PRD is sprint-ready. For P0/P1 PRDs, consider generating an execution plan via `/exec-plan {PRD-ID}` before implementation — this decomposes FRs into micro-tasks with file paths, test names, and verification commands."

## Notes

- This skill is read-only — it never modifies the PRD file
- Uses fork mode to keep the review output out of the main conversation context
- The requirement-reviewer agent runs on the default model with project memory
