# TRW Loop — Ceremony-Aware Autonomous Maintenance

This file customizes the `/loop` command for TRW-protocol-aware autonomous sessions.
Every bare `/loop` wake-up MUST follow the steps below — do NOT use Claude Code's
generic maintenance behavior (tend PRs, run cleanup). The TRW ceremony protocol
governs all unattended work.

---

## Loop Cycle Protocol

### Step 1 — Session Recovery
Call `trw_session_start(query="loop cycle autonomous maintenance")` immediately.
This loads prior learnings and any active run state. Without it you have no
context from previous cycles and will repeat work or miss the highest-value runway.

If `trw_status()` shows an active run that was not delivered in the previous cycle,
pick it up and continue from the last checkpoint. Do not re-plan; resume.

### Step 2 — Identify the Highest-Value Runway
After `trw_session_start`, do ONE of the following in priority order:

1. **Resume active run** — if `trw_status()` shows an in-progress run with
   pending FRs, continue that run to completion before starting anything new.

2. **Select highest-value PRD** — scan `docs/requirements-aare-f/INDEX.md`
   (or the active ROADMAP) for the highest-priority unstarted PRD whose
   dependencies are met. Prefer P0 > P1 > P2.

3. **Bundle scope** — each cycle MUST ship at least 5 substantive PRDs or
   represent 20 minutes to 4 hours of real work. Refactors or single-flag
   changes do not count as material unless paired with 4+ code PRDs.

### Step 3 — Execute with Ceremony

For each PRD or work unit:

```
trw_init(task_name, prd_scope)
  → work
  → trw_checkpoint(message) after each milestone  ← MANDATORY, not optional
  → trw_learn(summary, detail) for any discovery or gotcha
  → run project-native validation, then trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope='<exact command>') before delivery
  → trw_deliver()
```

Phase gates apply: RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER.
Read `.trw/frameworks/FRAMEWORK-CORE.md` if context was compacted since last read.

### Step 4 — Checkpoint Cadence
Call `trw_checkpoint` at least once per 15 minutes of implementation work,
and always before switching to a new PRD. This protects against context
compaction destroying in-progress work.

### Step 5 — Schedule the Next Wake-Up
At the END of each cycle, after `trw_deliver()`, use `CronCreate` to schedule
the next wake-up at ≤60 seconds. Operator directive: never let the loop idle
for more than 60 seconds between cycles. Example:

```
/schedule "continue TRW loop cycle" --interval 60s
```

If `CronCreate` is unavailable, note the gap and continue — do not block on it.

---

## Hard Constraints (Never Skip)

- **Never skip `trw_session_start`** — without it you are flying blind.
- **Never skip `trw_deliver`** — undelivered learnings evaporate on compaction.
- **Never skip `trw_build_check`** before `trw_deliver` — the deliver gate blocks on this.
- **Never start a new PRD without delivering the previous one** — partial work compounds.
- **Read FRAMEWORK.md after every compaction** — the methodology is context, not instinct.

---

## Cycle Scope Rules (Operator Directives, Permanent)

- Minimum 5 substantive PRDs per cycle OR 20 minutes to 4 hours of real work.
- Single-flag changes, refactors alone, or doc-only cycles are NOT material.
- A closure PRD counts only when paired with 4+ code PRDs.
- Wake-up cadence: ≤60 seconds between cycles — use `CronCreate` to enforce it.
- Do not ask for permission between in-scope runways; self-identify the next
  highest-value item and continue.

---

## Recovery After Compaction

Context compaction may fire mid-cycle. After it completes:

1. The `PostCompact` hook fires and emits recovery context automatically.
2. `SessionStart` (compact matcher) also fires with TRW protocol guidance.
3. Read the RECOVERED state lines (run path, phase, last checkpoint).
4. Call `trw_session_start` again to reload learnings.
5. Call `trw_status()` to confirm current phase.
6. Resume from the last checkpoint — do not re-plan.

---

## Value Hierarchy (Never Deviate)

**Truthfulness > Quality > Knowledge > Velocity**

Ship — but not at the cost of verification, correctness, or preserved learnings.
When in doubt, call `trw_learn` first, then continue.
