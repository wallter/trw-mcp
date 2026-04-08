v24.5_TRW — CLAUDE CODE ORCHESTRATED AGILE SWARM
Slim-Persist | Parallel-First | Formation-Driven | Interrupt-Safe | CLI/TDD | YAML-First | Sensible Defaults | MCP-Integrated | Skills-Driven | Agent-Teams
Version date: 2026-04-08 | Model: Opus 4.6

<trw-framework>

<execution-summary>
## EXECUTION MODEL SUMMARY

**v24.5_TRW | Opus 4.6 | 6 phases | 4+5 formations | 3 confidence levels | 11 MCP tools | 10 skills | 10 agents | Agent Teams**

All Task() calls block. Multiple in ONE message = parallel. Background agents = FORBIDDEN.
MCP_MODE: tool → use trw-mcp tools. MCP_MODE: manual → bash fallbacks.
Principles: P1 Behavioral > Structural. P2 Prevention > Detection. P3 External > Internal. P4 Focused Context > Shared. P5 Coordinate > Command. P6 PRD-to-Code Traceability.
Agent Teams: LEAD coordinates teammates via shared task list. Subagents for RESEARCH, Agent Teams for IMPLEMENT+.
</execution-summary>

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

| File | Update When | Failure |
|------|-------------|---------|
| `reports/plan.md` | Plan changes | Block IMPLEMENT |
| `reports/final.md` | Run completes | Block DELIVER |
| `meta/run.yaml` | Phase/status | Invalid state |
| `meta/events.jsonl` | Significant event | Lost audit |
| `shards/wave_manifest.yaml` | Wave status changes | Lost wave state |

Write every state change to disk immediately, verify the write succeeded, then proceed. Treat persistence failures as P0 blockers.

---

## PHASES

```
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
```

| Phase | Exit Criteria | Skills | Cap |
|-------|---------------|--------|-----|
| RESEARCH | plan.md draft, >=3 evidence paths, formation selected. | `/trw-framework-check` | 25% |
| PLAN | Acceptance criteria, shards planned, wave_manifest.yaml created. | `/trw-sprint-init`, `/trw-prd-new`, `/trw-prd-ready` | 15% |
| IMPLEMENT | Shards/waves complete OR checkpointed, tests written. | `/trw-test-strategy` | 35% |
| VALIDATE | Coverage >= target, gates pass, no P0. Run `trw_build_check(scope="full")`. | `/trw-test-strategy` | 10% |
| REVIEW | Critic reviewed, simplifications applied, reflection completed. | `/trw-memory-audit` | 10% |
| DELIVER | PR created OR archived, final.md, CLAUDE.md synced. | `/trw-deliver`, `/trw-sprint-finish` | 5% |

ORC tracks elapsed wall-clock against TIMEBOX_HOURS. ORC MUST NOT advance until exit criteria met OR cap exceeded with rationale. Refine plan until stable — fixing a plan is cheaper than rewriting code.

### Dynamic Research

After each RESEARCH wave, ORC evaluates findings. If >30% have `open_questions`, spawn follow-up wave. If findings contradict, spawn DEBATE reconciliation. Max: MAX_RESEARCH_WAVES. Proven pattern: 3-wave (discovery → deep-dive → synthesis), Wave 3 MUST NOT be parallelized.

---

## RATIONALIZATION WATCHLIST

If you catch yourself thinking any of these, stop and follow the process — these are the exact thoughts that precede ceremony skips:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "This is too simple for ceremony" | Simple tasks compound into gaps when 10 agents skip in parallel | You skip checkpoint → context compacts → you re-implement from scratch |
| "I'll checkpoint/deliver after I finish this part" | Context compaction erases uncheckpointed work permanently | Past agents who skipped trw_deliver lost all session learnings — zero knowledge transfer |
| "I already know the codebase, I don't need trw_recall" | Prior learnings contain gotchas for exactly this area | Sprint 26 had 6 P0/P1 defects from agents who skipped recall |
| "I can implement directly, delegation is overhead" | Subagent implementation has 3x fewer P0 defects than direct | Your focused context is valuable — subagents get deeper context per task |
| "The build check can wait until the end" | Late build failures cascade into multi-file rework | 2x rework when caught at DELIVER vs catching at VALIDATE |
| "This refactor is small, I'll inline it" | Small inlined refactors break file ownership in teams | Creates merge conflicts and unreviewed code in teammate files |

---

## RIGID / FLEXIBLE TOOL CLASSIFICATION

Tools are classified by discretion level. Rigid tools have zero discretion — execute them unconditionally. Flexible tools must happen but you choose the timing.

**Rigid (unconditional — never skip, never rationalize):**
- `trw_session_start()` — always, first action of every session
- `trw_deliver()` — always, last action of every session
- `trw_build_check()` — always at VALIDATE and before DELIVER
- Completion artifacts — always before marking any task complete
- File ownership validation — always before spawning Agent Teams

**Flexible (must happen, you pick when):**
- `trw_checkpoint()` — must happen at milestones, you judge which milestones
- `trw_learn()` — on discoveries, gotchas, errors (you judge significance)
- `trw_recall()` — recommended at start, skippable for repeat-domain work
- Phase reversion — you judge when reversion beats pushing through

Do NOT reason about whether to execute rigid tools. Execute them.

---

## GATES

```
VALIDATE/DELIVER boundary? → FULL GATE (>=quorum judges, pairwise+rubric)
PLAN/REVIEW decision?      → LIGHT GATE (2 judges, rubric only)
Quality contested?         → SPAWN CRITIC
None of the above          → NO GATE (checkpoint only)
```

Rubric: correctness 35, tests 20, security 15, performance 10, maintainability 10, completeness 10.
Pass: `consensus >= quorum` AND `correlation >= CORRELATION_MIN`.
Fail: document → revert to prior phase → retry. Two consecutive failures → escalate to user.

---

## PHASE REVERSION

Agents SHOULD revert to earlier phases when implementation reveals structural gaps.

| Transition | Revert When | Push Through When |
|------------|-------------|-------------------|
| IMPLEMENT → PLAN | Module boundaries need redesign | Local workaround not affecting other modules |
| IMPLEMENT → RESEARCH | Approach based on incorrect assumptions | Rare — significant planning gap |
| VALIDATE → IMPLEMENT | Test failures reveal design flaw | Implementation bugs fixable in-phase |
| VALIDATE → PLAN | Test strategy itself is wrong | Test execution failures |
| REVIEW → IMPLEMENT | Review requires structural changes | Minor fixes or cosmetic improvements |

When shards discover structural impediments:

|  | Local (no interface change) | Architectural (changes shared interface) |
|---|---|---|
| **Blocking** | Inline refactor. Separate commit. | Create prerequisite PRD. Phase revert. |
| **Deferrable** | P2 TODO or QOL fix if <10 lines. | Create P2-P3 PRD. Add to backlog. |

---

## ADAPTIVE PLANNING

`reports/plan.md` is NOT frozen. Update on: new info invalidating assumptions, scope +20%, approach failure, user feedback. Add `## Revision [N]`, document change/why/impact, log to events.jsonl.

---

## MCP TOOLS (trw-mcp)

When `MCP_MODE: tool`, use these instead of manual equivalents. When `MCP_MODE: manual`, use bash/YAML fallbacks.

| Tool | Phase | Required | What It Does |
|------|-------|----------|--------------|
| `trw_session_start()` | Start | MUST | Recall learnings + check run status |
| `trw_deliver(run_path?)` | End | MUST | reflect → checkpoint → claude_md_sync → index_sync |
| `trw_recall(query, min_impact?)` | Any | SHOULD | Search `.trw/learnings/` by keyword |
| `trw_learn(summary, detail, impact?)` | Any | SHOULD | Record learning entry (0.0-1.0 impact) |
| `trw_claude_md_sync(scope?)` | DELIVER | MUST | Promote learnings to CLAUDE.md |
| `trw_init(task_name, prd_scope?)` | RESEARCH | MUST | Bootstrap run directory |
| `trw_status(run_path?)` | Any | SHOULD | Run state, phase, confidence |
| `trw_checkpoint(message?)` | Any | SHOULD | Atomic state snapshot (~10min) |
| `trw_prd_create(input_text)` | PLAN | SHOULD | Generate AARE-F PRD |
| `trw_prd_validate(prd_path)` | PLAN | MUST | PRD quality gate check |
| `trw_build_check(scope?)` | VALIDATE | MUST | Run pytest + mypy |

Lifecycle: `trw_session_start → /trw-sprint-init → /trw-prd-new (auto-chains: groom → review → exec plan) → work + trw_checkpoint + trw_learn → trw_build_check → /trw-deliver → /trw-sprint-finish`

Quick tasks: `trw_session_start → work → trw_learn [if discovery] → trw_deliver()`

If a tool fails, fall back to manual bash/YAML equivalent and log the error.

---

## SKILLS & AGENTS

Skills (`.claude/skills/`) are user-invocable workflows costing 0 tokens until triggered. Agents (`.claude/agents/`) are spawned via Task(). ORC MUST invoke skills at phase boundaries instead of manual tool sequences.

| Skill | Phase | What It Does |
|-------|-------|--------------|
| `/trw-sprint-init` | PLAN | Survey draft PRDs, create sprint doc, bootstrap run |
| `/trw-prd-new` | PLAN | Create PRD + auto-chain full pipeline (groom → review → exec plan) |
| `/trw-prd-ready` | PLAN | Full PRD lifecycle for existing PRDs (groom → review → exec plan) |
| `/trw-test-strategy` | IMPLEMENT | Audit coverage gaps, suggest targeted tests |
| `/trw-deliver` | DELIVER | Build gate + `trw_deliver()` in one step |
| `/trw-sprint-finish` | DELIVER | Validate PRDs, build gate, archive, deliver |
| `/trw-memory-audit` | ANY | Read-only learning health report |
| `/trw-framework-check` | ANY | Ceremony compliance, run health, version check |
| `/trw-commit` | ANY | Convention-enforced git commit |

| Agent | Model | Purpose |
|-------|-------|---------|
| `trw-lead` | Opus | **Team lead & orchestrator** — manages 6-phase lifecycle, delegates to teammates, enforces quality gates, preserves knowledge. Spawn as team lead for Agent Teams. |
| `trw-implementer` | Sonnet | Production code via TDD, honors interface contracts, file ownership |
| `trw-tester` | Sonnet | Comprehensive tests, >=90% diff coverage, parametrized edge cases |
| `trw-reviewer` | Opus | Adversarial code review + security audit (read-only, rubric-scored) |
| `trw-researcher` | Sonnet | Codebase exploration, evidence gathering, structured findings |
| `trw-requirement-reviewer` | Sonnet | PRD quality review (5-dimension scoring) |
| `trw-prd-groomer` | Sonnet | Research + draft PRD sections to target quality |
| `trw-requirement-writer` | Sonnet | Draft EARS-compliant FR/NFRs |
| `trw-traceability-checker` | Haiku | Bidirectional traceability verification (cost-optimized) |
| `trw-code-simplifier` | Sonnet | Code simplification (10 preservation rules) |

If a skill fails, ORC MAY fall back to raw MCP tools. Skills encapsulate best-practice sequences — manual equivalents skip validation steps.

---

## BOOTSTRAP

1. Call `trw_init(task_name=TASK, objective=...)`.
2. Success → `MCP_MODE: tool`. Init complete (dirs, run.yaml, events.jsonl created).
3. Failure → `MCP_MODE: manual`. Run manual fallback (see CLAUDE.md).

<bootstrap-rules>
- ORC MUST log `MCP_MODE` at bootstrap
- ORC MUST restore latest `{TASK_DIR}/runs/**` or honor `{RUN_ID}` and recreate scaffolding
- All writes MUST stay within `{REPO_ROOT}/**` and `{TASK_DIR}/**`
- Run artifacts (`docs/{TASK}/runs/**`, `.ai/**`) MUST NOT be committed
- `docs/documentation/`, `docs/knowledge-catalogue/`, `docs/requirements-aare-f/` SHOULD be committed
</bootstrap-rules>

---

## FORMATIONS

ORC selects formation per wave. Inputs: wave purpose, shard count, prior wave confidence.

### Shard Formations

```
Parallelizable without coordination?
+-- YES → MAP-REDUCE (shards: ceil(subtasks/3))
+-- NO → Single synthesis from diverse inputs?
        +-- YES → PLANNER→EXECUTOR→REFLECTOR (3 shards)
        +-- NO → Quality critical?
                +-- YES → DEBATE+CRITIC+JUDGE (4 shards)
                +-- NO → PIPELINE (min(3, stages))
```

### Agent Teams Formations

```
Multi-module, cross-layer?
+-- YES → LAYER-SPECIALISTS (1 TM/layer + tester + reviewer)
+-- NO → Single-module, deep?
        +-- YES → MAP-REDUCE (2-3 impl TMs + tester + reviewer)
        +-- NO → Exploratory?
                +-- YES → RESEARCH-SYNTHESIZE (2-4 researchers + synthesizer)
                +-- NO → High-risk?
                        +-- YES → BUILD-REVIEW-ITERATE (2 builders + 2 reviewers)
                        +-- NO → PIPELINE (2-3 TMs in sequence)
```

Shard formation scope: within a single wave. Team formation scope: entire team lifetime, persists across task waves.

---

## AGENT TEAMS

When tasks benefit from independent context windows or peer communication, ORC SHOULD use Agent Teams instead of subagent shards. The `trw-lead` agent (`.claude/agents/trw-lead.md`) encapsulates the full orchestrator protocol — spawn it as team lead via `Task(subagent_type="trw-lead")` for structured multi-agent work, or use its workflow as reference when orchestrating manually.

| Criteria | Use Subagents | Use Agent Teams |
|----------|--------------|-----------------|
| Communication | Results-only | Peer discussion needed |
| Context | Shared with parent | Independent windows |
| Cost sensitivity | Budget-constrained | Quality-prioritized |
| Task coupling | Independent | Interdependent |
| Phase | RESEARCH (always) | IMPLEMENT+ |

### Parallelism Levels

| Level | Mechanism | Notes |
|-------|-----------|-------|
| Subagent | Task() from LEAD | RESEARCH phase, blocking, parallel in ONE message |
| Agent Team | TeamCreate/SendMessage | IMPLEMENT+, independent sessions, shared task list |
| TM Shard | Task() from TM | Max 4 shards, depth 1, blocking |

ALL Task() calls MUST block. NO `run_in_background: true`. TM shards MUST NOT spawn sub-shards.

### Team Lifecycle

1. **SPAWN**: TeamCreate, define roles, spawn via Task(team_name, name, subagent_type)
2. **TASK**: Create shared task list with dependencies (TaskCreate/TaskUpdate addBlockedBy)
3. **WORK**: Teammates claim tasks, work independently, message peers via SendMessage
4. **GATE**: TeammateIdle/TaskCompleted hooks enforce quality (exit 2 = keep working/block)
5. **SYNTHESIZE**: Lead reviews outputs, resolves conflicts
6. **CLEANUP**: shutdown_request to each teammate → TeamDelete

### File Ownership

Prevents the #1 Agent Teams failure: two teammates editing the same file.

- Each file: at most ONE exclusive owner. Zero overlap. Validate before spawn.
- New files: creating TM owns them. Shared files: assign to ONE TM, others message.
- LEAD MUST generate `file_ownership.yaml` during PLAN and validate before TEAM-UP.

### Teammate Playbooks

Each teammate receives a standalone playbook (≤3000 tokens) with: identity/mission, framework essentials, file ownership, tasks with acceptance criteria, interface contracts, shard protocol, coordination rules, output contract schema, quality standards, PRD traceability.

### Session Resume

Agent Teams cannot resume. On resume: read `run.yaml` → `roster.yaml` → `task_plan.yaml` → `scratch/tm-*/result.yaml`, scope new team to incomplete work, regenerate playbooks, NEVER message previous teammates.

<team-rules>
- LEAD MUST stay in delegate mode during IMPLEMENT. LEAD does NOT code.
- Preferred lead: spawn `trw-lead` agent as team lead — it carries the full 6-phase orchestration protocol, delegation rules, quality gates, and knowledge preservation workflow. When the ORC is already an Opus session, it MAY self-orchestrate using the same protocol.
- Teammates read playbooks as FIRST action after spawn.
- 2-5 teammates optimal. Better decomposition > more headcount.
- Reviewer/Auditor: Opus model (`trw-reviewer`), read-only tools, adversarial stance.
- Implementer/Tester: Sonnet model (`trw-implementer`/`trw-tester`), cost-effective execution.
- Researcher: Sonnet model (`trw-researcher`), read-only, evidence-based findings.
- Trivial shards: Haiku model (`trw-traceability-checker`) for simple lookups/extraction.
</team-rules>

---

## EXPLORATION & PLANNING

RESEARCH and PLAN phases MUST use parallel blocking shards with persisted findings.

ORC MUST: identify independent axes → launch parallel blocking Task() in ONE message → each shard writes findings to disk BEFORE returning.

Shard count: `clamp(MIN_SHARDS_FLOOR, axes_of_inquiry, PARALLELISM_MAX)`

Shard output: `scratch/shard-{id}/findings.yaml` — fields: `shard_id`, `phase` (research|plan), `status` (complete|partial|failed), `summary`, `findings[]` (key, detail, evidence, confidence), `open_questions`, `files_examined`.

<exploration-rules>
- Shards MUST write findings as LAST action before returning
- Partial results: `status: partial` on error/timeout
- ORC reads findings from disk (not Task() return text) for resume safety
- On resume: scan `scratch/shard-*/findings.yaml`, skip `status: complete`
</exploration-rules>

---

## PARALLELISM

Heuristic: if shards independent (<=5% file overlap), spawn `clamp(MIN_SHARDS_FLOOR, axes, PARALLELISM_MAX)`. Default: 3. Trivial: 1.

<parallelism-rules>
- Every Task() MUST block. WHY: background agents lose MCP tools, cause 30-50K+ token explosion, context staleness, file lock deadlocks.
- Self-check: "Will I wait for this result before my next action?" YES = correct.
- Test ONE shard first before launching N parallel to validate prompt quality.
</parallelism-rules>

---

## REQUIREMENTS

Before IMPLEMENT: source identified (PRD/issue/request), acceptance criteria in `plan.md`, each REQ has ID + criterion + verification method, refactor prerequisites addressed BEFORE feature work.

Before DELIVER: each REQ maps to implementation files and test files with PASS status.

PRD lifecycle: `/trw-prd-new "feature"` creates a PRD and automatically runs the full pipeline (groom → review → exec plan). For existing PRDs, use `/trw-prd-ready PRD-ID`. Fallback: `trw_prd_create` + `trw_prd_validate`. Validation MUST pass before IMPLEMENT.

**Execution Plans** (generated automatically by `/trw-prd-new` and `/trw-prd-ready`): Decompose FRs into micro-tasks with file paths, test names, and verification commands. Stored at `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`. Consumed by `/trw-sprint-team` and `/trw-team-playbook` during PLAN phase.

---

## TDD & CODE QUALITY

<tdd-rules>
- Non-trivial code MUST have tests first
- `src/**` changes without `tests/**` → validation MUST fail (exception: whitespace/comments/docs only)
- Coverage: global >=85%, diff >=90%
- Structured logging: JSONL with `ts`, `level`, `component`, `op`, `outcome`. Redact secrets/PII.
- Run `trw_build_check(scope="full")` at VALIDATE and DELIVER
</tdd-rules>

---

## TOOL RETRY

Max: 3 | Backoff: exponential+jitter (immediate → 2s+jitter → 4s+jitter → fail+log+escalate)

---

## ERROR HANDLING

Prevention: validate inputs before launch, set timeouts, use output_contract to catch drift early.

| Scenario | Detection | Recovery |
|----------|-----------|----------|
| Tool failure | Error return | Retry → alternate tool → log |
| Shard timeout | >2x expected | Halt, partial commit, decompose smaller |
| Logic contradiction | Conflicting evidence | Debate+Critic → judges → fix tests then code |
| Path breach | Write outside boundary | Halt, log, revert, replan |

---

## GIT

```bash
git add <specific-paths>
git commit -m "feat(scope): msg" -m "WHY: rationale" -m "RUN_ID: {RUN_ID}"
git push -u origin "{BRANCH}"
```

All paths MUST be absolute (TASK_DIR or REPO_ROOT). Update CHANGELOG.md at DELIVER.

---

## MODEL

Primary: **Opus 4.6**. Child shards (depth >=2), trivial subtasks: Haiku 4.5 / Sonnet 4.5.
Agents SHOULD act. Chat MUST remain minimal. Artifacts MUST be auditable.

---

## TODO REGISTRY

Use `TaskCreate` / `TaskUpdate`. P0: resolve immediately. P1: next wave shard. P2: logged, deferred.

---

## SELF-IMPROVEMENT & LEARNING

| Trigger | Action |
|---------|--------|
| Workaround >2 retries | `trw_learn` + CLAUDE.md |
| Non-obvious API behavior | `trw_learn` |
| Environment-specific issue | `trw_learn` + root CLAUDE.md |
| Task/sprint completion | `/trw-deliver` or `/trw-sprint-finish` |
| >40 active learnings | `/trw-memory-audit` |

Root CLAUDE.md: max 200 lines. Sub-CLAUDE.md: max 50 lines, max depth 3.
CLAUDE.md MUST be read at: session start, every PLAN phase, after errors, before major refactors.
THIS FRAMEWORK (`.trw/frameworks/FRAMEWORK.md`) MUST be read at session start. It defines the methodology your tools implement — without it, you have tools but no process.

---

## ARTIFACT & PROMPT PATTERNS

| Pattern | Apply To | Why |
|---------|----------|-----|
| YAML over JSON | configs | 50% fewer tokens |
| XML tags | prompt sections | Claude-trained parsing |
| RFC 2119 caps | requirements | Unambiguous obligation |
| Tables over prose | comparisons | Dense + scannable |

<sub-agent-prompts>
Shard prompts: `<context>`, `<task>`, `<output_contract>`, `<constraints>` XML tags.
Inputs as file paths (never inlined). Target: <500 tokens. Output: YAML. Write contract file LAST.
Sub-agents inherit MCP tools. Use Write tool not heredocs (heredocs truncate >500 lines).
</sub-agent-prompts>

---

## FRAMEWORK ADHERENCE

**This document (`.trw/frameworks/FRAMEWORK.md`) is the methodology your tools implement.** Reading it is not optional ceremony — it is the difference between using tools with purpose and using tools without understanding. Agents who skip reading this document produce work that passes tool checks but misses phase gates, skips formations, ignores exit criteria, and creates rework that costs more than the 500 tokens of reading.

| Trigger | Action |
|---------|--------|
| Session start | Read this entire document before writing any code |
| Every 5 waves | Re-read framework, log compliance |
| After compact | IMMEDIATELY re-read this document before resuming work |
| Phase transition | Re-read relevant section (phases, gates, formations) |
| Before spawning Agent Teams | Re-read Agent Teams and File Ownership sections |

On compact: persist state → commit green → **reload this FRAMEWORK.md** + CLAUDE.md → `trw_session_start()` → resume from `wave_manifest.yaml`.

### Mid-Stream User Input

| Shard Progress | Action |
|---------------|--------|
| <50% | Checkpoint, defer shard, address user |
| >50% | Complete shard, then address user |
| P0 request | Micro-commit if green, rollback if red, switch immediately |

---

## QOL CHANGES

Shards MAY fix minor issues (<10 lines, already-open files, no behavior change, <=5% effort). Separate commits. When in doubt → P2 TODO.

</trw-framework>
