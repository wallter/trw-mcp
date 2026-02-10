## RESEARCH PHASE OVERLAY (v18.1_TRW)

This overlay augments the shared core with research-specific content.

---

### Dynamic Research (Research Reactor)

After each RESEARCH wave, ORC evaluates findings and MAY spawn follow-up waves:

| Condition | Action | Cap |
|-----------|--------|-----|
| >30% findings have `open_questions` | Spawn follow-up research wave targeting open questions | MAX_RESEARCH_WAVES |
| Findings contradict each other | Spawn reconciliation wave with DEBATE formation | MAX_RESEARCH_WAVES |
| All findings `confidence: high` AND no open questions | Advance to PLAN | — |
| MAX_RESEARCH_WAVES reached | Advance to PLAN with documented uncertainty | — |

ORC classifies open questions as: `answered_elsewhere` (skip), `needs_investigation` (shard), `deferred` (log).
Shards MAY flag discoveries that contradict prior assumptions via blackboard entry with key `emergent_axis`. ORC SHOULD spawn a targeted follow-up wave for each emergent axis.

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

```yaml
# meta/formation_manifest.yaml
formation:
  name: research-map-reduce
  type: map-reduce
  status: active
  shards: [shard-001, shard-002, shard-003]
  fallback: pipeline
```

Formations are wave-scoped; each wave selects its own formation. WHY: Wave boundaries are the checkpointing unit; cross-wave formations break resume semantics.

---

### Parallel Exploration

When entering RESEARCH, ORC:
1. Identifies independent exploration axes (codebase areas, questions, evidence paths)
2. Launches them as parallel blocking Task() calls in a single message
3. Each shard writes its findings to disk before returning

Example: 3 axes (auth, database, API) → 3 parallel shards → each writes `scratch/shard-{id}/findings.yaml` → ORC synthesizes into `plan.md`.

Shard count: `clamp(MIN_SHARDS_FLOOR, axes_of_inquiry, PARALLELISM_MAX)`

### Persisted Findings Format

Every exploration or planning shard MUST write a findings file before returning. WHY: This is the persistence contract that enables resume safety.

```yaml
# scratch/shard-{id}/findings.yaml — RESEARCH/PLAN format. IMPLEMENT uses output_contract.
shard_id: shard-explore-auth
phase: research            # research | plan
status: complete           # complete | partial | failed
summary: "One-line summary"
findings:
  - key: "auth_mechanism"
    detail: "JWT with refresh tokens in src/auth/jwt.py"
    evidence: ["src/auth/jwt.py:45"]
    confidence: high       # high | medium | low
open_questions: ["How are tokens revoked?"]
files_examined: ["src/auth/**"]
```

<exploration_rules>
- Shards MUST write `findings.yaml` as their last action before returning. WHY: Writing findings last ensures the file reflects complete shard output; early writes create incomplete files that confuse resume logic.
- Partial results MUST be written with `status: partial` if shard hits an error or timeout
- ORC reads findings from disk for resume safety; Task() return text is supplementary. WHY: Disk state survives session breaks; return text does not.
- Findings with `status: partial` SHOULD be re-run with narrowed scope
- Planning shards write to `scratch/shard-{id}/plan_fragment.yaml` (same structure, `phase: plan`)
</exploration_rules>
