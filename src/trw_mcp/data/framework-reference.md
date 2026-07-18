v26.1_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK
Slim-Persist | Evidence-First | Harness-Neutral | Client-Portable | Language-Agnostic | Schema-First | Sensible Defaults | MCP-Integrated | Nudge-Aware | Future-Model-Ready
Version date: 2026-07-09 | Model policy: capability-based, never provider-bound

<trw-framework>

<execution-summary>
<variables>
REPO_ROOT  := project_root            # resolve once from the active project/VCS; use an absolute path thereafter
TASK       := task_short_desc
TASK_DIR   := {REPO_ROOT}/docs/{TASK} # default; adjust to the project's own layout convention
RUNS_ROOT  := {REPO_ROOT}/.trw/runs
RUN_ID     := {utc_ts}-{short_id}
RUN_ROOT   := {RUNS_ROOT}/{TASK}/{RUN_ID}
BRANCH     := feat/{TASK}-{short_id}  # optional VCS adapter value
ORC        := Orchestrator
</variables>

---

## DEFAULTS

```yaml
MAX_RESEARCH_WAVES: 3        # bounded reconciliation; not a reason to invent extra waves
```

Use the harness's safe concurrency limit, the fewest independent evidence axes that cover the task, and explicit file ownership. A single-session fallback is always valid. Time, cost, and recursion budgets belong to the task/harness rather than universal framework constants.

---

### Inline Comment Markers

A learning can be anchored to the code it describes by leaving a marker in a
comment. When a learning's anchors are re-validated, a marker referencing that
learning's ID is counted as corroborating evidence that the learning is still
live — so a marker keeps a learning from decaying while the code it explains
still exists.

Pattern (`MARKER_PATTERN`, PRD-CORE-111 FR05):

```
mcp\.trw\.recall\(id=([A-Za-z]-[a-zA-Z0-9]{4,8}(?:,[A-Za-z]-[a-zA-Z0-9]{4,8})*)\)
```

Usage — any comment syntax, one or more IDs:

```python
# mcp.trw.recall(id=L-a3Fq)
def handle_webhook(request):  # the retry semantics here are non-obvious
    ...
```

```rust
// mcp.trw.recall(id=L-a3Fq,L-b2Xp)
pub fn reconcile(&self) -> Result<()> { ... }
```

IDs are 4-8 alphanumeric characters after a single-letter prefix, which covers
both the legacy 8-char hex IDs and current 4-char base62 IDs. Comma-separated
lists must not contain spaces. Write a marker when a non-obvious constraint in
the code is explained by a stored learning; do not scatter them decoratively —
a marker that points at a retired learning is worse than no marker.

---

### Dynamic Research

After each RESEARCH wave, evaluate findings. If >30% have `open_questions`, run a follow-up wave. If evidence contradicts, run a reconciliation pass with a critic/reviewer. Max: MAX_RESEARCH_WAVES. Wave 3 is synthesis and SHOULD be single-threaded.

---

## RATIONALIZATION WATCHLIST

If you catch yourself thinking any of these, stop and follow the process — these are the thoughts that precede avoidable rework:

| Thought | Why it is wrong | Consequence |
|---------|-----------------|-------------|
| "This is too simple for fundamentals" | MINIMAL still requires session start, validation, and delivery; checkpoint only when continuity risk makes it useful | Skipped evidence or lost state → rework |
| "I will checkpoint/deliver after this part" | Unpersisted progress is invisible to future sessions | Learning transfer is lost |
| "I already know the codebase" | Prior learnings often contain exact repo gotchas | You rediscover old failures |
| "I can implement directly; delegation is overhead" | Focused review/delegation catches defects when scope is non-trivial | Integration gaps reach VALIDATE |
| "The build check can wait until the end" | Late failures multiply touched files | Rework grows after assumptions harden |
| "I'm done — calling trw_deliver now" (no recorded build evidence) | Deliver-without-evidence is a dominant, repeatedly-measured false-completion pattern in TRW's eval corpus | A "done" claim that is fluent narration over a failed or unrun check |
| "It's implemented — wiring it up can come later" | Code with no consumer is not delivered; existence is not integration | Integration islands: dead modules with `implemented` status |
| "The model is stronger now, so process matters less" | Stronger models make larger confident mistakes when evidence is thin | False completion at higher velocity |

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

Stopping rule: after two consecutive failed reverts on the same failure, or when step output contradicts the plan's structural assumptions, stop replanning — record the contradiction in `meta/events.jsonl` and escalate to the user. Unbounded replan loops either spin forever or stop arbitrarily; neither is evidence-driven.

---

## ADAPTIVE PLANNING

`reports/plan.md` is a living artifact, not a contract to preserve a bad idea. Update on: new evidence, scope +20%, approach failure, user feedback, validation failure, or ownership conflict. Record what changed, why, and how verification changes.
Low-delta stop: two consecutive research/implement iterations with materially no new findings or unchanged results SHOULD trigger an immediate re-plan or DELIVER, not more iteration.

For STANDARD+ scope, structure the plan as three parts — requirements (verifiable acceptance criteria), design (interfaces, decisions, trade-offs), tasks (dependency-ordered, individually verifiable units). The spec-driven split is the convergent industry shape and measurably improves handoff fidelity; one artifact with three sections is fine.

---

## TRW TOOLS (MCP-FIRST, MANUAL-FALLBACK)

The method is canonical; MCP is its preferred TRW realization. If MCP is unavailable, use the equivalent project-native/file workflow and record the gap.

| Tool | Phase | Required | What It Does |
|------|-------|----------|--------------|
| `trw_session_start(query?)` | Start | MUST | Recall learnings + check active run state; surfaces any pre-compaction recovery directive |
| `trw_deliver(run_path?)` | End | MUST | Reflect, checkpoint, sync instructions/index state; launches background memory maintenance |
| `trw_recall(query, min_impact?)` | Any | SHOULD | Focused memory search (federates project + user tiers) |
| `trw_learn(summary, detail, impact?)` | Any | SHOULD | Persist reusable discoveries |
| `trw_learn_update(id, ...)` | Any | SHOULD | Correct or refresh a stale learning instead of duplicating it |
| `trw_checkpoint(message?)` | Any | SHOULD | Atomic progress snapshot |
| `trw_pre_compact_checkpoint()` | Any | SHOULD before compaction † | Persist a recovery directive surfaced by the next session start |
| `trw_init(task_name, prd_scope?)` | RESEARCH | TASK-DEPENDENT | Bootstrap a run; classifies the ceremony tier |
| `trw_adopt_run(run_path)` | Any | HANDOFF † | Take ownership of an existing run (pipeline/map-reduce handoffs, session recovery) |
| `trw_heartbeat()` | Any | PARALLEL † | Keep this session's run pin alive during long parallel work |
| `trw_status(run_path?)` | Any | SHOULD | Inspect run state and ceremony health |
| `trw_prd_create(input_text)` | PLAN | TASK-DEPENDENT | Create PRD when feature work needs one |
| `trw_prd_validate(prd_path)` | PLAN | TASK-DEPENDENT | Validate PRD structure/readiness |
| `trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope)` | VALIDATE | MUST after validation | Record the observed project-native build/test/type/lint/security outcome; does not run checks |
| `trw_review()` | REVIEW | STANDARD+ | Record the review artifact (auto mode is a limited marker scan; manual/no-arg pass is not substantive evidence; pair with an independent reviewer) |
| `trw_instructions_sync()` | DELIVER | SHOULD † | Refresh the client instruction file (also called automatically inside trw_deliver) |

† Admin-preset tools: light-client profiles expose a reduced `standard` preset that omits these — when a tool is not exposed, use the fallback (`trw_checkpoint` for compaction recovery; `trw_deliver` covers instruction sync) and record the gap. The live tool surface is larger still (security, observability, code-intelligence); discover it through the client's tool list. Fewer tool definitions consume less context and can improve tool selection accuracy — reduced presets are deliberate.

Lifecycle: `trw_session_start → research/plan as needed → implement + checkpoint/learn → validate with project-native checks + trw_build_check → review when needed → trw_deliver`.

Quick tasks: `trw_session_start → work → targeted project-native validation → trw_learn if discovery → trw_build_check if code changed → trw_deliver`.

Minimum manual equivalents when a tool is unavailable:

| Method obligation | Manual/project-native evidence |
|-------------------|--------------------------------|
| Start/recall | Read the active instruction file, relevant durable learnings, and any active run/handoff. |
| Checkpoint | Write a timestamped progress/resume record with decisions and residual risk. |
| Build check | Preserve exact command/procedure, result, failures, and scope after the last edit. |
| Review | Record reviewer, inspected scope, findings, verdict, and residual risk; an empty artifact is not review. |
| Deliver | Write completion/handoff evidence, preserve reusable learning, and sync instructions/indexes by the supported project path. |

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

## EXPLORATION & PLANNING

RESEARCH and PLAN SHOULD use independent evidence axes for non-trivial work.

ORC MUST: identify axes → assign or execute shards → persist findings → synthesize into a plan.

Shard count: `min(independent_axes_with_clear_outputs, harness_safe_parallelism)`, with a floor of one. Quality comes from independent evidence and explicit ownership, not from reaching an arbitrary shard count.

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
- If wave outputs repeat materially identical rationales without independent evidence, run a dissenting pass with an altered perspective or formation — convergence without an attempted falsification is a groupthink signal, not a confidence signal.
</exploration-rules>

---

## TOOL RETRY

Retry budgets are ceilings, not required attempts. For plausibly transient
non-TRW operations, use at most three total attempts: the initial attempt,
2s+jitter before attempt two, and 4s+jitter before attempt three; then fail,
record the gap, and use an alternate path when safe.

Bundled helpers use the narrower MCP policy for a failed or unavailable
`trw_*` call: retry once, then record the skipped ceremony step loudly before
continuing. A role-local persistence-critical policy may be stricter and wins.
Do not retry deterministic schema/path errors without changing the input.

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

## TODO / DEBT REGISTRY

Use the active client todo system when available; otherwise use a checked-in backlog, PRD, issue, or `reports/plan.md`.

Priority:
- P0: blocks correctness, data loss, security, or delivery truthfulness — resolve immediately.
- P1: needed for current scope quality — next wave or current sprint.
- P2: useful but deferrable — backlog with evidence.

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

### Mid-Stream User Input

| Input | Action |
|-------|--------|
| Stop, cancel, P0, or explicit switch-now request | Reach the nearest safe state without delaying for shard completion; checkpoint or report partial state and switch immediately. |
| Scope/priority clarification | Acknowledge it promptly, preserve completed work, and change course at the next safe boundary. |
| Non-blocking addition | Record it, finish only the smallest safe unit already in flight, then re-evaluate the plan. |

---

</trw-framework>


<!-- GENERATED FILE -- do not edit. Source: framework.source.md. Regenerate: python3 scripts/compile-framework-canons.py --write. compiler_schema=1. -->
