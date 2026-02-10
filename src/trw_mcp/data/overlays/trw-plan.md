## PLAN PHASE OVERLAY (v18.1_TRW)

This overlay augments the shared core with planning-specific content.

---

### FORMATIONS

ORC selects formation per wave using the tree below. Inputs: wave purpose, shard count, prior wave confidence.

```
Parallelizable without coordination?
├─ YES → MAP-REDUCE (shards: ceil(subtasks/3))
└─ NO → Single synthesis from diverse inputs?
        ├─ YES → PLANNER→EXECUTOR→REFLECTOR (3 shards)
        └─ NO → Quality critical?
                ├─ YES → DEBATE+CRITIC+JUDGE (4 shards)
                └─ NO → PIPELINE (min(3, stages))
```

Formations are wave-scoped; each wave selects its own formation. WHY: Wave boundaries are the checkpointing unit; cross-wave formations break resume semantics.

---

### Planning Shards

Planning shards write to `scratch/shard-{id}/plan_fragment.yaml` (same structure as findings, `phase: plan`).

ORC synthesizes plan fragments into `reports/plan.md`. Plan MUST include:
1. Acceptance criteria derived from requirements
2. Shard cards with output contracts
3. Wave manifest (`shards/wave_manifest.yaml`)
4. Risk register draft (`validation/risk-register.yaml`)

---

### ADAPTIVE PLANNING

`reports/plan.md` is a living document. Update when new info invalidates assumptions, scope changes >20%, approach fails, or user feedback.

| Trigger | Action |
|---------|--------|
| Blocker | STOP → update plan → may revert to PLAN |
| Scope +20% | Pause → update → confirm with user |
| Failure | Document → plan alternative |

<phase_revisiting>
```
IMPLEMENT → (blocker) → PLAN → IMPLEMENT
VALIDATE → (flaw) → PLAN → IMPLEMENT → VALIDATE
```
</phase_revisiting>

When updating plan: add `## Revision [N]`, document change/why/impact, log to events.jsonl.

---

### REQUIREMENTS (Pre-Development)

<pre_development>
Before IMPLEMENT, verify:
1. Source identified (PRD, issue, request)
2. Acceptance criteria in `plan.md`
3. Each REQ has: ID, criterion, verification method
</pre_development>

### AARE-F Tools

When `MCP_MODE: tool` and AARE-F framework file exists: `trw_prd_create` at RESEARCH/PLAN, `trw_prd_validate` (MUST pass) pre-IMPLEMENT, `trw_traceability_check` at VALIDATE/DELIVER.

---

### PSR at PLAN Start

| Phase | Inputs | Outputs |
|-------|--------|---------|
| PLAN start | Objective, prior knowledge | Assumptions → `trw_learn` entries |
