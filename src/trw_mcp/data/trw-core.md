v18.1_TRW — CLAUDE CODE ORCHESTRATED AGILE SWARM (SHARED CORE)
Slim-Persist | Parallel-First | Formation-Driven | Interrupt-Safe | CLI/TDD | YAML-First | Sensible Defaults | MCP-Integrated

<critical>
## EXECUTION MODEL SUMMARY

**v18.1_TRW | 6 phases | 4 formations | 3 confidence levels | 22 MCP tools**

All Task() calls block. Multiple in a single message = parallel. Background agents are forbidden (see PARALLELISM). WHY: background agents lose MCP tools and cause token explosion.
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

---

<defaults>
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
</defaults>

---

<confidence_routing>
## CONFIDENCE

| Level | AARE-F Equivalent | Gate |
|-------|-------------------|------|
| `high` | ≥85% confidence | Pass |
| `medium` | 70–85% | Review |
| `low` | <70% | Block → Critic |

Shard-to-run rollup: run confidence = lowest shard confidence in active wave.
</confidence_routing>

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

<persistence_rules>
State change persistence protocol:
1. Write the state change to disk
2. Verify the write succeeded
3. Proceed to next operation

WHY: Enables interrupt-safe resume; if the session breaks mid-operation, all completed work is recoverable from disk state.

Treat persistence failures as P0 blockers. WHY: Silent state loss causes cascading corruption in downstream phases.
</persistence_rules>

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
Before advancing to the next phase, ORC MUST verify exit criteria. WHY: Advancing with unmet criteria propagates uncertainty into later phases, where rework costs 3-5x more.
- `MCP_MODE: tool` → call `trw_phase_check(phase_name)` — advances only if `valid: true`
- `MCP_MODE: manual` → ORC checks exit criteria from the table above manually
</phase_transitions>

<phase_rules>
ORC verifies exit criteria before advancing. Advancing with unmet criteria propagates uncertainty into later phases where rework costs 3-5x more. Cap exceeded with rationale is the only exception.
Refine plan until stable — fixing a plan is cheaper than rewriting code.
Two consecutive iterations with <5% findings delta → re-plan or advance to DELIVER.
</phase_rules>

---

## BOOTSTRAP

<mcp_check>
ORC MUST detect MCP tool availability at bootstrap and set `MCP_MODE` in `run.yaml`. WHY: All subsequent sections branch on this value.

1. Call `trw_init(task_name=TASK, objective=...)`.
2. If tool returns successfully → `MCP_MODE: tool`. Init is complete (dirs, run.yaml, events.jsonl, FRAMEWORK_SNAPSHOT all created).
3. If tool unavailable or errors → `MCP_MODE: manual`. Run manual fallback (see CLAUDE.md for bootstrap script).
</mcp_check>

<bootstrap_rules>
- ORC MUST log `MCP_MODE` at bootstrap — all subsequent sections reference this value
- ORC MUST restore latest `{TASK_DIR}/runs/**` or honor `{RUN_ID}` and recreate scaffolding
- All writes MUST stay within `{REPO_ROOT}/**` and `{TASK_DIR}/**`. WHY: Prevents accidental writes to system paths or other projects; containment boundary for audit and cleanup.
- `{RUN_ROOT}/meta/FRAMEWORK_SNAPSHOT.md` = authoritative reference for run
- If `docs/documentation/` or `docs/knowledge-catalogue/` exist → note paths in `run.yaml`
- If AARE-F framework file exists → log location to `run.yaml`, follow for requirements work
- Worktree strategy: in-repo under `docs/` (portability, single-clone simplicity)
- Run artifacts (`docs/{TASK}/runs/**`, `.ai/**`) MUST NOT be committed. WHY: Run artifacts are ephemeral, large, and session-specific; committing them bloats the repo and creates merge conflicts.
- `docs/documentation/`, `docs/knowledge-catalogue/`, `docs/requirements-aare-f/` SHOULD be committed.
</bootstrap_rules>

---

## MCP TOOLS (trw-mcp)

When `MCP_MODE: tool`, ORC and shards use these tools; manual equivalents are fallbacks only when MCP_MODE is manual.
WHY: MCP tools enforce atomic persistence, concurrent safety, and audit trails that manual equivalents cannot guarantee.

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

<mcp_rules>
- Shards inherit MCP_MODE from ORC — do not re-detect
- If a MUST tool fails at runtime, log error via `trw_event` (or events.jsonl) and fall back to manual
</mcp_rules>

---

## SUB-AGENT MCP ACCESS

Sub-agents (shards) inherit MCP tool access from the parent session. All TRW MCP tools are available to shards without re-registration.
Shards MUST call `trw_shard_context` as their first MCP action to obtain run paths, shard ID, and tool guidance.

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
Shard cards define parallel work units. Fields:
`id`, `title`, `wave` (1-based; children inherit parent's wave), `goals`, `planned_outputs`,
`output_contract` (file + schema keys + required + optional_keys),
`input_refs` (file paths from prior waves), `self_decompose` (default: true),
`max_child_depth` (default: 2), `confidence` (high|medium|low).
</shard_card>

---

## PARALLELISM

Heuristic: if shards are independent (≤5% file overlap), spawn `clamp(MIN_SHARDS_FLOOR, axes_of_work, PARALLELISM_MAX)`. Default: 3. Trivial tasks: 1.

| Mode | When |
|------|------|
| Parallel blocking shards (multiple Task() in a single message) | Required when shards are independent. WHY: Serial execution of independent work wastes wall-clock time proportional to shard count. |
| Background Bash (`&` tracked in events.jsonl) | Allowed for non-agent shell commands |

<parallelism_rules>
- Every Task() call MUST block (omit `run_in_background` or set `false`). WHY: background agents lose MCP tools, cause token explosion (30-50K+), context staleness, file lock deadlocks.
- If wave output similarity > CORRELATION_MIN → spawn a dissenting shard in the next wave
- Self-check before every Task(): "Will I wait for this result before my next action?" YES = correct.
</parallelism_rules>

---

## TDD & CODE QUALITY

<tdd_rules>
- Non-trivial code MUST have tests first. WHY: Tests-first (TDD) catches interface mismatches before implementation investment; tests also serve as executable documentation of intent.
- `src/**` changes without `tests/**` → validation MUST fail (exception: whitespace, comments, docs only)
- Coverage: global ≥85%, diff ≥90%
- Structured logging: JSONL with `ts`, `level`, `component`, `op`, `outcome`. Redact secrets and PII before logging.
</tdd_rules>

---

## TESTING STRATEGY

### Test Types

| Type | Scope | Speed | When to Run |
|------|-------|-------|-------------|
| Unit | Single function/class | Fast (<1s) | Every code change |
| Integration | Module boundaries | Medium (<30s) | After feature completion |
| E2E | Full system flow | Slow (>30s) | Pre-merge, CI/CD |

### Phase-Appropriate Testing

| Phase | Testing Activity |
|-------|-----------------|
| IMPLEMENT | Unit tests for changed code; targeted integration tests |
| VALIDATE | Full test suite; coverage check; regression sweep |
| DELIVER | E2E smoke tests; coverage gate (global ≥85%, diff ≥90%) |

### Test Organization Principles

<test_organization_rules>
- Mirror source structure: `src/auth/login.py` → `tests/test_auth_login.py`
- Name tests after behavior: `test_expired_token_returns_401` not `test_token_1`
- Follow AAA pattern: Arrange → Act → Assert (one logical assertion per test)
- One concept per test function — if a test needs multiple unrelated assertions, split it
- Tests are documentation: a new contributor should understand the feature by reading tests alone
</test_organization_rules>

### Test Quality Rules

<test_quality_rules>
- Test behavior, not implementation — mock boundaries, not internals
- Tests must be independent and order-insensitive — no shared mutable state between tests
- Consolidate shared fixtures in `conftest.py` at the appropriate scope level
- Prefer deterministic tests — avoid `time.sleep`, real network calls, or flaky assertions
- Delete tests that test nothing (tautologies) or that duplicate other tests
</test_quality_rules>

### Anti-Patterns

| Anti-Pattern | Why It Fails | Fix |
|---|---|---|
| Sprint/ticket-named files (`test_fix_008.py`) | Undiscoverable, fragments coverage | Name after feature: `test_prd_utils.py` |
| Over-mocking | Tests pass but code breaks | Mock only at system boundaries |
| Testing implementation details | Refactors break all tests | Test inputs → outputs |
| Duplicated fixtures across files | Drift, maintenance burden | Centralize in `conftest.py` |
| Giant test files (>1500 lines) | Hard to navigate, slow feedback | Split by feature area |
| Hardcoded magic values | Tests are opaque | Use named constants or fixtures |

---

<tool_retry>
## TOOL RETRY

```
Max: 3 | Backoff: exponential+jitter
  1: immediate
  2: 2s + jitter(0-2s)
  3: 4s + jitter(0-4s)
  Fail: log events.jsonl, escalate
```
</tool_retry>

---

<error_handling>
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
</error_handling>

---

<turn_hygiene>
## TURN HYGIENE

Turn start: status (Green|Amber|Red), phase, wave progress, next actions. Turn end: decisions, artifacts modified, next action. Compact format only. WHY: Token budget is finite; every chat token displaces a reasoning or code token.
</turn_hygiene>

---

## TODO REGISTRY

Use built-in `TaskCreate` / `TaskUpdate` for TODO tracking (no custom `todo.jsonl`).

| Priority | Meaning | ORC Action at Phase Transitions |
|----------|---------|-------------------------------|
| P0 | Blocker | Blocks current wave — resolve immediately |
| P1 | Important | Becomes shard in next wave if capacity allows |
| P2 | Nice-to-have | Logged, addressed opportunistically or deferred |

Shards SHOULD create a TODO when noticing an improvement outside their scope. WHY: Prevents scope creep within shards while ensuring discovered improvements are not lost.

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

### CLAUDE.md Protocol

- Root: max 200 lines. Sub-CLAUDE.md: max 50 lines, max depth 3.
- Structure: Context, Key Facts, Gotchas, See Also.
- Append (don't rewrite). Date-stamp entries.

<mandatory_reads>
CLAUDE.md MUST be read at: session start, every PLAN phase, after errors, before major refactors.
</mandatory_reads>

---

## ARTIFACT & PROMPT PATTERNS

Generated artifacts (reports, shard cards, sub-agent prompts, plans) follow these conventions. WHY: Consistent artifact format enables automated parsing, cross-shard aggregation, and tooling integration.

<patterns>
| Pattern | Apply To | Why |
|---------|----------|-----|
| YAML over JSON | configs, structured data | 50% fewer tokens |
| XML tags | prompt sections, rules | Claude-trained parsing |
| RFC 2119 caps | requirements (MUST/SHOULD/MAY) | Unambiguous obligation |
| Tables over prose | comparisons, options, lists | Dense + scannable |
| Rules with rationale | constraints, instructions | WHY context improves 4.x compliance |
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
| Every 5 waves | Re-read framework, log compliance. WHY: Context compaction and long sessions cause instruction drift; periodic re-reads counteract attention decay. |
| After compact | Re-read before resuming work |
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
