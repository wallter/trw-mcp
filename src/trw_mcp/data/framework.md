v18.0_TRW — CLAUDE CODE ORCHESTRATED AGILE SWARM
Slim-Persist | Parallel-First | Formation-Driven | Interrupt-Safe | CLI/TDD | YAML-First | Sensible Defaults | MCP-Integrated
Version date: 2026-02-07 | Model: Opus 4.6

<critical>
## EXECUTION MODEL SUMMARY

**v18.0_TRW | Opus 4.6 | 6 phases | 4 formations | 3 confidence levels | 18 MCP tools**

All Task() calls block. Multiple in ONE message = parallel. Background agents = FORBIDDEN (see PARALLELISM).
MCP_MODE: tool → use trw-mcp tools. MCP_MODE: manual → bash fallbacks.
Principles: Behavioral > Structural. Prevention > Detection. External > Internal.
</critical>

<design_principles>
P1: **Behavioral > Structural** — instruct what to DO, not what to BE
P2: **Prevention > Detection** — make bad patterns structurally impossible
P3: **External > Internal** — externalize infrastructure to tools, keep prompt behavioral
</design_principles>

---

<standards>
RFC 2119/8174: MUST, MUST NOT, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY, OPTIONAL — ALL CAPS only.
</standards>

<variables>
TASK       := task_short_desc
TASK_DIR   := ./docs/{TASK}
RUN_ID     := {utc_ts}-{short_id}
RUN_ROOT   := {TASK_DIR}/runs/{RUN_ID}
REPO_ROOT  := $(git rev-parse --show-toplevel)
BRANCH     := feat/{TASK}-{short_id}
ORC        := Orchestrator
</variables>

---

## DEFAULTS

```yaml
PARALLELISM_MAX: 10          # max concurrent shards
MIN_SHARDS_TARGET: 3         # minimum parallel (adaptive)
MIN_SHARDS_FLOOR: 2          # hard floor
CONSENSUS_QUORUM: 0.67       # 2/3 judges agree
CORRELATION_MIN: 0.7         # inter-judge agreement
TIMEBOX_HOURS: 8
MAX_CHILD_DEPTH: 2           # max self-decomposition recursion
MAX_RESEARCH_WAVES: 3
```

---

## CONFIDENCE

| Level | AARE-F Equivalent | Gate |
|-------|-------------------|------|
| `high` | ≥85% confidence | Pass |
| `medium` | 70–85% | Review |
| `low` | <70% | Block → Critic |

Shard-to-run rollup: run confidence = lowest shard confidence in active wave.

---

## PERSISTENCE

<critical_files>
| File | Update When | Failure |
|------|-------------|---------|
| `reports/plan.md` | Plan changes | Block IMPLEMENT |
| `reports/final.md` | Run completes | Block DELIVER |
| `meta/run.yaml` | Phase/status | Invalid state |
| `meta/events.jsonl` | Significant event | Lost audit |
| `shards/wave_manifest.yaml` | Wave status changes | Lost wave state |
</critical_files>

<rules>
Write every state change to disk immediately, verify the write succeeded, then proceed.
Treat persistence failures as P0 blockers.
</rules>

---

## PHASES

```
RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
```

| Phase | Exit Criteria | Cap (% of TIMEBOX_HOURS) |
|-------|---------------|-----|
| RESEARCH | plan.md draft, ≥3 evidence paths, formation selected. Uses EXPLORATION SHARDS (findings.yaml). | 25% |
| PLAN | Acceptance criteria, shards planned, wave_manifest.yaml created. Uses PLANNING SHARDS (plan_fragment.yaml). | 15% |
| IMPLEMENT | Shards/waves complete OR checkpointed, tests written. Uses WAVE ORCHESTRATION (output_contract). | 40% |
| VALIDATE | Coverage ≥ target, gates pass, no P0 | 10% |
| REVIEW | Critic reviewed, simplifications applied, **reflection completed** (`trw_reflect`) | 5% |
| DELIVER | PR created OR archived, final.md, **CLAUDE.md synced** (`trw_claude_md_sync`), CHANGELOG.md updated | 5% |

ORC tracks elapsed wall-clock against TIMEBOX_HOURS.

<phase_transitions>
Before advancing to the next phase, ORC MUST verify exit criteria:
- `MCP_MODE: tool` → call `trw_phase_check(phase_name)` — advances only if `valid: true`
- `MCP_MODE: manual` → ORC checks exit criteria from the table above manually
</phase_transitions>

<rules>
ORC MUST NOT advance until exit criteria met OR cap exceeded with rationale.
Refine plan until stable — fixing a plan is cheaper than rewriting code.
Two consecutive iterations with <5% findings delta → re-plan or advance to DELIVER.
</rules>

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

## FORMATIONS

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

Formation scope: within a single wave. Each wave MAY use a different formation. Formations MUST NOT span waves.

---

## GATES

```
VALIDATE/DELIVER boundary?
├─ YES → FULL GATE (≥quorum judges, pairwise+rubric)
└─ NO → PLAN/REVIEW decision?
        ├─ YES → LIGHT GATE (2 judges, rubric only)
        └─ NO → Quality contested?
                ├─ YES → SPAWN CRITIC
                └─ NO → NO GATE (checkpoint only)
```

Rubric: correctness 35, tests 20, security 15, performance 10, maintainability 10, completeness 10.
Pass: `consensus ≥ quorum` AND `correlation ≥ CORRELATION_MIN`.
Fail: document reasons → revert to prior phase (add tests, refactor, fix) → retry gate. Two consecutive gate failures → escalate to user.

---

## BOOTSTRAP

<mcp_check>
ORC MUST detect MCP tool availability at bootstrap and set `MCP_MODE` in `run.yaml`:

1. Call `trw_init(task_name=TASK, objective=...)`.
2. If tool returns successfully → `MCP_MODE: tool`. Init is complete (dirs, run.yaml, events.jsonl, FRAMEWORK_SNAPSHOT all created).
3. If tool unavailable or errors → `MCP_MODE: manual`. Run manual fallback (see CLAUDE.md for bootstrap script).
</mcp_check>

<rules>
- ORC MUST log `MCP_MODE` at bootstrap — all subsequent sections reference this value
- ORC MUST restore latest `{TASK_DIR}/runs/**` or honor `{RUN_ID}` and recreate scaffolding
- All writes MUST stay within `{REPO_ROOT}/**` and `{TASK_DIR}/**`
- `{RUN_ROOT}/meta/FRAMEWORK_SNAPSHOT.md` = authoritative reference for run
- If `docs/documentation/` or `docs/knowledge-catalogue/` exist → note paths in `run.yaml`
- If AARE-F framework file exists → log location to `run.yaml`, follow for requirements work
- Worktree strategy: in-repo under `docs/` (portability, single-clone simplicity)
- Run artifacts (`docs/{TASK}/runs/**`, `.ai/**`) MUST NOT be committed.
- `docs/documentation/`, `docs/knowledge-catalogue/`, `docs/requirements-aare-f/` SHOULD be committed.
</rules>

---

## MCP TOOLS (trw-mcp)

When `MCP_MODE: tool`, ORC and shards MUST use these tools instead of manual equivalents. When `MCP_MODE: manual`, use inline bash/YAML fallbacks in each section.

| Tool | Obligation | When |
|------|------------|------|
| `trw_init` | MUST | Bootstrap |
| `trw_status` | SHOULD | Status checks |
| `trw_phase_check` | MUST | Before phase advance |
| `trw_wave_validate` | MUST | After each wave |
| `trw_resume` | MUST | Session resume |
| `trw_checkpoint` | SHOULD | Periodic |
| `trw_event` | SHOULD | Audit trail |
| `trw_shard_context` | MUST (shards) | First call in sub-agent |
| `trw_reflect` | MUST | After errors, at REVIEW, at DELIVER |
| `trw_learn` | SHOULD | Record discoveries during any phase |
| `trw_learn_update` | MAY | Lifecycle mgmt |
| `trw_learn_prune` | MAY | Learning hygiene |
| `trw_recall` | SHOULD | Before new tasks, at PLAN start |
| `trw_script_save` | MAY | Reusable scripts |
| `trw_claude_md_sync` | MUST | At DELIVER |
| `trw_prd_create` | SHOULD | Requirements |
| `trw_prd_validate` | MUST | Pre-IMPLEMENT |
| `trw_traceability_check` | SHOULD | VALIDATE/DELIVER |

### MCP Resources

| URI | Description |
|-----|-------------|
| `trw://config` | Current TRWConfig values |
| `trw://framework` | Bundled FRAMEWORK.md text |
| `trw://learnings` | Learning index from .trw/ |
| `trw://patterns` | Discovered patterns index |
| `trw://run-state` | Current run state (latest run.yaml) |

<rules>
- Shards inherit MCP_MODE from ORC — do not re-detect
- If a MUST tool fails at runtime, log error via `trw_event` (or events.jsonl) and fall back to manual
</rules>

---

## SUB-AGENT MCP ACCESS

Sub-agents (shards) inherit MCP tool access from the parent session. All TRW MCP tools are available to shards without re-registration. Shards MUST call `trw_shard_context` as their first MCP action to obtain run paths, shard ID, and tool guidance.

### Shard Context Injection

```
Shard start → trw_shard_context(run_path, shard_id)
            → returns: run_path, run_id, shard_id, wave_number,
                       scratch_path, findings_path, events_path,
                       tool_guidance
            → shard uses returned paths for all subsequent tool calls
```

Shards MUST pass `shard_id` to `trw_event`, `trw_learn`, `trw_checkpoint`, and `trw_recall` for attribution.

### Sub-Agent Tool Obligations

| Tool | Obligation | When |
|------|------------|------|
| `trw_shard_context` | MUST | First action in shard |
| `trw_event(shard_id=...)` | SHOULD | Progress, findings, errors |
| `trw_learn(shard_id=...)` | SHOULD | Discoveries, gotchas |
| `trw_checkpoint(shard_id=...)` | SHOULD | Before returning, periodic |
| `trw_recall` | SHOULD | Before starting research |
| `trw_finding_register` | SHOULD | When findings match finding schema |
| `trw_phase_check` | MUST NOT | Only ORC checks phase gates |
| `trw_init` | MUST NOT | Only ORC bootstraps runs |
| `trw_claude_md_sync` | MUST NOT | Only ORC syncs at DELIVER |

### Concurrent Safety

Sub-agents MAY run in parallel (multiple Task() calls in one message). Concurrent safety guarantees:

| Operation | Safety Model |
|-----------|-------------|
| JSONL append (`events.jsonl`, `checkpoints.jsonl`) | Advisory `LOCK_EX` per write — atomic, no interleaving |
| YAML write (`run.yaml`, `index.yaml`) | Atomic temp-file-then-rename with `LOCK_EX` |
| Read-Modify-Write (`index.yaml`) | `lock_for_rmw` advisory lock — serializes concurrent R-M-W cycles |
| Scratch directory | Per-shard isolation (`scratch/{shard_id}/`) — no contention |

Limitations: Advisory locks are process-scoped (fcntl). Across separate OS processes, lock files provide coordination but not strict mutual exclusion. Within a single MCP server process, all guarantees hold.

---

## FILE STRUCTURE

<meta_files format="yaml">
run.yaml:              status, phase, confidence
consensus.yaml:        decisions, thresholds
locks.yaml:            file/shard locks
formation_manifest.yaml: active formation
</meta_files>

<meta_files format="jsonl">
events.jsonl:          event stream
validations.jsonl:     validation stream
checkpoints.jsonl:     checkpoint stream
</meta_files>

| Dir | Contents |
|-----|----------|
| `reports/` | `plan.md`, `final.md` |
| `artifacts/` | bundles, outputs, `logs/` |
| `scratch/` | `_orchestrator/`, `shard-{id}/`, `_blackboard/`, `wave-{N}/` |
| `shards/` | `manifest.yaml`, `wave_manifest.yaml` |
| `validation/` | `risk-register.yaml` |

<shard_card>
Shard cards define parallel work units. Fields: `id`, `title`, `wave` (1-based; children inherit parent's wave), `goals`, `planned_outputs`, `output_contract` (file + schema keys + required + optional_keys), `input_refs` (file paths from prior waves), `self_decompose` (default: true), `max_child_depth` (default: 2), `confidence` (high|medium|low).
</shard_card>

---

## ARCHITECTURE

<architecture>

### Module Boundaries

TRW-MCP follows a layered architecture. Dependency direction flows downward only.

```
tools/ → state/ → models/
  ↓        ↓        ↓
server.py  scoring.py  exceptions.py
```

| Layer | Responsibility | May Import | May NOT Import |
|-------|---------------|------------|----------------|
| `tools/` | MCP tool functions, user-facing API | `state/`, `models/`, `exceptions` | — |
| `state/` | Business logic, persistence, validation | `models/`, `exceptions` | `tools/` |
| `models/` | Pydantic v2 data models, enums | `exceptions` | `tools/`, `state/` |
| `server.py` | FastMCP entry point, tool registration | `tools/` | `state/`, `models/` (direct) |

### Tool Modules

| Module | Tools | Purpose |
|--------|-------|---------|
| `tools/orchestration.py` | 8 | Run lifecycle: init, status, phase_check, wave_validate, resume, checkpoint, event, shard_context |
| `tools/learning.py` | 7 | Self-learning: reflect, learn, learn_update, recall, prune, script_save, claude_md_sync |
| `tools/requirements.py` | 4 | PRD management: prd_create, prd_validate, traceability_check, prd_status_update |
| `tools/findings.py` | 3 | Finding lifecycle: finding_register, finding_to_prd, finding_query |
| `tools/refactoring.py` | 3 | Debt management: refactor_classify, debt_register, debt_gate |
| `tools/ceremony.py` | 2 | Delivery: deliver, session_start |
| `tools/compliance.py` | 1 | Behavioral audit: compliance_check |
| `tools/build.py` | 1 | Build verification: build_check |
| `tools/testing.py` | 1 | Test targeting: test_target |
| `tools/bdd.py` | 1 | BDD scenario generation: bdd_generate |
| `tools/sprint.py` | 2 | Sprint management: tracks, velocity |

### Fitness Functions

Architecture fitness is opt-in via `architecture_fitness_enabled: true` in config. When enabled, `trw_phase_check` runs fitness checks at phase boundaries.

| Check | Phases | What It Validates |
|-------|--------|-------------------|
| Import direction | implement, validate | Layers only import downward per `dependency_rules` |
| Convention compliance | All (gated per convention) | Conventions like `no_star_imports` |

Configuration via `.trw/config.yaml`:

```yaml
architecture_fitness_enabled: true
architecture:
  dependency_rules:
    - layer: "models"
      may_import: []
      may_not_import: ["state", "tools"]
    - layer: "state"
      may_import: ["models"]
      may_not_import: ["tools"]
  conventions:
    - name: "no_star_imports"
      gate: "implement"
      check_method: "no_star_imports"
      severity: "error"
```

Fitness score: `max(0.0, 1.0 - violations × penalty)`. Violations are advisory (never block phase gates).

### Integration Checks

`check_integration()` at VALIDATE/DELIVER verifies:
- Every `tools/*.py` module has a `register_*_tools()` function
- Every register function is imported and called in `server.py`
- Every tool module has a corresponding test file

### Compliance Auditing

`trw_compliance_check(mode, strictness)` audits behavioral compliance against FRAMEWORK.md requirements:

| Dimension | What It Checks |
|-----------|---------------|
| RECALL | `trw_recall()` invoked at session start |
| EVENTS | Structured events logged during run |
| REFLECTION | `trw_reflect()` invoked at REVIEW/DELIVER |
| CHECKPOINT | Periodic checkpoints for long sessions |
| CHANGELOG | `CHANGELOG.md` updated for implementation runs |
| CLAUDE_MD_SYNC | `trw_claude_md_sync()` invoked at DELIVER |

Modes: `advisory` (warnings only) or `gate` (blocking). Returns compliance score 0.0–1.0.

</architecture>

---

## EXPLORATION & PLANNING SHARDS

RESEARCH and PLAN phases MUST use parallel blocking shards and persist all findings incrementally. This ensures break/resume safety and maximizes throughput during the most uncertainty-heavy phases.

### Parallel Exploration

When entering RESEARCH, ORC MUST:
1. Identify independent exploration axes (codebase areas, questions, evidence paths)
2. Launch them as parallel blocking Task() calls in a SINGLE message
3. Each shard writes its findings to disk BEFORE returning

Example: 3 axes (auth, database, API) → 3 parallel shards → each writes `scratch/shard-{id}/findings.yaml` → ORC synthesizes into `plan.md`.

Shard count: `clamp(MIN_SHARDS_FLOOR, axes_of_inquiry, PARALLELISM_MAX)`

### Persisted Findings Format

Every exploration or planning shard MUST write a findings file before returning:

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

<rules>
- Shards MUST write `findings.yaml` as their LAST action before returning
- Partial results MUST be written with `status: partial` if shard hits an error or timeout
- ORC MUST read findings from disk (not rely on Task() return text alone) for resume safety
- On resume: scan `scratch/shard-*/findings.yaml` and skip shards with `status: complete`
- Findings with `status: partial` MUST be re-run with narrowed scope
- Planning shards write to `scratch/shard-{id}/plan_fragment.yaml` (same structure, `phase: plan`)
</rules>

---

## WAVE ORCHESTRATION

Waves sequence groups of parallel shards with inter-wave data flow. Each wave completes before the next begins, enabling dependent shards to consume outputs from prior waves.

### Wave Manifest

`shards/wave_manifest.yaml` — each entry: `wave` (1-based), `shards` (IDs), `status` (pending|active|complete|failed|partial), `depends_on` (prior wave numbers).

### Execution Rules

| Rule | Description |
|------|-------------|
| Parallel within wave | All shards in a wave launch as blocking Task() calls in ONE message |
| Sequential between waves | Wave N+1 starts only after wave N status = `complete` |
| Fail-fast | If any shard in a wave fails, ORC MUST pause and replan before advancing |
| Manifest update | ORC MUST update `wave_manifest.yaml` status after each wave completes |
| Post-wave validation | When `MCP_MODE: tool`, call `trw_wave_validate(wave_number)` after each wave |
| Progress tracking | ORC SHOULD call `trw_status()` for wave progress overview |

### Blackboard (Inter-Shard Coordination)

```yaml
# scratch/_blackboard/{formation}.yaml
entries:
  - ts: "2026-01-25T12:00:01Z"
    shard: shard-001
    key: finding_001
    value: {summary: "...", confidence: high}
```
Append-only. Lock via `meta/locks.yaml`. Archive on completion. ORC MAY use per-wave blackboards (`scratch/_blackboard/wave-{N}/`).

### Replanning Triggers

| Trigger | Action |
|---------|--------|
| Shard failure in wave | Pause → assess → replan remaining waves |
| New dependency discovered | Insert new wave or merge into existing |
| Scope reduction | Remove unnecessary waves, update manifest |
| All shards independent | Collapse to single wave (see shortcut below) |

Wave replanning is tactical. For strategic plan changes (scope >20%, blockers), see ADAPTIVE PLANNING.

### Resume Protocol

On resume, `trw_resume()` (or manual scan) classifies shards as complete/partial/failed/not_started. Launch only incomplete shards as parallel blocking Task() calls. Session break loses at most in-flight shards, never completed work.

### Single-Wave Shortcut

When ALL shards are independent, ORC MAY omit `wave_manifest.yaml` and launch all shards directly.

---

## OUTPUT CONTRACTS

Every shard declares what it will produce (`output_contract`: `file`, `schema` with `keys`/`required`, `optional_keys`). ORC validates after each wave.

### Dependency Graph

```
Wave 1: shard-001 → result.yaml ─┐
         shard-002 → result.yaml ─┼─→ Wave 2: shard-004 (input_refs: [shard-001, shard-002])
         shard-003 → result.yaml ─┘
                                       Wave 2: shard-005 (input_refs: [shard-003])
```

### Validation Rules

| Rule | Description |
|------|-------------|
| Post-wave check | ORC MUST verify each shard's output file exists and contains required keys |
| Missing output | Block next wave, log failure, trigger replan |
| Schema mismatch | Warn + log; proceed only if optional keys missing |
| Contract immutability | Once a wave starts, its shards' contracts MUST NOT change |

---

## SELF-DIRECTING SHARDS

Shards MAY self-decompose into child shards (bounded recursion). Eligibility — ALL must be true:
1. `self_decompose: true` in shard card
2. Current depth < `MAX_CHILD_DEPTH`
3. Task has ≥2 independent subtasks identifiable before execution
4. Parent shard can define output contracts for each child

### Child Shard Rules

| Rule | Description |
|------|-------------|
| Blocking | Child shards MUST be launched as blocking Task() calls |
| Formation | Parent selects formation for children (MAP-REDUCE typical) |
| Persistence | Children write to `scratch/shard-{parent}/children/shard-{child}/` |
| Depth tracking | Each child card includes `depth: {parent_depth + 1}` |
| Aggregation | Parent MUST aggregate child outputs into its own output contract |

Child manifest in `scratch/shard-{parent}/children/manifest.yaml`: `parent`, `depth`, `children` list (each with `id`, `depth`, `status`, `output_contract`).

### Depth Limits

Depth 0 = ORC-spawned, 1 = child, 2 = grandchild. Hard ceiling: 3 (token budgets fragment below useful thresholds). Override per-shard via `max_child_depth` in card.

<rules>
- At hard ceiling, shard MUST NOT self-decompose regardless of card settings
- Parent MUST wait for all children before writing its own output
- If any child fails, parent MUST handle (retry, replan, or fail with partial)
</rules>

---

## ADAPTIVE PLANNING

`reports/plan.md` is NOT frozen. Update when new info invalidates assumptions, scope changes >20%, approach fails, or user feedback.

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

## PHASE REVERSION

<phase_reversion>

Agents SHOULD revert to earlier phases when implementation reveals structural gaps. Reverting early prevents workarounds that compound technical debt.

### ReversionTrigger Classification

| Trigger | Value | When to Use |
|---------|-------|-------------|
| `REFACTOR_NEEDED` | `refactor_needed` | Structural refactor required before proceeding |
| `ARCHITECTURE_MISMATCH` | `architecture_mismatch` | Planned architecture conflicts with discovered requirements |
| `NEW_DEPENDENCY` | `new_dependency` | Undiscovered dependency must be addressed first |
| `TEST_STRATEGY_CHANGE` | `test_strategy_change` | Test approach needs revision |
| `SCOPE_CHANGE` | `scope_change` | Requirements expanded beyond original plan |
| `OTHER` | `other` | Catch-all for uncategorized triggers |

### When to Revert vs Push Through

| Transition | Revert When | Push Through When |
|------------|-------------|-------------------|
| IMPLEMENT → PLAN | Module boundaries need redesign; approach conflicts with plan | Local workaround not affecting other modules |
| IMPLEMENT → RESEARCH | Technical approach based on incorrect assumptions | Rare — indicates significant planning gap |
| VALIDATE → IMPLEMENT | Test failures reveal design flaw (not just a bug) | Implementation bugs fixable in-phase |
| VALIDATE → PLAN | Test strategy itself is wrong | Test execution failures, not strategy flaws |
| REVIEW → IMPLEMENT | Review requires structural changes beyond a patch | Minor fixes or cosmetic improvements |

### How to Revert

Log a `phase_revert` event. The handler validates ordering (target MUST be earlier than source), classifies the trigger, captures affected PRDs, and atomically updates `run.yaml` phase.

```
trw_event(
  event_type="phase_revert",
  data={
    "from_phase": "implement",
    "to_phase": "plan",
    "trigger": "refactor_needed",
    "reason": "Shared utility X needs extraction before module Y"
  }
)
```

### Reversion Health Metrics

`trw_status()` includes reversion metrics from `events.jsonl`:

| Classification | Rate | Meaning |
|----------------|------|---------|
| `healthy` | <15% | Normal discovery-driven adjustments |
| `elevated` | 15–30% | Planning may need more research depth |
| `concerning` | ≥30% | Significant planning gaps — increase RESEARCH waves |

Thresholds configurable via `reversion_rate_elevated` and `reversion_rate_concerning` in config.

</phase_reversion>

---

## REFACTORING WORKFLOW

<refactoring>

When shards discover structural impediments during implementation, classify them immediately using the 2x2 matrix.

### Classification Matrix

ORC or shard MUST call `trw_refactor_classify(description, blocks_output_contract, changes_interface)` within 1 turn of discovery.

|  | Local (no interface change) | Architectural (changes shared interface) |
|---|---|---|
| **Blocking** (shard cannot complete) | Inline refactor. Separate commit. QOL log. | Create prerequisite PRD. Phase revert to PLAN. Execute as new wave. |
| **Deferrable** (shard can complete) | P2 TODO or QOL fix if <10 lines. Debt registry entry. | Create P2-P3 PRD. Add to roadmap backlog. Debt registry entry. |

Decision questions:
- **Blocking vs Deferrable**: Can the current shard complete its output contract WITHOUT this refactor?
- **Local vs Architectural**: Does this refactor change an interface that other modules depend on?

### Tool Chain

```
Discovery → trw_refactor_classify() → classification + prescribed action
         → trw_debt_register()      → DEBT-{NNN} in .trw/debt-registry.yaml
         → trw_debt_gate()          → budget recommendation at phase boundaries
```

### Blocking-Architectural Workflow

For the most disruptive quadrant (`blocking-architectural`):

1. **CHECKPOINT**: `trw_checkpoint("pre-refactor")`
2. **CLASSIFY**: `trw_refactor_classify(...)` — confirm `blocking-architectural`
3. **EXTRACT PRD**: `trw_prd_create(...)` with prerequisite dependency on current feature PRD
4. **REVERT**: `trw_event("phase_revert", data={trigger: "refactor_needed", ...})`
5. **PLAN**: Plan refactor as new wave(s) with rollback criteria
6. **IMPLEMENT**: Execute behavior-preserving refactor with tests
7. **VALIDATE**: Full test suite via `trw_build_check`
8. **RESUME**: Return to feature implementation on the now-refactored codebase

For `blocking-local`: collapse to checkpoint → inline refactor → separate commit → resume.

### Debt Budget at Phase Gates

`trw_debt_gate(phase)` recommends shard allocation:

| Phase | Behavior | Gate Impact |
|-------|----------|-------------|
| PLAN | Reports critical/high debt affecting planned files | Warning if critical items exist |
| VALIDATE | Reports potential new debt introduced during IMPLEMENT | Advisory only |

Budget heuristic: critical debt → allocate up to 20% of wave capacity for refactoring shards. High debt → at least 15%.

### Debt Lifecycle

```
discovered → assessed → scheduled → in_progress → resolved
```

Decay scoring: debt that persists across sessions grows more urgent (`decay_score` increases over time). Auto-promotes to `critical` when `decay_score >= 0.9`.

</refactoring>

---

## PARALLELISM

Heuristic: if shards are independent (≤5% file overlap), spawn `clamp(MIN_SHARDS_FLOOR, axes_of_work, PARALLELISM_MAX)`. Default: 3. Trivial tasks: 1.

| Mode | Allowed |
|------|---------|
| Parallel blocking shards (multiple Task() in ONE message) | REQUIRED when independent |
| Background Bash (`&` tracked in events.jsonl) | ALLOWED |

<rules>
- Every Task() call MUST block (omit `run_in_background` or set `false`). WHY: background agents lose MCP tools, cause token explosion (30-50K+), context staleness, file lock deadlocks.
- If wave output similarity > CORRELATION_MIN → spawn a dissenting shard in the next wave
- Self-check before every Task(): "Will I wait for this result before my next action?" YES = correct.
</rules>

---

## REQUIREMENTS

<pre_development>
Before IMPLEMENT, verify:
1. Source identified (PRD, issue, request)
2. Acceptance criteria in `plan.md`
3. Each REQ has: ID, criterion, verification method
</pre_development>

<post_development>
Before DELIVER:
```yaml
requirements_traceability:
  - req_id: REQ-001
    implemented_in: [src/auth/login.py]
    verified_by: [tests/test_auth.py::test_login]
    status: PASS
```
</post_development>

### AARE-F Tools

When `MCP_MODE: tool` and AARE-F framework file exists: `trw_prd_create` at RESEARCH/PLAN, `trw_prd_validate` (MUST pass) pre-IMPLEMENT, `trw_traceability_check` at VALIDATE/DELIVER.

---

## TDD & CODE QUALITY

<rules>
- Non-trivial code MUST have tests first
- `src/**` changes without `tests/**` → validation MUST fail (exception: whitespace, comments, docs only)
- Coverage: global ≥85%, diff ≥90%
- Structured logging: JSONL with `ts`, `level`, `component`, `op`, `outcome`. Redact secrets and PII before logging.
</rules>

---

## TESTING STRATEGY

<testing_strategy>

Testing scope varies by phase. Use `trw_test_target` for targeted tests during IMPLEMENT and `trw_build_check` for full verification at VALIDATE/DELIVER.

### Phase-Specific Testing

| Phase | What to Run | Tool | Coverage | mypy |
|-------|-------------|------|----------|------|
| RESEARCH | None | — | No | No |
| PLAN | None | — | No | No |
| IMPLEMENT | Targeted unit tests on changed files | `trw_test_target(changed_files, phase="implement")` | No | No |
| VALIDATE | Unit + integration, full suite | `trw_build_check(scope="full")` | Yes (≥85%) | Yes (--strict) |
| REVIEW | Tests should already pass from VALIDATE | — | No | No |
| DELIVER | Full suite with coverage gates | `trw_build_check(scope="full")` | Yes (≥85%) | Yes (--strict) |

### Targeted Testing (`trw_test_target`)

Analyzes changed source files and returns a targeted test subset:
- Maps source files to test files via dependency map (`.trw/test-map.yaml`)
- Resolves transitive dependencies using BFS on the import graph
- Generates parallel-safe pytest command (isolated via `run_id`)
- Returns phase-appropriate strategy recommendation

First use requires `generate_map=True` to build the dependency map. Subsequent calls reuse the cached map.

### Build Verification (`trw_build_check`)

Runs pytest and/or mypy, caches results to `.trw/context/build-status.yaml`. Phase gates read the cache — they never spawn subprocesses directly.

| Scope | Runs | Use When |
|-------|------|----------|
| `full` | pytest + mypy | VALIDATE/DELIVER phase gates |
| `pytest` | pytest only | Quick test verification |
| `mypy` | mypy only | Type checking only |

Cached results older than 30 minutes are flagged as stale. Enforcement levels: `strict` (errors block), `lenient` (warnings only, default), `off` (skipped).

</testing_strategy>

---

## TOOL RETRY

```
Max: 3 | Backoff: exponential+jitter
  1: immediate
  2: 2s + jitter(0-2s)
  3: 4s + jitter(0-4s)
  Fail: log events.jsonl, escalate
```

---

## ERROR HANDLING

Prevention: validate inputs before shard launch, set timeouts at Task() creation, use output_contract to catch drift early.

| Scenario | Detection | Recovery |
|----------|-----------|----------|
| Tool failure | Tool returns error | Retry w/ backoff → alternate tool → log events.jsonl |
| Shard timeout | Exceeds 2x expected duration | Halt, commit partial (`status: partial`), decompose smaller |
| Shard file error | Missing input / permission denied | Escalate to ORC → replan wave |
| Shard network error | API/fetch failure | Retry w/ backoff (3x) → fail with partial |
| Logic contradiction | Conflicting evidence | Debate+Critic → judges adjudicate → fix tests, then code |
| Path breach | Write outside boundary | Halt, log, revert, re-plan |
| Off-track output | Shard veering from requirements | Revert to last checkpoint, restart with tighter constraints |

---

## GIT

```bash
git status -sb
git add <specific-paths>           # Use `git add <specific-paths>` (interactive modes unavailable)
git commit -m "feat(scope): msg" -m "WHY: rationale" -m "RUN_ID: {RUN_ID}"
git push -u origin "{BRANCH}"
gh pr create --fill --head "{BRANCH}"
```

All file paths in commands, logs, and shard cards MUST be absolute paths derived from TASK_DIR or REPO_ROOT.
Update CHANGELOG.md `[Unreleased]` for user-visible changes at DELIVER.
When `MCP_MODE: tool`, ORC SHOULD call `trw_event("git_commit", data={branch, message, run_id})` after commits.

---

## TURN HYGIENE

Turn start: status (Green|Amber|Red), phase, wave progress, next actions. Turn end: decisions, artifacts modified, next action. Compact format only.

---

## MODEL

- Primary: **Opus 4.6**; child shards (depth ≥2) or trivial subtasks MAY use Haiku 4.5 / Sonnet 4.5
- Agents SHOULD act. Chat MUST remain minimal. Artifacts MUST be auditable.

---

## TODO REGISTRY

Use built-in `TaskCreate` / `TaskUpdate` for TODO tracking (no custom `todo.jsonl`).

| Priority | Meaning | ORC Action at Phase Transitions |
|----------|---------|-------------------------------|
| P0 | Blocker | Blocks current wave — resolve immediately |
| P1 | Important | Becomes shard in next wave if capacity allows |
| P2 | Nice-to-have | Logged, addressed opportunistically or deferred |

Shards MUST create a TODO when noticing an improvement outside their scope.

---

## SELF-IMPROVEMENT & LEARNING

Learning lifecycle (see MCP TOOLS table for tool obligations):
```
Before task → trw_recall(query)          # SHOULD: check prior knowledge
During work → trw_learn(summary, detail) # SHOULD: record workarounds, API gotchas, env issues
After errors → trw_reflect(run_path)     # MUST: auto-extract cause, impact, prevention
On fix → trw_learn_update(id, "resolved")
At DELIVER → trw_claude_md_sync()        # MUST: promote high-impact → CLAUDE.md
```

When `MCP_MODE: manual`, write learnings directly to CLAUDE.md or sub-CLAUDE.md files.

### Mandatory Reflection Triggers

| Trigger | Action | Target |
|---------|--------|--------|
| Workaround >2 retries | `trw_learn` + write to CLAUDE.md or sub-CLAUDE.md | Root or module |
| Non-obvious API behavior | `trw_learn` + sub-CLAUDE.md | Module dir |
| Environment-specific issue | `trw_learn` + root CLAUDE.md | Project root |
| Phase gate failure | `trw_reflect` MUST run before retry | Run |
| REVIEW phase entry | `trw_reflect` MUST run before `trw_phase_check("review")` | Run |
| DELIVER phase entry | `trw_claude_md_sync` MUST run before `trw_phase_check("deliver")` | Project |

### Reflection Gate

ORC MUST call `trw_reflect(run_path)` before `trw_phase_check("review")` and before `trw_phase_check("deliver")`.
`trw_phase_check` verifies reflection events exist in `events.jsonl` — gate warns without them.
After `trw_claude_md_sync()`, ORC MUST log: `trw_event("claude_md_synced", data={scope, entries_promoted})`.

### PSR (Prompt Self-Review)

| Phase | Inputs | Outputs |
|-------|--------|---------|
| PLAN start | Objective, prior knowledge | Assumptions → `trw_learn` entries |
| REVIEW | Run events, outcomes | What helped/hurt → `trw_reflect` + propose framework edits |
| DELIVER | High-impact learnings | Promote findings → `trw_claude_md_sync` (MUST) |

### CLAUDE.md Protocol

- Root: max 200 lines. Sub-CLAUDE.md: max 50 lines, max depth 3.
- Structure: Context, Key Facts, Gotchas, See Also.
- Append (don't rewrite). Date-stamp entries.

<mandatory_reads>
CLAUDE.md MUST be read at: session start, every PLAN phase, after errors, before major refactors.
</mandatory_reads>

---

## ARTIFACT & PROMPT PATTERNS

All generated artifacts (reports, shard cards, sub-agent prompts, plans) MUST follow:

<patterns>
| Pattern | Apply To | Why |
|---------|----------|-----|
| YAML over JSON | configs, structured data | 50% fewer tokens |
| XML tags | prompt sections, rules | Claude-trained parsing |
| RFC 2119 caps | requirements (MUST/SHOULD/MAY) | Unambiguous obligation |
| Tables over prose | comparisons, options, lists | Dense + scannable |
| Rules not explanations | constraints, instructions | LLMs don't need "why" |
</patterns>

<sub_agent_prompts>
Shard prompts use `<context>`, `<task>`, `<output_contract>`, `<constraints>`, `<mcp_tools>` XML tags.
Inputs as file paths (never inlined). Target: <500 tokens. Output: YAML. Write contract file LAST; if input missing, write `status: failed`.

<mcp_tools>
First call: `trw_shard_context(run_path, shard_id)` → use returned paths for all tools.

| Shard Type | Tool Sequence |
|------------|---------------|
| research | `trw_recall` → `trw_event` (progress) → `trw_learn` (discoveries) → `trw_checkpoint` |
| planning | `trw_recall` → `trw_event` (decisions) → `trw_checkpoint` |
| implementation | `trw_event` (progress) → `trw_learn` (gotchas) → `trw_checkpoint` |
| grooming | `trw_recall` → `trw_event` (refinements) → `trw_learn` (gaps found) |
| validation | `trw_event` (results) → `trw_learn` (failures) → `trw_checkpoint` |

Pass `shard_id` to every tool call for attribution.
</mcp_tools>
</sub_agent_prompts>

<reports>
`plan.md` and `final.md`: YAML frontmatter, tables for requirements, bullet lists over paragraphs.
</reports>

---

## FRAMEWORK ADHERENCE

<mandatory_triggers>
| Trigger | Action |
|---------|--------|
| Every 5 waves | Re-read framework, log compliance |
| After compact | IMMEDIATELY re-read before work |
| Phase transition | Re-read relevant section |
| Gate failure | Re-read gate requirements |
</mandatory_triggers>

Log: `{"event":"framework_review","trigger":"...","violations":0}`

### Context Compaction Protocol

On context compact: (1) persist all state to `run.yaml` and critical files, (2) commit green state, (3) reload FRAMEWORK.md + CLAUDE.md, (4) resume from `wave_manifest.yaml`.

### Mid-Stream User Input

| Shard Progress | Action |
|---------------|--------|
| <50% complete | Checkpoint current state, defer shard, address user request |
| >50% complete | Complete current shard, then address user request |
| User request is P0 | Micro-commit if green, rollback if red, then switch immediately |

---

## RISK REGISTRY

`validation/risk-register.yaml` — each risk: `{id, description, impact, likelihood, mitigation, status: open|mitigated|accepted}`.

---

## QOL CHANGES (Opportunistic Cleanup)

Shards MAY fix minor issues (<10 lines, already-open files, obviously correct, no behavior change, ≤5% effort). Separate commits. Log: `qol_fixes: [{file, change, lines_changed}]`. When in doubt → P2 TODO.
