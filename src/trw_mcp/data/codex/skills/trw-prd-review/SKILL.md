---
name: trw-prd-review
description: "Internal phase: Review a PRD for quality, returning a structured READY/NEEDS WORK/BLOCK verdict with per-dimension scores. Read-only — never modifies files. Called automatically by /trw-prd-ready and /trw-prd-new. Not intended for direct user invocation.\n"
---

> Codex-specific skill: this version is authored for Codex. Follow Codex-native skill and subagent flows, and ignore Claude-only references if any remain.

<!-- ultrathink -->

# PRD Review Skill

Performs a comprehensive read-only quality review of a PRD.

## How It Works

Use an independent reviewer only when the active client explicitly provides one. Otherwise perform a separate same-context
read-only pass and disclose that independent execution was unavailable. Assess five dimensions:

Use the caller's immediately preceding full `trw_prd_validate` result; if it is absent, run full validation before review.

1. **Structure** — AARE-F section completeness and formatting
2. **Content Quality** — substantive depth vs. placeholder content
3. **Requirements Quality** — EARS compliance, confidence scores, testability
4. **Evidence & Confidence** — source citations, confidence calibration
5. **Traceability** — bidirectional links to code and tests

## Input

The internal `/trw-prd-ready` or `/trw-prd-new` caller provides a PRD ID or file path. Treat it as the
review target; do not prompt for or advertise direct invocation.

To resolve a PRD ID to a file path, read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`).

## Output

Return a structured review with:
- Per-dimension scores (0-100%)
- Overall verdict: **READY** / **NEEDS WORK** / **BLOCK**
- Specific findings with severity and recommendations
- Suggested next actions

### Verdict contract

- **READY** requires `validation_partial: false`, `valid: true`, and risk-scaled `quality_tier: approved`. It also
  requires no unresolved blocking finding.
- **NEEDS WORK** means the PRD is reviewable but has bounded missing, ambiguous, untestable, or weakly evidenced content.
- **BLOCK** applies when the file is unreadable, validation is partial in a way that hides readiness, core scope or
  requirements are absent, evidence is fabricated, or a systemic issue prevents safe planning.

Scores are diagnostic only. Do not invent a percentage gate or let document length determine the verdict.

### Finding contract

For every finding include severity (`blocking | warning | suggestion`), section/line, violated rule, concrete impact,
smallest remediation or acceptance condition, evidence checked, and uncertainty.

## Notes

- This skill is read-only — it never modifies the PRD file
- Never claim independent or forked review when the active client did not provide it
