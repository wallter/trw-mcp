---
name: trw-framework-check
context: fork
agent: Explore
description: >
  Check TRW framework compliance. Reports ceremony adherence,
  phase gate status, and active run health. Use when unsure if
  framework obligations are being met.
  Use: /trw-framework-check
user-invocable: true
---

# Framework Compliance Check Skill

Use when: a session has finished and you need to confirm phase gates, active run health, and ceremony adherence before closing.

Check the current state of TRW framework compliance including ceremony adherence, phase gate status, learning layer health, and active run state.

## Workflow

1. **Run status**: Call `trw_status()` to get current run state (phase, confidence, wave progress, or "no active run").

2. **Ceremony checklist** — verify each ceremony step:
   - Was `trw_session_start` called this session? (Check `.trw/context/` for recent session markers)
   - Are there checkpoints in the active run? (Read `checkpoints.jsonl` if run exists)
   - Is CLAUDE.md auto-section current? (Read CLAUDE.md, check `trw:start`/`trw:end` markers exist and have content)

3. **Learning layer health**:
   - Call `trw_recall('*', compact=true)` for active learning count
   - Check if learning count is in healthy range (20-40 active)
   - Verify index.yaml `last_updated` is recent

4. **Framework version check**:
   - Read `.trw/frameworks/FRAMEWORK.md` for version string
   - Compare with the version shown in `trw_status()` output
   - Flag if versions differ (deployed copy may be out of date)

5. **PRD catalogue check**:
   - Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) and search for `INDEX.md` in its parent directory
   - If found, read PRD status counts
   - Flag any PRDs stuck in `review` or `approved` status (should progress to implementation)

6. **Report**: Structured compliance checklist:

   ```
   ## Framework Compliance Report

   ### Ceremony Status
   - [x/!] Session start ceremony
   - [x/!] Active checkpoints
   - [x/!] CLAUDE.md auto-section

   ### Run Status
   - Phase: {current phase or "no run"}
   - Confidence: {score}

   ### Learning Layer
   - Active: {N} entries (healthy: 20-40)
   - Last updated: {date}

   ### Framework Version
   - FRAMEWORK.md: {version}
   - Bundled copy: {version}
   - Status: {synced/out-of-date}

   ### PRD Status
   - {N} draft, {N} review, {N} approved, {N} done
   - Warnings: {any stuck PRDs}

   ### Recommendations
   - {actionable next steps}
   ```

## Notes

- This skill is read-only — it reports status but does not fix issues
- Use at the start of a session to verify framework health
- If issues are found, the recommendations will point to the right tools/skills to fix them
