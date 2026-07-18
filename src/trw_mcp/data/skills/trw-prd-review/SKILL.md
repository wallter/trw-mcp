---
name: trw-prd-review
description: >-
  Assess PRD quality and output a structured READY/NEEDS WORK/BLOCK verdict with per-dimension scores. The skill is read-only and never modifies files. Trigger only via automatic invocation from /trw-prd-ready or /trw-prd-new; do not invoke directly.
user-invocable: false
argument-hint: "[PRD-ID or file path]"
context: fork
agent: trw-requirement-reviewer
---
<!-- ultrathink -->

# PRD Review Skill

Performs a comprehensive quality review of a PRD using the
trw-requirement-reviewer agent.

## How It Works

This skill forks execution to the `trw-requirement-reviewer` agent, which
performs a read-only quality assessment:

1. **Structure** — AARE-F section completeness and formatting
2. **Content Quality** — substantive depth vs. placeholder content
3. **Requirements Quality** — EARS compliance, confidence scores, testability,
   and execution clarity
4. **Evidence & Confidence** — source citations, confidence calibration
5. **Traceability** — bidirectional links to code and tests

## High-Signal Review Focus

The reviewer should treat these as load-bearing signals when deciding whether a
PRD is truly READY:

- primary control points and implementation surfaces
- behavior switch matrix coverage for requirement changes
- key files that anchor the expected code paths
- proof-oriented tests and verification commands
- rollout, rollback, migration, and completion evidence

High content density without the items above is **not** sprint-readiness. If the
document looks polished but still lacks executable evidence, the verdict should
remain **NEEDS WORK**. Review output should prefer missing proof or missing
traceability over generic requests to add more prose.

## Input

The internal `/trw-prd-ready` or `/trw-prd-new` caller provides a PRD ID or file path. Treat it as the
review target; do not prompt for or advertise direct invocation.

To resolve a PRD ID to a file path, read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`).

## Output

The agent returns a structured review with:
- Per-dimension scores (0-100%)
- Overall verdict: **READY** / **NEEDS WORK** / **BLOCK**
- Specific findings with severity and recommendations
- Suggested next actions

## Notes

- This skill is read-only — it never modifies the PRD file
- Uses fork mode to keep the review output out of the main conversation context
- The trw-requirement-reviewer agent runs on the default model with project memory
- Treat score-gaming as a failure mode: denser prose is only useful when it
  improves implementability or evidence quality
