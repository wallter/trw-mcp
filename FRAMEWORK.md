v25_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK
Slim-Persist | Evidence-First | Harness-Neutral | Client-Portable | Language-Agnostic | Schema-First | Sensible Defaults | MCP-Integrated | Nudge-Aware | Future-Model-Ready
Version date: 2026-04-30 | Model policy: capability-based, never provider-bound

> **v25 mandate** — TRW is a method, not a model prompt. It MUST work under any capable coding harness: frontier cloud models, balanced everyday models, local/open-weight models, domain-specialized models, future step-function models, or human-operated CLI workflows. Client-, provider-, and language-specific affordances are optional adapters; the core protocol is phases, evidence, tools, checks, persistence, nudges, and learning.

<trw-framework>

<execution-summary>
## EXECUTION MODEL SUMMARY

**v25_TRW | model-agnostic | language-agnostic | 6 phases | 4 formations | 3 confidence levels | MCP-first tools | optional skills | optional delegates | adaptive nudges**

Core loop: load memory → understand evidence → plan only as needed → implement → verify with project-native checks → review → deliver.
TRW tools are the canonical interface. Client commands, slash commands, hooks, skills, and custom agents are convenience adapters only.
Parallel work is OPTIONAL and harness-dependent. If a client cannot delegate, run the same protocol in one session with smaller checkpoints.
Principles: P1 Evidence > assertion. P2 Prevention > detection. P3 External checks > self-belief. P4 Small context > overloaded context. P5 Coordinate by contracts. P6 PRD-to-code traceability.
</execution-summary>

<standards>
RFC 2119/8174: MUST, MUST NOT, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY, OPTIONAL — ALL CAPS only.
</standards>

<variables>
TASK       := task_short_desc
TASK_DIR   := ./docs/{TASK}
RUNS_ROOT  := ./.trw/runs
RUN_ID     := {utc_ts}-{short_id}
RUN_ROOT   := {RUNS_ROOT}/{TASK}/{RUN_ID}
REPO_ROOT  := $(git rev-parse --show-toplevel)
BRANCH     := feat/{TASK}-{short_id}
ORC        := Orchestrator
</variables>

---

## DEFAULTS

```yaml
PARALLELISM_MAX: 6           # max concurrent delegate shards when the harness supports it
MIN_SHARDS_TARGET: 3         # preferred independent evidence axes for non-trivial work
MIN_SHARDS_FLOOR: 1          # single-session fallback is always valid
CONSENSUS_QUORUM: 0.67       # 2/3 reviewers or checks agree
CORRELATION_MIN: 0.7         # inter-reviewer agreement when multiple reviewers exist
TIMEBOX_HOURS: 8
MAX_CHILD_DEPTH: 1           # avoid recursive delegation unless the user explicitly asks
MAX_RESEARCH_WAVES: 3
```

Defaults are not laws. Use fewer shards when the task is small, the harness cannot delegate, or file ownership would be unclear.

---

## CONFIDENCE

| Level | Evidence Standard | Gate |
|-------|-------------------|------|
| `high` | Direct source evidence + passing verification | Pass |
| `medium` | Plausible source evidence, partial verification, or known residual risk | Review |
| `low` | Assumption, stale memory, unverified output, or conflicting evidence | Block → investigate |

Run confidence = the lowest confidence among active requirements. Do not average away a blocking gap.

---

## PERSISTENCE

| File | Update When | Failure |
|------|-------------|---------|
| `reports/plan.md` | Plan changes or scope decisions | Block IMPLEMENT for STANDARD+ work |
| `reports/final.md` | Run completes | Block DELIVER for STANDARD+ work |
| `meta/run.yaml` | Phase/status changes | Invalid state |
| `meta/events.jsonl` | Significant event | Lost audit trail |
| `scratch/**/findings.yaml` | Delegate or wave findings | Lost resume point |

Write important state to disk before relying on it. Treat persistence failures as P0 blockers unless the task is explicitly throwaway.

---

## PHASES

```
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
```

| Phase | Exit Criteria | Recommended Surface | Cap |
|-------|---------------|---------------------|-----|
| RESEARCH | Source/acceptance identified, evidence paths collected, open questions explicit | `trw_recall`, repo search, read-only delegates | 25% |
| PLAN | Acceptance criteria, change scope, verification method, ownership boundaries | PRD/issue/request + `reports/plan.md` when needed | 15% |
| IMPLEMENT | Changes complete or checkpointed; tests or validation assets updated where warranted | source edits, focused commits | 35% |
| VALIDATE | Targeted tests/checks pass; no known P0; `trw_build_check` recorded | project-native test/lint/type/build/security checks | 10% |
| REVIEW | Diff inspected against requirements; STANDARD+ uses independent review when available | `trw_review` or manual rubric | 10% |
| DELIVER | Final summary, committed/archived artifacts, learnings preserved, `trw_deliver` called | client instruction sync + final checkpoint | 5% |

ORC MUST NOT advance until exit criteria are met OR a cap is exceeded with written rationale. Fix the phase, not the narrative.

### Dynamic Research

After each RESEARCH wave, evaluate findings. If >30% have `open_questions`, run a follow-up wave. If evidence contradicts, run a reconciliation pass with a critic/reviewer. Max: MAX_RESEARCH_WAVES. Wave 3 is synthesis and SHOULD be single-threaded.

---

## RATIONALIZATION WATCHLIST

If you catch yourself thinking any of these, stop and follow the process — these are the thoughts that precede avoidable rework:

| Thought | Why it is wrong | Consequence |
|---------|-----------------|-------------|
| "This is too simple for ceremony" | Small tasks still lose context and repeat known gotchas | No checkpoint → compaction/interruption → rework |
| "I will checkpoint/deliver after this part" | Unpersisted progress is invisible to future sessions | Learning transfer is lost |
| "I already know the codebase" | Prior learnings often contain exact repo gotchas | You rediscover old failures |
| "I can implement directly; delegation is overhead" | Focused review/delegation catches defects when scope is non-trivial | Integration gaps reach VALIDATE |
| "The build check can wait until the end" | Late failures multiply touched files | Rework grows after assumptions harden |
| "The model is stronger now, so process matters less" | Stronger models make larger confident mistakes when evidence is thin | False completion at higher velocity |

---

## RIGID / FLEXIBLE TOOL CLASSIFICATION

Rigid tools have zero discretion. Flexible tools must happen when their trigger is real.

**Rigid (unconditional):**
- `trw_session_start(query?)` — first TRW action of every session; load memory and active state
- `trw_deliver()` — last TRW action of every session; preserve progress and maintenance state
- `trw_build_check()` — record project-native validation at VALIDATE and before DELIVER after code/test changes
- `trw_review()` — before DELIVER for STANDARD+ complexity when the tool is available
- Completion artifacts — before claiming done
- Dirty-workspace check — before staging, committing, or delegating write work

**Flexible (triggered):**
- `trw_checkpoint()` — at milestones and before risky context changes
- `trw_learn()` — on non-obvious discoveries, gotchas, or validated patterns
- `trw_recall()` — at start or before unfamiliar/high-risk areas
- Phase reversion — when evidence invalidates the current phase

Do NOT debate rigid tools. Execute or record why the tool was unavailable and use the manual fallback.

---

## GATES

```
VALIDATE/DELIVER boundary? → FULL GATE (tests + rubric + requirement trace)
PLAN/REVIEW decision?      → LIGHT GATE (rubric + evidence check)
Quality contested?         → CRITIC / independent reviewer
None of the above          → checkpoint only
```

Rubric: correctness 35, tests 20, security 15, performance 10, maintainability 10, completeness 10.
Pass: `consensus >= quorum` AND `correlation >= CORRELATION_MIN` when multiple reviewers exist. If there is only one reviewer, require explicit evidence and residual-risk notes.
Fail: document → revert to prior phase → retry. Two consecutive failures → escalate to user.

---

## PHASE REVERSION

Agents SHOULD revert to earlier phases when evidence reveals structural gaps.

| Transition | Revert When | Push Through When |
|------------|-------------|-------------------|
| IMPLEMENT → PLAN | Module boundaries, contracts, or data model need redesign | Local workaround does not affect shared interfaces |
| IMPLEMENT → RESEARCH | Approach depends on an incorrect or missing fact | Assumption can be verified quickly in phase |
| VALIDATE → IMPLEMENT | Failures show implementation bugs | Test harness/config issue is isolated |
| VALIDATE → PLAN | Test strategy or acceptance criteria are wrong | Tests are correct but code is not |
| REVIEW → IMPLEMENT | Review finds structural or safety issues | Minor docs/style fixes only |

When structural impediments appear:

|  | Local (no interface change) | Architectural (shared interface change) |
|---|---|---|
| **Blocking** | Refactor in a separate commit, then resume | Create/adjust PRD or plan; phase revert |
| **Deferrable** | Log P2 TODO if truly out of scope | Add backlog PRD/debt entry |

---

## ADAPTIVE PLANNING

`reports/plan.md` is a living artifact, not a contract to preserve a bad idea. Update on: new evidence, scope +20%, approach failure, user feedback, validation failure, or ownership conflict. Record what changed, why, and how verification changes.

---

## TRW TOOLS (MCP-FIRST, MANUAL-FALLBACK)

Use MCP tools when available. If MCP is unavailable, use the equivalent file/CLI workflow and record the gap.

| Tool | Phase | Required | What It Does |
|------|-------|----------|--------------|
| `trw_session_start(query?)` | Start | MUST | Recall learnings + check active run state |
| `trw_deliver(run_path?)` | End | MUST | Reflect, checkpoint, sync instructions/index state |
| `trw_recall(query, min_impact?)` | Any | SHOULD | Focused memory search |
| `trw_learn(summary, detail, impact?)` | Any | SHOULD | Persist reusable discoveries |
| `trw_checkpoint(message?)` | Any | SHOULD | Atomic progress snapshot |
| `trw_init(task_name, prd_scope?)` | RESEARCH | TASK-DEPENDENT | Bootstrap a run when ceremony tier requires one |
| `trw_status(run_path?)` | Any | SHOULD | Inspect run state and ceremony health |
| `trw_prd_create(input_text)` | PLAN | TASK-DEPENDENT | Create PRD when feature work needs one |
| `trw_prd_validate(prd_path)` | PLAN | TASK-DEPENDENT | Validate PRD structure/readiness |
| `trw_build_check(scope?)` | VALIDATE | MUST after validation | Record project-native build/test/type/lint/security outcome |
| `trw_review()` | REVIEW | STANDARD+ | Independent rubric review when available |

Lifecycle: `trw_session_start → research/plan as needed → implement + checkpoint/learn → validate with project-native checks + trw_build_check → review when needed → trw_deliver`.

Quick tasks: `trw_session_start → work → targeted project-native validation → trw_learn if discovery → trw_build_check if code changed → trw_deliver`.

---

## SKILLS, COMMANDS, HOOKS, AND CLIENT ADAPTERS

Skills, slash commands, hooks, custom agents, and client config files are adapters. They MAY encapsulate best-practice tool sequences, but they MUST NOT be the only way to perform the work.

Rules:
- Every adapter MUST have a tool/manual equivalent.
- Adapter docs MUST avoid provider-only assumptions unless scoped to that provider's adapter.
- Hooks are advisory unless the runtime explicitly blocks execution.
- Skills are optional entrypoints; direct MCP tools remain canonical.
- Instruction sync targets are profile-driven (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.codex/INSTRUCTIONS.md`, `.cursor/rules/**`, etc.). The framework MUST say "client instruction file" unless a provider-specific adapter is being documented.

---

## BOOTSTRAP

1. Load memory with `trw_session_start(query=TASK_DOMAIN)`.
2. Read the active client instruction file(s) and this framework.
3. If the task needs a run directory, call `trw_init(task_name=TASK, objective=...)`.
4. If tool bootstrap fails, use manual file/YAML fallbacks and log the error.

<bootstrap-rules>
- ORC MUST identify the active client/harness and its available tool surface.
- ORC MUST avoid assuming delegation, hooks, skills, background tasks, or fixed context windows.
- ORC MUST restore latest `{RUNS_ROOT}/{TASK}/**` or honor `{RUN_ID}` when resuming.
- All writes MUST stay within `{REPO_ROOT}/**`, `{TASK_DIR}/**`, and `{RUNS_ROOT}/**` unless the user explicitly expands scope.
- Runtime artifacts (`{RUNS_ROOT}/**`, `.trw/memory/**`, `.trw/context/**`, `.ai/**`) SHOULD NOT be mixed into source commits.
</bootstrap-rules>

---

## FORMATIONS

ORC selects the simplest formation that fits the evidence and harness.

```
Can one session do it safely?
+-- YES → SINGLE-TRACK
+-- NO → Are subtasks independent?
        +-- YES → MAP-REDUCE (parallel delegates if available; sequential otherwise)
        +-- NO → Is there a staged handoff?
                +-- YES → PIPELINE
                +-- NO → Quality/risk contested?
                        +-- YES → DEBATE + CRITIC + JUDGE
                        +-- NO → SMALLER PLAN, then SINGLE-TRACK
```

| Formation | Use When | Output |
|-----------|----------|--------|
| SINGLE-TRACK | Small or tightly coupled task | One diff + validation |
| MAP-REDUCE | Independent evidence axes or file sets | Findings per shard + synthesis |
| PIPELINE | Clear stage handoffs | Stage artifact per handoff |
| DEBATE + CRITIC + JUDGE | Conflicting evidence or high-risk design | Decision record + rejected alternatives |

No formation requires a specific vendor tool. If parallel delegates are unavailable, execute the same shards sequentially and preserve findings.

---

## DELEGATION AND FILE OWNERSHIP

Delegation is an optimization, not a dependency.

Use delegates when:
- The user explicitly asks for parallel/subagent work, OR
- The harness provides safe subagents and the task has independent read-only axes, OR
- STANDARD+ implementation can be split into disjoint file ownership.

Do not delegate when:
- The next critical-path action depends immediately on the result.
- File ownership cannot be made explicit.
- The harness has no reliable way to return results or diffs.
- The task is small enough that coordination dominates.

File ownership rules for delegated write work:
- Each writable file has at most one owner.
- Test files count as owned source files.
- Shared files require a single owner and an interface contract.
- Delegates MUST report changed paths, validation run, and unresolved risks.
- ORC integrates, verifies, and owns the final result.

---

## EXPLORATION & PLANNING

RESEARCH and PLAN SHOULD use independent evidence axes for non-trivial work.

ORC MUST: identify axes → assign or execute shards → persist findings → synthesize into a plan.

Shard count: `clamp(MIN_SHARDS_FLOOR, axes_of_inquiry, PARALLELISM_MAX)`.

Shard output fields:

```yaml
shard_id: string
phase: research|plan|review
status: complete|partial|failed
summary: string
findings:
  - key: string
    detail: string
    evidence: [path-or-command]
    confidence: high|medium|low
open_questions: [string]
files_examined: [path]
```

<exploration-rules>
- Persist findings before returning from a delegate or ending a wave.
- Partial results MUST be labeled `status: partial`.
- ORC reads persisted findings or explicit final outputs, not vibes.
- On resume, skip completed findings and continue incomplete axes.
</exploration-rules>

---

## REQUIREMENTS

Before IMPLEMENT:
- Source identified: PRD, issue, user request, incident, or explicit maintenance objective.
- Acceptance criteria are explicit enough to verify.
- Each requirement has an ID or stable bullet, evidence path, and verification method.
- Refactor prerequisites are addressed before feature code.

Before DELIVER:
- Each requirement maps to implementation files and validation evidence.
- Any deferred requirement is labeled with severity and owner/backlog path.
- Final response distinguishes completed work from remaining risk.

PRD lifecycle is task-dependent. New features and broad behavior changes SHOULD have PRDs. Small fixes MAY use the user request as the governing requirement.

---

## LANGUAGE-AGNOSTIC VALIDATION & CODE QUALITY

<trw-validation-rules>
- Infer the project's language, framework, build system, package manager, and test runner from files and config before choosing commands.
- Non-trivial production behavior SHOULD have tests first or tests in the same commit, using the project's native test framework.
- Production behavior changes without nearby tests or an explicit validation rationale SHOULD fail review, regardless of language.
- Tests MUST verify behavior — assert on output values and observable effects — not mere existence (a symbol is present, `is not None`, `callable`, or a mock `assert_called`). A test that mocks the unit under test verifies the mock, not the code; coverage built on mock-only or existence-only tests is false confidence (it passes while the real path is dead — see VALIDATE blindness). Every requirement claimed done SHOULD have at least one test that exercises the real data path and asserts the produced value, and any "verified/implemented" claim SHOULD name a test that actually exists and passes.
- Coverage, type-safety, lint, formatting, security, and build targets come from package/repo config; do not invent universal percentages or single-language gates.
- Run the narrowest meaningful check first, then broaden before delivery when risk warrants it.
- Record the exact command(s), result, and residual risk with `trw_build_check` after checks run.
- When reporting static/type/lint/schema checks to `trw_build_check`, prefer the language-neutral `static_checks_clean` status. Legacy tool-specific field names are compatibility aliases, not framework concepts.
- Examples are illustrative only: choose the test runner, type checker, linter, formatter, security scanner, and build command declared by the repo you are editing; if no safe command is evident, report that uncertainty instead of inventing one.
</trw-validation-rules>

---

## TOOL RETRY

Max: 3 | Backoff: immediate → 2s+jitter → 4s+jitter → fail + log + alternate path.

Retry only when the failure is plausibly transient. Do not retry deterministic schema/path errors without changing the input.

---

## ERROR HANDLING

Prevention: validate inputs, constrain write paths, set timeouts, request structured outputs, and keep diffs small.

| Scenario | Detection | Recovery |
|----------|-----------|----------|
| Tool failure | Error return / no output | Retry → alternate tool → log |
| Delegate timeout | >2x expected or no heartbeat | Mark partial, shrink scope, continue critical path |
| Logic contradiction | Conflicting evidence | Reconcile with critic/reviewer, then update plan |
| Path breach | Write outside boundary | Halt, revert, replan |
| Validation failure | Test/lint/type failure | Fix in IMPLEMENT or revert phase if criteria wrong |
| Dirty workspace risk | Unexpected modified files | Stop staging; isolate paths; ask if ownership unclear |

---

## GIT

```bash
git status --short
git add <specific-paths>
git diff --cached --check
git commit -m "feat(scope): msg" -m "WHY: rationale"
```

Stage narrowly. Never sweep unrelated dirty files into a commit. Commit runtime state separately from source when both must be saved.

---

## NUDGES AND ADAPTIVE GUIDANCE

Nudges are lightweight, evidence-aware reminders surfaced through MCP responses, hooks, or client adapters. They guide behavior; they are not a substitute for tools, tests, or user instructions.

Rules:
- Nudges MUST be client-, model-, and language-neutral unless emitted by a scoped adapter.
- Nudges SHOULD point to the next concrete action, not shame, block, or over-explain.
- Nudges MUST respect configured density, budget, cooldown, and task profile.
- Nudges SHOULD use current evidence: phase, missing validation, stale checkpoint, relevant learning, dirty workspace, or active PRD.
- Nudges MUST NOT assert completion, test success, security status, or empirical lift without evidence.
- Light profiles MAY suppress ceremony nudges when bootstrap instructions already cover them.
- If repeated nudges are ignored, reduce frequency or change the message; do not escalate into brittle hard blocking unless the adapter explicitly supports it and the risk is high.

Nudge pools:

| Pool | Use For | Example Next Action |
|------|---------|---------------------|
| workflow | phase/order gaps | start session, checkpoint, deliver |
| learnings | relevant prior gotchas | recall or apply known pattern |
| ceremony | validation/review/delivery gates | run project-native check, record build result |
| context | scope and evidence hygiene | read source path, shrink prompt, preserve uncertainty |

Nudges are part of the operating layer because they make the right next step cheap across clients. Keep them short, actionable, and grounded in observable state.

---

## MODEL POLICY

TRW does not require a named model. Select by capability and risk:

| Role | Required Capability |
|------|---------------------|
| Orchestration | strong planning, tool discipline, evidence synthesis |
| Implementation | reliable code edits, tests, local debugging |
| Review/Critic | adversarial reasoning, security awareness, diff inspection |
| Extraction | cheap accurate lookup, schema following |

Rules:
- Never hardcode provider/model names in core framework guidance.
- Do not assume a fixed context window; inspect the current harness or keep prompts small.
- Prefer capability labels (`frontier`, `balanced`, `local-large`, `local-small`) over vendor names in generic configs.
- Stronger models still need explicit evidence, tests, and persistence.
- If a model family needs special prompting, put it in that family adapter, not the core framework.

### Eval and Transfer Discipline

TRW claims MUST be stratified before they are generalized. A result from one solver, harness, benchmark family, or prompt variant is evidence for that slice only.

Required reporting for important framework/prompt/eval changes:
- Show pooled results plus meaningful strata: deep vs shallow adoption, benchmark family, solver/model class, harness/client, and clean vs MCP-filtered runs when applicable.
- Treat knowledge-quality and ceremony metrics as diagnostics, not promotion proof. Outcome gates still require solved-task or acceptance evidence.
- Track wall time, timeout rate, tool-call overhead, analyzer failures, and parser failures as first-class costs.
- When a solver/model changes, verify the analyzer/scorer/parser path too. Silent analyzer mismatch is a validation failure.
- After repeated non-replication, stop wording tweaks and change the harness, retrieval substrate, ordering, measurement, or task decomposition instead.
- Broad claims require replication across problem shape and capability class. If evidence is mixed, preserve the uncertainty.

---

## TODO / DEBT REGISTRY

Use the active client todo system when available; otherwise use a checked-in backlog, PRD, issue, or `reports/plan.md`.

Priority:
- P0: blocks correctness, data loss, security, or delivery truthfulness — resolve immediately.
- P1: needed for current scope quality — next wave or current sprint.
- P2: useful but deferrable — backlog with evidence.

---

## SELF-IMPROVEMENT & LEARNING

| Trigger | Action |
|---------|--------|
| Workaround >2 retries | `trw_learn` with root cause and fallback |
| Non-obvious API/runtime behavior | `trw_learn` |
| Environment-specific issue | `trw_learn` + update relevant client instructions if durable |
| Task/sprint completion | `trw_deliver` |
| Repeated noisy/duplicate memory | memory audit/optimization workflow when requested |

Instruction files should stay short and adapter-specific. Durable knowledge belongs in TRW memory first.

---

## ARTIFACT & PROMPT PATTERNS

| Pattern | Apply To | Why |
|---------|----------|-----|
| Schema over prose | delegate outputs, findings, plans | Parseable and resumable |
| File paths over pasted blobs | prompts and handoffs | Keeps context small and evidence inspectable |
| Small prompts | all harnesses | Survives unknown context limits |
| Requirement IDs | PRDs/issues/tasks | Enables traceability |
| Tables for comparisons | design/review | Dense and scannable |
| Explicit uncertainty | evidence summaries | Prevents false confidence |

<delegate-prompts>
Delegate prompts SHOULD include: context, task, constraints, output contract, and paths to inspect. Keep prompts concise; provide file paths instead of large inline content. Ask for changed paths, validation, and risks in final output.
</delegate-prompts>

---

## FRAMEWORK ADHERENCE

**This document (`.trw/frameworks/FRAMEWORK.md`) is the methodology TRW tools implement.** Reading it is not optional when the task is non-trivial: without it, tools become disconnected rituals.

| Trigger | Action |
|---------|--------|
| Session start | Read this document before non-trivial edits |
| After compaction/resume | Reload this document and active client instructions |
| Phase transition | Re-read relevant phase/gate section |
| Before delegation | Re-read Delegation and File Ownership |
| Before delivery | Re-read Rigid Tools, Gates, Requirements, and Git |

On compact: checkpoint state → commit green work when safe → reload this framework + active client instructions → `trw_session_start(query=...)` → resume from persisted state.

### Mid-Stream User Input

| Progress | Action |
|----------|--------|
| <50% through current shard | Checkpoint, defer shard, address user |
| >50% through current shard | Finish the shard if safe, then address user |
| P0 request | Micro-commit if green or rollback if red; switch immediately |

---

## QOL CHANGES

QOL fixes are allowed when they are <10 lines, already-open files, behavior-preserving, and <=5% of effort. Separate commit when practical. When in doubt, log a P2 item instead.

</trw-framework>
