v23.0_TRW — CLAUDE CODE ORCHESTRATED AGILE SWARM
Slim-Persist | Parallel-First | Formation-Driven | Interrupt-Safe | CLI/TDD | YAML-First | Sensible Defaults | MCP-Integrated | Skills-Driven
Version date: 2026-02-18 | Model: Opus 4.6

<trw-framework>

<execution-summary>
## EXECUTION MODEL SUMMARY

**v23.0_TRW | Opus 4.6 | 6 phases | 4 formations | 3 confidence levels | 11 MCP tools | 10 skills | 4 agents**

All Task() calls block. Multiple in ONE message = parallel. Background agents = FORBIDDEN (see PARALLELISM).
MCP_MODE: tool → use trw-mcp tools. MCP_MODE: manual → bash fallbacks.
Principles: Behavioral > Structural. Prevention > Detection. External > Internal.
</execution-summary>

<design-principles>
P1: **Behavioral > Structural** — instruct what to DO, not what to BE
P2: **Prevention > Detection** — make bad patterns structurally impossible
P3: **External > Internal** — externalize infrastructure to tools, keep prompt behavioral
</design-principles>

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
| `high` | >=85% confidence | Pass |
| `medium` | 70-85% | Review |
| `low` | <70% | Block -> Critic |

Shard-to-run rollup: run confidence = lowest shard confidence in active wave.

---

## PERSISTENCE

<critical-files>
| File | Update When | Failure |
|------|-------------|---------|
| `reports/plan.md` | Plan changes | Block IMPLEMENT |
| `reports/final.md` | Run completes | Block DELIVER |
| `meta/run.yaml` | Phase/status | Invalid state |
| `meta/events.jsonl` | Significant event | Lost audit |
| `shards/wave_manifest.yaml` | Wave status changes | Lost wave state |
</critical-files>

<persistence-rules>
Write every state change to disk immediately, verify the write succeeded, then proceed.
Treat persistence failures as P0 blockers.
</persistence-rules>

---

## PHASES

```
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
```

| Phase | Exit Criteria | Skills | Cap |
|-------|---------------|--------|-----|
| RESEARCH | plan.md draft, >=3 evidence paths, formation selected. | `/framework-check` | 25% |
| PLAN | Acceptance criteria, shards planned, wave_manifest.yaml created. | `/sprint-init`, `/prd-new`, `/prd-groom`, `/prd-review` | 15% |
| IMPLEMENT | Shards/waves complete OR checkpointed, tests written. | `/test-strategy` | 35% |
| VALIDATE | Coverage >= target, gates pass, no P0. Run `trw_build_check(scope="full")`. | `/test-strategy` | 10% |
| REVIEW | Critic reviewed, simplifications applied, reflection completed. | `/memory-optimize`, `/memory-audit` | 10% |
| DELIVER | PR created OR archived, final.md, CLAUDE.md synced. | `/deliver`, `/sprint-finish` | 5% |

ORC tracks elapsed wall-clock against TIMEBOX_HOURS.

<phase-transitions>
Before advancing to the next phase, ORC MUST verify exit criteria manually from the table above.
</phase-transitions>

<phase-rules>
ORC MUST NOT advance until exit criteria met OR cap exceeded with rationale.
Refine plan until stable — fixing a plan is cheaper than rewriting code.
Two consecutive iterations with <5% findings delta -> re-plan or advance to DELIVER.
</phase-rules>

### Dynamic Research (Research Reactor)

After each RESEARCH wave, ORC evaluates findings and MAY spawn follow-up waves:

| Condition | Action | Cap |
|-----------|--------|-----|
| >30% findings have `open_questions` | Spawn follow-up research wave targeting open questions | MAX_RESEARCH_WAVES |
| Findings contradict each other | Spawn reconciliation wave with DEBATE formation | MAX_RESEARCH_WAVES |
| All findings `confidence: high` AND no open questions | Advance to PLAN | — |
| MAX_RESEARCH_WAVES reached | Advance to PLAN with documented uncertainty | — |

ORC classifies open questions as: `answered_elsewhere` (skip), `needs_investigation` (shard), `deferred` (log).

**Proven pattern**: 3-wave research (discovery -> deep-dive -> synthesis) consistently produces actionable plans. Wave 1: broad parallel exploration. Wave 2: targeted deep-dives on Wave 1 findings. Wave 3: synthesis shard that reads ALL prior outputs — MUST NOT be parallelized.

---

## GATES

```
VALIDATE/DELIVER boundary?
+-- YES -> FULL GATE (>=quorum judges, pairwise+rubric)
+-- NO -> PLAN/REVIEW decision?
        +-- YES -> LIGHT GATE (2 judges, rubric only)
        +-- NO -> Quality contested?
                +-- YES -> SPAWN CRITIC
                +-- NO -> NO GATE (checkpoint only)
```

Rubric: correctness 35, tests 20, security 15, performance 10, maintainability 10, completeness 10.
Pass: `consensus >= quorum` AND `correlation >= CORRELATION_MIN`.
Fail: document reasons -> revert to prior phase -> retry gate. Two consecutive failures -> escalate to user.

---

## PHASE REVERSION

Agents SHOULD revert to earlier phases when implementation reveals structural gaps. Reverting early prevents workarounds that compound technical debt.

### When to Revert vs Push Through

| Transition | Revert When | Push Through When |
|------------|-------------|-------------------|
| IMPLEMENT -> PLAN | Module boundaries need redesign; approach conflicts with plan | Local workaround not affecting other modules |
| IMPLEMENT -> RESEARCH | Technical approach based on incorrect assumptions | Rare — indicates significant planning gap |
| VALIDATE -> IMPLEMENT | Test failures reveal design flaw (not just a bug) | Implementation bugs fixable in-phase |
| VALIDATE -> PLAN | Test strategy itself is wrong | Test execution failures, not strategy flaws |
| REVIEW -> IMPLEMENT | Review requires structural changes beyond a patch | Minor fixes or cosmetic improvements |

### Refactoring During Implementation

When shards discover structural impediments, classify immediately:

|  | Local (no interface change) | Architectural (changes shared interface) |
|---|---|---|
| **Blocking** (shard cannot complete) | Inline refactor. Separate commit. | Create prerequisite PRD. Phase revert to PLAN. |
| **Deferrable** (shard can complete) | P2 TODO or QOL fix if <10 lines. | Create P2-P3 PRD. Add to roadmap backlog. |

---

## ADAPTIVE PLANNING

`reports/plan.md` is NOT frozen. Update when new info invalidates assumptions, scope changes >20%, approach fails, or user feedback.

| Trigger | Action |
|---------|--------|
| Blocker | STOP -> update plan -> may revert to PLAN |
| Scope +20% | Pause -> update -> confirm with user |
| Failure | Document -> plan alternative |

When updating plan: add `## Revision [N]`, document change/why/impact, log to events.jsonl.

---

## MCP TOOLS (trw-mcp)

When `MCP_MODE: tool`, ORC SHOULD use these tools instead of manual equivalents. When `MCP_MODE: manual`, use inline bash/YAML fallbacks described in each section.

### Ceremony (Session Lifecycle)

**`trw_session_start()`** — MUST call as first action in every session.
Combines `trw_recall('*', min_impact=0.7)` + `trw_status()` into a single call.
Returns: high-impact learnings from `.trw/`, active run state (if any), error list.
Skipping this causes the server to prepend warnings to every subsequent tool response.

**`trw_deliver(run_path?, skip_reflect?, skip_index_sync?)`** — MUST call at task completion.
Batched delivery ceremony: reflect -> checkpoint -> claude_md_sync -> index_sync -> auto_progress.
Each sub-operation runs independently — failures in one step do not block others.

### Engineering Memory

**`trw_recall(query, tags?, min_impact?, status?, max_results?, compact?)`** — SHOULD call before new tasks, at PLAN start.
Searches `.trw/learnings/` by keyword match. Use `"*"` to list all (auto-enables compact mode).
Returns: matching learnings ranked by utility score (Q-learning + Ebbinghaus decay).

**`trw_learn(summary, detail, tags?, evidence?, impact?)`** — SHOULD call on errors, discoveries, gotchas.
Records a learning entry to `.trw/learnings/`. Impact score 0.0-1.0 controls future recall ranking.

**`trw_claude_md_sync(scope?, target_dir?)`** — MUST call at DELIVER.
Promotes high-impact learnings to the `<!-- trw:start -->` section in CLAUDE.md.

### Run Orchestration

**`trw_init(task_name, objective?, config_overrides?, prd_scope?, run_type?, task_root?)`** — MUST call to bootstrap a new task.
Creates `.trw/`, run directories, `run.yaml`, `events.jsonl`, and `FRAMEWORK_SNAPSHOT.md`.
`run_type`: `"implementation"` (default) or `"research"` (skips PRD enforcement).

**`trw_status(run_path?)`** — SHOULD call when resuming, for progress checks.
Returns current run state: phase, confidence, event count, wave progress, reversion metrics.

**`trw_checkpoint(run_path?, message?)`** — SHOULD call every milestone or ~10 minutes.
Appends atomic state snapshot to `checkpoints.jsonl`. Provides resume safety.

### Requirements (AARE-F)

**`trw_prd_create(input_text, category?, priority?, title?, sequence?)`** — SHOULD call at RESEARCH/PLAN.
Generates an AARE-F-compliant PRD. Categories: CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX.

**`trw_prd_validate(prd_path)`** — MUST pass before IMPLEMENT.
Validates PRD against AARE-F quality gates: completeness, ambiguity, traceability, density.

### Build Verification

**`trw_build_check(scope?, run_path?, timeout_secs?)`** — MUST call at VALIDATE before delivery.
Runs pytest and/or mypy via subprocess, caches results to `.trw/context/build-status.yaml`.
Scopes: `"full"` (pytest + mypy), `"pytest"`, `"mypy"`. Max timeout: 600s.

### MCP Resources

| URI | Description |
|-----|-------------|
| `trw://config` | Current TRWConfig values |
| `trw://framework` | Bundled FRAMEWORK.md text |
| `trw://learnings` | Learning index from .trw/ |
| `trw://patterns` | Discovered patterns index |
| `trw://run-state` | Current run state (latest run.yaml) |

### Tool & Skill Lifecycle

```
Session start -> trw_session_start()
Sprint start  -> /sprint-init "name"                  [creates sprint doc + run]
Requirements  -> /prd-new "feature"                   [skeleton PRD]
              -> /prd-groom PRD-ID                    [sprint-ready quality]
              -> /prd-review PRD-ID                   [quality gate]
Work phase    -> trw_checkpoint(message)              [periodic]
              -> trw_learn(summary, detail)           [on discoveries]
              -> /test-strategy                       [coverage audit]
Validation    -> trw_build_check(scope="full")
Delivery      -> /deliver                             [build gate + ceremony]
Sprint end    -> /sprint-finish                       [validates + archives]
```

Quick tasks (no run directory needed):
```
trw_session_start() -> work -> trw_learn() [if discovery] -> trw_deliver()
```

### Fallback Rules

- If a tool fails at runtime, fall back to manual bash/YAML equivalent and log the error.
- `MCP_MODE: manual` -> write learnings directly to CLAUDE.md, write events to `events.jsonl` via bash.

---

## SKILLS & AGENTS

Skills are user-invocable workflows (`.claude/skills/`) that cost 0 tokens until triggered. Agents are specialized sub-agents (`.claude/agents/`) spawned via Task(). ORC SHOULD use skills as the standard way to perform phase operations.

### Skill-Phase Mapping

| Skill | Phase | Mode | What It Does |
|-------|-------|------|--------------|
| `/sprint-init` | PLAN | inline | Survey draft PRDs, create sprint doc, bootstrap run |
| `/prd-new` | PLAN | inline | Generate AARE-F PRD from feature description |
| `/prd-groom` | PLAN | inline | Research + draft to bring PRD to sprint-ready (>=0.85) |
| `/prd-review` | PLAN | fork->agent | Read-only 5-dimension quality review (READY/NEEDS WORK/BLOCK) |
| `/test-strategy` | IMPLEMENT | inline | Audit coverage gaps, suggest targeted tests |
| `/deliver` | DELIVER | inline | Build gate + `trw_deliver()` in one step |
| `/sprint-finish` | DELIVER | inline | Validate PRDs, build gate, archive sprint doc, deliver |
| `/memory-audit` | ANY | inline | Read-only learning health report |
| `/memory-optimize` | REVIEW | inline | Prune stale learnings, consolidate duplicates |
| `/framework-check` | ANY | inline | Ceremony compliance, run health, version check |

### When to Use Skills vs Raw Tools

| Operation | Use Skill | Use Raw Tool |
|-----------|-----------|-------------|
| Create a PRD | `/prd-new` | `trw_prd_create` only if skill unavailable |
| Validate a PRD | `/prd-review` | `trw_prd_validate` for quick score only |
| End a session | `/deliver` | `trw_deliver()` for ceremony-only (no build gate) |
| Start a sprint | `/sprint-init` | `trw_init()` for run bootstrap without sprint context |
| Record a learning | — | `trw_learn()` always (no skill needed) |
| Check status | `/framework-check` | `trw_status()` for run state only |

### Agents

| Agent | Purpose | Spawned By |
|-------|---------|------------|
| `requirement-reviewer` | PRD quality review (5-dimension scoring) | `/prd-review` skill (fork) |
| `prd-groomer` | Research + draft PRD sections to target quality | `/prd-groom` or Task() |
| `requirement-writer` | Draft EARS-compliant FR/NFRs with confidence scores | Task() |
| `traceability-checker` | Grep-based bidirectional traceability verification | Task() |
| `code-simplifier` | Simplify code (10 preservation rules, trw-simplify skill) | Task() |

### Automatic Invocation Rules

Skills MUST be invoked at phase boundaries. ORC MUST NOT perform these operations manually when the skill is available.

| Phase Transition | ORC MUST Invoke | Instead of Manual |
|-----------------|-----------------|-------------------|
| Entering PLAN (new sprint) | `/sprint-init` | Manual `trw_init` + ad-hoc sprint doc |
| Creating requirements | `/prd-new` | Manual `trw_prd_create` without context check |
| Pre-IMPLEMENT quality gate | `/prd-review` | Manual `trw_prd_validate` without 5-dimension scoring |
| Entering VALIDATE | `/test-strategy` | Ad-hoc test selection |
| Entering DELIVER | `/deliver` | Manual `trw_build_check` + `trw_deliver` separately |
| Closing sprint | `/sprint-finish` | Manual PRD status check + archive |
| REVIEW phase (>40 active learnings) | `/memory-optimize` | Manual learning pruning |

Skills are also triggered by:
1. **SessionStart hook** — injects available skills list into every session
2. **Agents with `skills:` field** — preloaded skill content at spawn time
3. **FRAMEWORK.md phase instructions** — agent reads phase table and invokes listed skills

<skill-rules>
- ORC MUST invoke the skill listed in the Phase table when entering that phase
- If a skill fails, ORC MAY fall back to raw MCP tool equivalents
- Skills encapsulate best-practice tool sequences — manual equivalents skip validation steps
</skill-rules>

---

## BOOTSTRAP

<mcp-check>
ORC MUST detect MCP tool availability at bootstrap and set `MCP_MODE` in `run.yaml`:

1. Call `trw_init(task_name=TASK, objective=...)`.
2. If tool returns successfully -> `MCP_MODE: tool`. Init is complete (dirs, run.yaml, events.jsonl, FRAMEWORK_SNAPSHOT all created).
3. If tool unavailable or errors -> `MCP_MODE: manual`. Run manual fallback (see CLAUDE.md for bootstrap script).
</mcp-check>

<bootstrap-rules>
- ORC MUST log `MCP_MODE` at bootstrap — all subsequent sections reference this value
- ORC MUST restore latest `{TASK_DIR}/runs/**` or honor `{RUN_ID}` and recreate scaffolding
- All writes MUST stay within `{REPO_ROOT}/**` and `{TASK_DIR}/**`
- `{RUN_ROOT}/meta/FRAMEWORK_SNAPSHOT.md` = authoritative reference for run
- Worktree strategy: in-repo under `docs/` (portability, single-clone simplicity)
- Run artifacts (`docs/{TASK}/runs/**`, `.ai/**`) MUST NOT be committed
- `docs/documentation/`, `docs/knowledge-catalogue/`, `docs/requirements-aare-f/` SHOULD be committed
</bootstrap-rules>

---

## FORMATIONS

ORC selects formation per wave using the tree below. Inputs: wave purpose, shard count, prior wave confidence.

```
Parallelizable without coordination?
+-- YES -> MAP-REDUCE (shards: ceil(subtasks/3))
+-- NO -> Single synthesis from diverse inputs?
        +-- YES -> PLANNER->EXECUTOR->REFLECTOR (3 shards)
        +-- NO -> Quality critical?
                +-- YES -> DEBATE+CRITIC+JUDGE (4 shards)
                +-- NO -> PIPELINE (min(3, stages))
```

Formation scope: within a single wave. Each wave MAY use a different formation. Formations MUST NOT span waves.

---

## EXPLORATION & PLANNING SHARDS

RESEARCH and PLAN phases MUST use parallel blocking shards and persist all findings incrementally.

### Parallel Exploration

When entering RESEARCH, ORC MUST:
1. Identify independent exploration axes (codebase areas, questions, evidence paths)
2. Launch them as parallel blocking Task() calls in a SINGLE message
3. Each shard writes its findings to disk BEFORE returning

Shard count: `clamp(MIN_SHARDS_FLOOR, axes_of_inquiry, PARALLELISM_MAX)`

### Persisted Findings Format

```yaml
# scratch/shard-{id}/findings.yaml
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

<exploration-rules>
- Shards MUST write `findings.yaml` as their LAST action before returning
- Partial results MUST be written with `status: partial` if shard hits an error or timeout
- ORC MUST read findings from disk (not rely on Task() return text alone) for resume safety
- On resume: scan `scratch/shard-*/findings.yaml` and skip shards with `status: complete`
- Planning shards write to `scratch/shard-{id}/plan_fragment.yaml` (same structure, `phase: plan`)
</exploration-rules>

---

## WAVE ORCHESTRATION

Waves sequence groups of parallel shards with inter-wave data flow. Each wave completes before the next begins.

### Wave Manifest

`shards/wave_manifest.yaml` — each entry: `wave` (1-based), `shards` (IDs), `status` (pending|active|complete|failed|partial), `depends_on` (prior wave numbers).

### Execution Rules

| Rule | Description |
|------|-------------|
| Parallel within wave | All shards in a wave launch as blocking Task() calls in ONE message |
| Sequential between waves | Wave N+1 starts only after wave N status = `complete` |
| Fail-fast | If any shard fails, ORC MUST pause and replan before advancing |
| Manifest update | ORC MUST update `wave_manifest.yaml` status after each wave completes |
| Progress tracking | ORC SHOULD call `trw_status()` for wave progress overview |

### Replanning Triggers

| Trigger | Action |
|---------|--------|
| Shard failure in wave | Pause -> assess -> replan remaining waves |
| New dependency discovered | Insert new wave or merge into existing |
| Scope reduction | Remove unnecessary waves, update manifest |
| All shards independent | Collapse to single wave |

### Resume Protocol

On resume, scan `scratch/shard-*/findings.yaml` and classify shards as complete/partial/failed/not_started. Launch only incomplete shards as parallel blocking Task() calls. Session break loses at most in-flight shards, never completed work.

### Single-Wave Shortcut

When ALL shards are independent, ORC MAY omit `wave_manifest.yaml` and launch all shards directly.

---

## OUTPUT CONTRACTS

Every shard declares what it will produce (`output_contract`: `file`, `schema` with `keys`/`required`, `optional_keys`). ORC validates after each wave.

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
3. Task has >=2 independent subtasks identifiable before execution
4. Parent shard can define output contracts for each child

Depth 0 = ORC-spawned, 1 = child, 2 = grandchild. Hard ceiling: 3.

<shard-rules>
- Child shards MUST be launched as blocking Task() calls
- Parent MUST wait for all children before writing its own output
- If any child fails, parent MUST handle (retry, replan, or fail with partial)
- At hard ceiling, shard MUST NOT self-decompose regardless of card settings
</shard-rules>

---

## PARALLELISM

Heuristic: if shards are independent (<=5% file overlap), spawn `clamp(MIN_SHARDS_FLOOR, axes_of_work, PARALLELISM_MAX)`. Default: 3. Trivial tasks: 1.

| Mode | Allowed |
|------|---------|
| Parallel blocking shards (multiple Task() in ONE message) | REQUIRED when independent |
| Background Bash (`&` tracked in events.jsonl) | ALLOWED |

<parallelism-rules>
- Every Task() call MUST block (omit `run_in_background` or set `false`). WHY: background agents lose MCP tools, cause token explosion (30-50K+), context staleness, file lock deadlocks.
- Self-check before every Task(): "Will I wait for this result before my next action?" YES = correct.
- Before launching N parallel shards, ORC SHOULD test ONE shard first to validate prompt quality and tool access.
</parallelism-rules>

---

## REQUIREMENTS

<pre-development>
Before IMPLEMENT, verify:
1. Source identified (PRD, issue, request)
2. Acceptance criteria in `plan.md`
3. Each REQ has: ID, criterion, verification method
4. Refactor prerequisites MUST be identified and addressed BEFORE feature work
</pre-development>

<post-development>
Before DELIVER, verify requirements traceability: each REQ maps to implementation files and test files with PASS status.
</post-development>

### AARE-F Workflow

When AARE-F framework file exists, ORC SHOULD use skills for the full PRD lifecycle:

```
/prd-new "feature description"     -> skeleton PRD (RESEARCH/PLAN)
/prd-groom PRD-ID                  -> sprint-ready quality (PLAN)
/prd-review PRD-ID                 -> quality verdict (PLAN, pre-IMPLEMENT)
```

Fallback: `trw_prd_create` + `trw_prd_validate` directly when skills are unavailable.
`trw_prd_validate` MUST pass before IMPLEMENT regardless of method.

---

## TDD & CODE QUALITY

<tdd-rules>
- Non-trivial code MUST have tests first
- `src/**` changes without `tests/**` -> validation MUST fail (exception: whitespace, comments, docs only)
- Coverage: global >=85%, diff >=90%
- Structured logging: JSONL with `ts`, `level`, `component`, `op`, `outcome`. Redact secrets and PII.
- Run `trw_build_check(scope="full")` at VALIDATE and DELIVER
</tdd-rules>

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
| Tool failure | Tool returns error | Retry w/ backoff -> alternate tool -> log events.jsonl |
| Shard timeout | Exceeds 2x expected duration | Halt, commit partial (`status: partial`), decompose smaller |
| Shard file error | Missing input / permission denied | Escalate to ORC -> replan wave |
| Shard network error | API/fetch failure | Retry w/ backoff (3x) -> fail with partial |
| Logic contradiction | Conflicting evidence | Debate+Critic -> judges adjudicate -> fix tests, then code |
| Path breach | Write outside boundary | Halt, log, revert, re-plan |

---

## GIT

```bash
git status -sb
git add <specific-paths>
git commit -m "feat(scope): msg" -m "WHY: rationale" -m "RUN_ID: {RUN_ID}"
git push -u origin "{BRANCH}"
gh pr create --fill --head "{BRANCH}"
```

All file paths MUST be absolute paths derived from TASK_DIR or REPO_ROOT.
Update CHANGELOG.md `[Unreleased]` for user-visible changes at DELIVER.

---

## TURN HYGIENE

Turn start: status (Green|Amber|Red), phase, wave progress, next actions. Turn end: decisions, artifacts modified, next action. Compact format only.

---

## MODEL

- Primary: **Opus 4.6**; child shards (depth >=2) or trivial subtasks MAY use Haiku 4.5 / Sonnet 4.5
- Agents SHOULD act. Chat MUST remain minimal. Artifacts MUST be auditable.

---

## TODO REGISTRY

Use built-in `TaskCreate` / `TaskUpdate` for TODO tracking.

| Priority | Meaning | ORC Action |
|----------|---------|------------|
| P0 | Blocker | Resolve immediately |
| P1 | Important | Becomes shard in next wave if capacity allows |
| P2 | Nice-to-have | Logged, addressed opportunistically or deferred |

---

## SELF-IMPROVEMENT & LEARNING

### Learning Triggers

| Trigger | Action |
|---------|--------|
| Workaround >2 retries | `trw_learn` + write to CLAUDE.md |
| Non-obvious API behavior | `trw_learn` |
| Environment-specific issue | `trw_learn` + root CLAUDE.md |
| Task completion | `/deliver` (build gate + delivery ceremony) |
| Sprint completion | `/sprint-finish` (validates + archives + delivers) |
| Learning layer bloat (>40 active) | `/memory-audit` then `/memory-optimize` |

When `MCP_MODE: manual`, write learnings directly to CLAUDE.md or sub-CLAUDE.md files.

### CLAUDE.md Protocol

- Root: max 200 lines. Sub-CLAUDE.md: max 50 lines, max depth 3.
- Structure: Context, Key Facts, Gotchas, See Also.
- Append (don't rewrite). Date-stamp entries.

<mandatory-reads>
CLAUDE.md MUST be read at: session start, every PLAN phase, after errors, before major refactors.
</mandatory-reads>

---

## ARTIFACT & PROMPT PATTERNS

<prompt-patterns>
| Pattern | Apply To | Why |
|---------|----------|-----|
| YAML over JSON | configs, structured data | 50% fewer tokens |
| XML tags | prompt sections, rules | Claude-trained parsing |
| RFC 2119 caps | requirements (MUST/SHOULD/MAY) | Unambiguous obligation |
| Tables over prose | comparisons, options, lists | Dense + scannable |
| Rules not explanations | constraints, instructions | LLMs don't need "why" |
</prompt-patterns>

<sub-agent-prompts>
Shard prompts use `<context>`, `<task>`, `<output_contract>`, `<constraints>` XML tags.
Inputs as file paths (never inlined). Target: <500 tokens. Output: YAML. Write contract file LAST.

**Prompt quality rule**: Shard prompt quality directly determines output quality. MUST include: specific file paths with line numbers, explicit tool sequences, concrete success criteria.

Sub-agents inherit MCP tool access. Shards MUST use structured file-creation tools (Write tool) rather than shell heredocs — heredocs silently truncate outputs beyond ~500 lines.
</sub-agent-prompts>

---

## FRAMEWORK ADHERENCE

<adherence-triggers>
| Trigger | Action |
|---------|--------|
| Every 5 waves | Re-read framework, log compliance |
| After compact | IMMEDIATELY re-read before work |
| Phase transition | Re-read relevant section |
| Gate failure | Re-read gate requirements |
</adherence-triggers>

### Context Compaction Protocol

On context compact: (1) persist all state to `run.yaml` and critical files, (2) commit green state, (3) reload FRAMEWORK.md + CLAUDE.md, (4) MUST execute full session-start ceremony (`trw_session_start()` or `trw_recall` + `trw_status`), (5) resume from `wave_manifest.yaml`.

### Mid-Stream User Input

| Shard Progress | Action |
|---------------|--------|
| <50% complete | Checkpoint current state, defer shard, address user request |
| >50% complete | Complete current shard, then address user request |
| User request is P0 | Micro-commit if green, rollback if red, then switch immediately |

---

## QOL CHANGES (Opportunistic Cleanup)

Shards MAY fix minor issues (<10 lines, already-open files, obviously correct, no behavior change, <=5% effort). Separate commits. When in doubt -> P2 TODO.

</trw-framework>
