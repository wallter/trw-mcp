---
name: trw-prd-review
description: >
  Review a PRD for quality, returning a structured READY/NEEDS WORK/BLOCK
  verdict with per-dimension scores. Read-only — never modifies files.
  Use: /prd-review PRD-CORE-020
user-invocable: true
argument-hint: "[PRD-ID or file path]"
context: fork
agent: trw-requirement-reviewer
---

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
- `/prd-review PRD-CORE-020`
- `/prd-review path/to/PRD-CORE-020.md`

To resolve a PRD ID to a file path, read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`).

## Output

The agent returns a structured review with:
- Per-dimension scores (0-100%)
- Overall verdict: **READY** / **NEEDS WORK** / **BLOCK**
- Specific findings with severity and recommendations
- Suggested next actions

## Multi-PRD Batching

When reviewing multiple PRDs, batch them into parallel subagents — never 1 agent per PRD:

- **2-4 PRDs**: 2 parallel `trw-requirement-reviewer` agents, each reviewing 1-2 PRDs
- **5-8 PRDs**: 3 parallel agents, each reviewing 2-3 PRDs sequentially
- Launch all agents in ONE message for parallelism
- Each agent returns a structured verdict per PRD

## Notes

- This skill is read-only — it never modifies the PRD file
- Uses fork mode to keep the review output out of the main conversation context
- The trw-requirement-reviewer agent runs on the default model with project memory
