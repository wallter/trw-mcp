## IMPLEMENT PHASE OVERLAY (v18.1_TRW)

This overlay augments the shared core with implementation-specific content.

---

### WAVE ORCHESTRATION

Waves sequence groups of parallel shards with inter-wave data flow. Each wave completes before the next begins, enabling dependent shards to consume outputs from prior waves.

#### Wave Manifest

`shards/wave_manifest.yaml` — each entry: `wave` (1-based), `shards` (IDs), `status` (pending|active|complete|failed|partial), `depends_on` (prior wave numbers).

#### Execution Rules

| Rule | Description |
|------|-------------|
| Parallel within wave | All shards in a wave launch as blocking Task() calls in a single message |
| Sequential between waves | Wave N+1 starts only after wave N status = `complete` |
| Fail-fast | If any shard in a wave fails, ORC MUST pause and replan before advancing |
| Manifest update | ORC MUST update `wave_manifest.yaml` status after each wave completes |
| Post-wave validation | When `MCP_MODE: tool`, call `trw_wave_validate(wave_number)` after each wave |
| Progress tracking | ORC SHOULD call `trw_status()` for wave progress overview |

#### Blackboard (Inter-Shard Coordination)

```yaml
# scratch/_blackboard/{formation}.yaml
entries:
  - ts: "2026-01-25T12:00:01Z"
    shard: shard-001
    key: finding_001
    value: {summary: "...", confidence: high}
```
Append-only. Lock via `meta/locks.yaml`. Archive on completion. ORC MAY use per-wave blackboards (`scratch/_blackboard/wave-{N}/`).

#### Replanning Triggers

| Trigger | Action |
|---------|--------|
| Shard failure in wave | Pause → assess → replan remaining waves |
| New dependency discovered | Insert new wave or merge into existing |
| Scope reduction | Remove unnecessary waves, update manifest |
| All shards independent | Collapse to single wave (see shortcut below) |

Wave replanning is tactical. For strategic plan changes (scope >20%, blockers), see ADAPTIVE PLANNING.

#### Resume Protocol

On resume, `trw_resume()` (or manual scan) classifies shards as complete/partial/failed/not_started. Launch only incomplete shards as parallel blocking Task() calls. Session break loses at most in-flight shards, never completed work.

#### Single-Wave Shortcut

When all shards are independent, ORC MAY omit `wave_manifest.yaml` and launch all shards directly.

---

### OUTPUT CONTRACTS

Every shard declares what it will produce (`output_contract`: `file`, `schema` with `keys`/`required`, `optional_keys`). ORC validates after each wave.

#### Dependency Graph

```
Wave 1: shard-001 → result.yaml ─┐
         shard-002 → result.yaml ─┼─→ Wave 2: shard-004 (input_refs: [shard-001, shard-002])
         shard-003 → result.yaml ─┘
                                       Wave 2: shard-005 (input_refs: [shard-003])
```

#### Validation Rules

| Rule | Description |
|------|-------------|
| Post-wave check | ORC MUST verify each shard's output file exists and contains required keys |
| Missing output | Block next wave, log failure, trigger replan |
| Schema mismatch | Warn + log; proceed only if optional keys missing |
| Contract immutability | Once a wave starts, its shards' contracts MUST NOT change. WHY: Downstream shards in later waves depend on declared contracts; changing contracts mid-wave invalidates dependency assumptions. |

---

### SELF-DIRECTING SHARDS

Shards MAY self-decompose into child shards (bounded recursion). Eligibility — ALL must be true:
1. `self_decompose: true` in shard card
2. Current depth < `MAX_CHILD_DEPTH`
3. Task has ≥2 independent subtasks identifiable before execution
4. Parent shard can define output contracts for each child

#### Child Shard Rules

| Rule | Description |
|------|-------------|
| Blocking | Child shards launch as blocking Task() calls (inherits from PARALLELISM rules) |
| Formation | Parent selects formation for children (MAP-REDUCE typical) |
| Persistence | Children write to `scratch/shard-{parent}/children/shard-{child}/` |
| Depth tracking | Each child card includes `depth: {parent_depth + 1}` |
| Aggregation | Parent MUST aggregate child outputs into its own output contract |

Child manifest in `scratch/shard-{parent}/children/manifest.yaml`: `parent`, `depth`, `children` list (each with `id`, `depth`, `status`, `output_contract`).

#### Depth Limits

<depth_rules>
Depth 0 = ORC-spawned, 1 = child, 2 = grandchild. Hard ceiling: 3. WHY: Token budgets fragment below useful thresholds at depth >2; each level of recursion halves available context. Override per-shard via `max_child_depth` in card.

- At hard ceiling, shard MUST NOT self-decompose regardless of card settings
- Blocking Task() calls ensure parent waits for all children before writing its own output
- If any child fails, parent MUST handle (retry, replan, or fail with partial)
</depth_rules>

---

### Context Compaction Protocol

On context compact: (1) persist all state to `run.yaml` and critical files, (2) commit green state, (3) reload FRAMEWORK.md + CLAUDE.md, (4) resume from `wave_manifest.yaml`.
