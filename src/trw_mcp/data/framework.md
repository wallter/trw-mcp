v17.2_TRW — CLAUDE CODE ORCHESTRATED AGILE SWARM
Slim-Persist | Parallel-First | Formation-Driven | Interrupt-Safe | CLI/TDD | YAML-First | Sensible Defaults | MCP-Integrated
Version date: 2026-02-07 | Model: Opus 4.6

<critical>
## EXECUTION MODEL SUMMARY

**v17.2_TRW | Opus 4.6 | 6 phases | 4 formations | 3 confidence levels | 17 MCP tools**

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

<claude_code_patterns>
**Tool patterns:**
- Independent tool calls → single message, parallel execution
- Read every file before modifying it
- `/clear` between unrelated tasks — reset context

**Output control:**
- Prefill `{` to force JSON output (API)
- `<thinking>...</thinking>` then `<answer>...</answer>` for CoT
- Explicit format in prompt → model follows

**Claude Opus 4.6 behavior:**
- Plans more carefully, sustains agentic tasks longer
- Literal instruction following — say what TO do, not what NOT to do
- Normal language works — excessive CRITICAL/MUST unnecessary
- Provide WHY for better generalization
- Adaptive thinking — effort parameter (low/medium/high/max) replaces manual budget_tokens
- May overspawn subagents — use wave orchestration to control
</claude_code_patterns>

<abbreviations>
RR := {RUN_ROOT}  | TD := {TASK_DIR}  | RID := {RUN_ID}  | ORC := Orchestrator
</abbreviations>

<variables>
TASK       := task_short_desc
TASK_DIR   := ./docs/{TASK}
RUN_ID     := {utc_ts}-{short_id}
RUN_ROOT   := {TASK_DIR}/runs/{RUN_ID}
REPO_ROOT  := $(git rev-parse --show-toplevel)
BRANCH     := feat/{TASK}-{short_id}
</variables>

---

## DEFAULTS

```yaml
PARALLELISM_MAX: 10          # max concurrent shards
MIN_SHARDS_TARGET: 3         # minimum parallel (adaptive)
MIN_SHARDS_FLOOR: 2          # hard floor
AUTONOMY_LEVEL: auto         # auto | review | stop_on_gate
CONSENSUS_QUORUM: 0.67       # 2/3 judges agree
CORRELATION_MIN: 0.7         # inter-judge agreement
CHECKPOINT_SECS: 600         # 10 min
TIMEBOX_HOURS: 8
MAX_CHILD_DEPTH: 2           # max self-decomposition recursion
SELF_DECOMPOSE_DEFAULT: true
MAX_RESEARCH_WAVES: 3
QOL_FIXES_ENABLED: true
LEARNING_PROMOTION: true
KNOWLEDGE_SYSTEMS: auto      # auto | off
```

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
- ORC MUST restore latest `{TD}/runs/**` or honor `{RID}` and recreate scaffolding
- All writes MUST stay within `{REPO_ROOT}/**` and `{TD}/**`
- `{RR}/meta/FRAMEWORK_SNAPSHOT.md` = authoritative reference for run
- If `docs/documentation/` or `docs/knowledge-catalogue/` exist → note paths in `run.yaml`
- If AARE-F framework file exists → log location to `run.yaml`, follow for requirements work
- Worktree strategy: in-repo under `docs/` (portability, single-clone simplicity). Do not change without documenting rationale.
- Run artifacts (`docs/{TASK}/runs/**`, `.ai/**`) MUST NOT be committed.
- `docs/documentation/`, `docs/knowledge-catalogue/`, `docs/requirements-aare-f/` SHOULD be committed.
</rules>

---

## MCP TOOLS (trw-mcp)

When `MCP_MODE: tool`, ORC and shards MUST use these tools instead of manual equivalents. When `MCP_MODE: manual`, use inline bash/YAML fallbacks in each section.

RFC 2119 obligations: MUST = required (manual fallback if tool errors); SHOULD = preferred; MAY = optional.

| Tool | Obligation | When |
|------|------------|------|
| `trw_init` | MUST | Bootstrap |
| `trw_status` | SHOULD | Status checks |
| `trw_phase_check` | MUST | Before phase advance |
| `trw_wave_validate` | MUST | After each wave |
| `trw_resume` | MUST | Session resume |
| `trw_checkpoint` | SHOULD | Every CHECKPOINT_SECS |
| `trw_event` | SHOULD | Audit trail |
| `trw_reflect` | SHOULD | After errors/friction |
| `trw_learn` | MAY | Record discoveries |
| `trw_learn_update` | MAY | Lifecycle mgmt |
| `trw_learn_prune` | MAY | Learning hygiene |
| `trw_recall` | MAY | Before new tasks |
| `trw_script_save` | MAY | Reusable scripts |
| `trw_claude_md_sync` | SHOULD | At DELIVER |
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

## LEGACY MIGRATION

On init, ORC MUST scan `{RR}/meta/` for `.json` (excluding `.jsonl`):
```
Glob pattern: {RUN_ROOT}/meta/*.json  (then exclude *.jsonl matches)
```
If found: convert → YAML, validate, archive to `artifacts/legacy/`, log. **Priority: FIRST.**

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
| REVIEW | Critic reviewed, simplifications applied | 5% |
| DELIVER | PR created OR archived, final.md, CHANGELOG.md updated | 5% |

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
Shards MAY flag discoveries that contradict prior assumptions via blackboard entry with key `emergent_axis` (e.g., "database is graph-based, not SQL"). ORC SHOULD spawn a targeted follow-up wave for each emergent axis.

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
- On resume: ORC MUST scan `scratch/shard-*/findings.yaml` and skip shards with `status: complete`
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
| Output persistence | Every shard MUST write its output to disk before returning |
| Post-wave validation | When `MCP_MODE: tool`, ORC MUST call `trw_wave_validate(wave_number)` after each wave |
| Progress tracking | ORC SHOULD call `trw_status()` for wave progress overview |

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
1. `self_decompose: true` in shard card (default: `SELF_DECOMPOSE_DEFAULT`)
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

## BLACKBOARD

For inter-shard coordination:
```yaml
# scratch/_blackboard/{formation}.yaml
entries:
  - ts: "2026-01-25T12:00:01Z"
    shard: shard-001
    key: finding_001
    value: {summary: "...", confidence: high}
```
Append-only. Lock via `meta/locks.yaml`. Archive on completion.

Wave isolation: ORC MAY use per-wave blackboards (`scratch/_blackboard/wave-{N}/`). Default: single shared blackboard.

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

## TDD & CODE QUALITY

<rules>
- Non-trivial code MUST have tests first
- `src/**` changes without `tests/**` → validation MUST fail (exception: whitespace, comments, docs only)
- Coverage: global ≥85%, diff ≥90%
- Structured logging: JSONL with `ts`, `level`, `component`, `op`, `outcome`. Redact secrets and PII before logging.
</rules>

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

## TURN HYGIENE

At turn start, report: status (Green|Amber|Red + reason), phase, wave progress, active shards, next actions.
At turn end, report: key decisions, artifacts modified, immediate next action.
Use compact format; include only fields relevant to current phase.

---

## GIT

```bash
git status -sb
git add <specific-paths>           # Use `git add <specific-paths>` (interactive modes unavailable)
git commit -m "feat(scope): msg" -m "WHY: rationale" -m "RUN_ID: {RID}"
git push -u origin "{BRANCH}"
gh pr create --fill --head "{BRANCH}"
```

All file paths in commands, logs, and shard cards MUST be absolute paths derived from TASK_DIR or REPO_ROOT.
Update CHANGELOG.md `[Unreleased]` for user-visible changes at DELIVER.
When `MCP_MODE: tool`, ORC SHOULD call `trw_event("git_commit", data={branch, message, run_id})` after commits.

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

## MODEL

- Primary: **Opus 4.6** (or task-specified)
- Cost-aware: trivial subtasks MAY use Haiku 4.5 / Sonnet 4.5
- Depth-aware: child shards (depth ≥2) MAY use lighter models (Haiku 4.5 / Sonnet 4.5)
- Agents SHOULD act. Chat MUST remain minimal. Artifacts MUST be auditable.

---

## SELF-IMPROVEMENT & LEARNING

Learning lifecycle (see MCP TOOLS table for tool obligations):
```
Before task → trw_recall(query)          # check prior knowledge
During work → trw_learn(summary, detail) # record workarounds, API gotchas, env issues
After errors → trw_reflect(run_path)     # auto-extract cause, impact, prevention
On fix → trw_learn_update(id, "resolved")
At DELIVER → trw_claude_md_sync()        # promote high-impact → CLAUDE.md
```

When `MCP_MODE: manual`, write learnings directly to CLAUDE.md or sub-CLAUDE.md files.

### PSR (Prompt Self-Review)
- At PLAN start: capture objective digest and key assumptions
- At REVIEW: capture what helped, what hurt, propose framework edits

### CLAUDE.md Protocol

- Root: max 200 lines. Sub-CLAUDE.md: max 50 lines, max depth 3.
- Structure: Context, Key Facts, Gotchas, See Also.
- Append (don't rewrite). Date-stamp entries.
- Read at: session start, every PLAN phase, after errors, before major refactors.

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
| Abbreviations | repeated terms (define once) | Token reduction |
| Rules not explanations | constraints, instructions | LLMs don't need "why" |
</patterns>

<sub_agent_prompts>
Shard prompts use `<context>`, `<task>`, `<output_contract>`, `<constraints>` XML tags.
Inputs as file paths (never inlined). Target: <500 tokens. Output: YAML. Write contract file LAST; if input missing, write `status: failed`.
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

When `QOL_FIXES_ENABLED: true`, shards MAY fix minor issues (<10 lines, already-open files, obviously correct, no behavior change, ≤5% effort). Separate commits. Log: `qol_fixes: [{file, change, lines_changed}]`. When in doubt → P2 TODO.

---

## KNOWLEDGE SYSTEMS

| System | Path | Purpose | When |
|--------|------|---------|------|
| Documentation | `docs/documentation/` | LLM-optimized project reference | Consult during RESEARCH; update at DELIVER |
| Knowledge Catalogue | `docs/knowledge-catalogue/` | Structured knowledge entries | Consult during RESEARCH; create entries for significant findings |

<rules>
- `KNOWLEDGE_SYSTEMS: auto` + directories exist → consult and update. Otherwise create `DOCS_INDEX.md` (minimum viable).
- SHOULD, not MUST. Complements CLAUDE.md (don't duplicate).
- AARE-F for requirements work — reference it, do not inline.
</rules>
