v26.1_TRW — MODEL-AGNOSTIC ENGINEERING MEMORY FRAMEWORK
Slim-Persist | Evidence-First | Harness-Neutral | Client-Portable | Language-Agnostic | Schema-First | Sensible Defaults | MCP-Integrated | Nudge-Aware | Future-Model-Ready
Version date: 2026-07-09 | Model policy: capability-based, never provider-bound

> **v26.1 mandate** — TRW is a method, not a model prompt. It MUST work under any capable coding harness: frontier cloud models, balanced everyday models, local/open-weight models, domain-specialized models, future step-function models, or human-operated CLI workflows. Client-, provider-, and language-specific affordances are optional adapters; the core protocol is phases, evidence, tools, checks, persistence, nudges, and learning. v26.1 refines enforcement honesty (what tools actually gate vs what discipline you must apply yourself), ceremony tiers, context engineering, and autonomous-operation rules.

<trw-framework>

<execution-summary>
## EXECUTION MODEL SUMMARY

**v26.1_TRW | model-agnostic | language-agnostic | 6 phases | 3 ceremony tiers | 4 formations | 3 confidence levels | MCP-first tools | optional skills | optional delegates | adaptive nudges**

Core loop: load memory → understand evidence → plan only as needed → implement → verify with project-native checks → review → deliver.
**Deliver gate (no fourth path)**: call `trw_deliver` only with (1) a recorded passing `trw_build_check`; (2) a durable acceptable-failure record naming the failed check, residual risk, owner, and expiry, passed through `allow_unverified=true` + `unverified_reason`; or (3) an authorized operator/config override recorded with technical rationale. An override permits delivery; it never turns unverified work into verified work.
Ceremony scales by tier — MINIMAL (IMPLEMENT+VALIDATE+DELIVER) / STANDARD (+PLAN+REVIEW) / COMPREHENSIVE (all six); VALIDATE is never skipped (see CEREMONY TIERS).
The method is canonical; TRW MCP tools are its preferred implementation. Client commands, hooks, skills, custom agents, and manual/project-native fallbacks are adapters that MUST preserve the same evidence obligations.
Parallel work is OPTIONAL and harness-dependent. If a client cannot delegate, run the same protocol in one session with smaller checkpoints.
Principles: P1 Evidence > assertion. P2 Prevention > detection. P3 External checks > self-belief. P4 Small context > overloaded context. P5 Coordinate by contracts. P6 PRD-to-code traceability.
Empirical posture (TRW eval corpus, 2026 — qualitative by design; numbers live in the canonical synthesis, never here): the cross-session transfer mechanism is confirmed on purpose-built surfaces, and magnitude is positive on one locally-solvable surface; lift on arbitrary natural tasks is not generally demonstrated. Within-session single-shot lift from ceremony alone is rejected at power. Preserve the strata and caveats in the canonical synthesis. The framework's measured value is persistence + verification, not ritual — shed optional ceremony before shedding evidence.
</execution-summary>

<standards>
RFC 2119/8174: MUST, MUST NOT, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, MAY, OPTIONAL — ALL CAPS only.
</standards>

## READING CONTRACT: METHOD, RUNTIME, ADAPTERS

| Layer | Authority | Interpretation |
|-------|-----------|----------------|
| Method | RFC-keyword requirements in this document | The operating discipline. A warning-only implementation does not make a `MUST` optional. |
| Runtime | Current MCP/tool and project-check behavior | The enforcement boundary. Do not call a rule machine-enforced unless source or an executable probe proves it. |
| Adapter | Hooks, skills, slash commands, client config, delegates | Optional convenience. An adapter may automate the method but MUST NOT redefine it. |

When the layers drift, report the mismatch, follow the higher-authority instruction without inventing enforcement, and repair or backlog the drift. Never use a permissive runtime as evidence that a methodological obligation disappeared; never use normative prose as evidence that a runtime gate exists.

---

## CONFIDENCE

| Level | Evidence Standard | Gate |
|-------|-------------------|------|
| `high` | Direct source evidence + passing verification | Pass |
| `medium` | Plausible source evidence, partial verification, or known residual risk | Review |
| `low` | Assumption, stale memory, unverified output, or conflicting evidence | Block → investigate |

Run confidence = the lowest confidence among active requirements. Do not average away a blocking gap.
Where the project defines an evidence taxonomy (e.g., Observed / Verified / Inferred / Unknown), map: high ≈ Verified; medium ≈ Observed but not independently verified, or Inferred with partial verification; low ≈ Unknown or conflicting. Status reports SHOULD use the evidence taxonomy; run gating uses these three levels. Never collapse Observed into Verified.

---

## PERSISTENCE

When a run directory exists, paths below are relative to `{RUN_ROOT}`. A task for which `trw_init` is legitimately skipped MUST still leave durable completion evidence in the nearest project-native artifact (issue/PR, requested handoff, checked-in report, or final response captured by the harness). No-run work is not exempt from persistence, and it MUST NOT fabricate a run layout that was never initialized.

| File | Update When | Failure |
|------|-------------|---------|
| `{RUN_ROOT}/reports/plan.md` | Plan changes or scope decisions | Block IMPLEMENT for STANDARD+ run-backed work |
| `{RUN_ROOT}/reports/final.md` | Run completes | Block DELIVER for STANDARD+ run-backed work |
| `{RUN_ROOT}/meta/run.yaml` | Phase/status changes | Invalid run state |
| `{RUN_ROOT}/meta/events.jsonl` | Significant event | Lost run audit trail |
| `{RUN_ROOT}/scratch/**/findings.yaml` | Delegate or wave findings | Lost resume point |

Write important state to disk before relying on it. Treat failure of a required persistence surface as a P0 blocker; choose the surface from actual task continuity needs rather than ceremony for its own sake.

## CEREMONY TIERS

`trw_init` classifies each run from complexity signals (or an explicit `complexity_hint`); the tier sets which phases are mandatory. **VALIDATE is never skipped at any tier.** Machine-observable code normally requires tests; analysis, inspection, demonstration, or other project-native evidence may be the correct method for other requirements.

| Tier | Typical Scope | Mandatory Phases | Skipped |
|------|---------------|------------------|---------|
| MINIMAL | trivial: ~1 file, typo-class fix | IMPLEMENT, VALIDATE, DELIVER | RESEARCH, PLAN, REVIEW |
| STANDARD | default for most tasks | PLAN, IMPLEMENT, VALIDATE, REVIEW, DELIVER | RESEARCH |
| COMPREHENSIVE | architectural, cross-package, or P0/P1 risk | all six phases | none |

"STANDARD+" in this document means STANDARD or COMPREHENSIVE. REVIEW is mandatory at STANDARD+ because self-review validates the implementation, not the spec — the implementing agent MUST NOT be the sole reviewer when any independent reviewer is available; when the harness truly cannot delegate, do a cold-context second pass and label it self-review. (The mandate is methodological: machine enforcement warns on a missing review and hard-blocks on a block-verdict review — see GATES.)
Light clients and small/local models keep the same mandatory phases but a reduced ceremony surface (fewer nudges, smaller recall payloads, curated tool presets). The compounding value is persistence, not ritual weight.

Session-type rule: a task with no expected continuity (one-shot, no prior learnings, no run to resume) drops RESEARCH-phase weight and ceremony density — but the tier still follows scope (a one-shot multi-file change is still STANDARD). Cross-session and multi-session work is where persistence can compound; positive evidence is scoped to the surfaces described in the canonical synthesis, not a universal-lift claim.

Ceremony tiers are orthogonal to **trust tiers** (crawl → walk → run): a project accumulates trust with session history, which tunes guardrail strictness and review-sampling policy (advisory metadata — delivery gates still check review evidence at STANDARD+ independently). New projects start at crawl (max guardrails); trust names describe the project's history, not the task's complexity.

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
| REVIEW | Diff inspected against requirements by an independent reviewer (mandatory at STANDARD+; a block-verdict review with critical findings blocks delivery) | `trw_review` artifact + independent reviewer or cold-context pass | 10% |
| DELIVER | Final summary; committed/archived artifacts; learnings preserved; new modules/entry points have a verified consumer or an explicit seam/backlog entry (delivered ≠ wired); `trw_deliver` called | client instruction sync + final checkpoint | 5% |

ORC MUST NOT advance until exit criteria are met. A cap breach triggers scope reduction, re-planning, or escalation with written rationale; it never waives the exit criteria. A ceremony tier may skip a phase only when its tier definition says so. Fix the phase, not the narrative.

Per the READING CONTRACT, phase-gate enforcement defaults to lenient (warn-and-proceed; strict mode is a config opt-in) and the % caps are orchestration targets, not machine limits. The machine-enforced gates live at delivery — the build gate and the review gates (see GATES).

## RIGID / FLEXIBLE TOOL CLASSIFICATION

Rigid tools have zero discretion. Flexible tools MUST happen when their trigger is real.

**Rigid (unconditional):**
- `trw_session_start(query?)` — first TRW action of every session; load memory and active state
- `trw_deliver()` — last TRW action of every session; preserve progress and maintenance state. **Gate (no fourth path)**: (1) a recorded passing `trw_build_check`, OR (2) `allow_unverified=true` with a valid, unexpired acceptable-failure record (`failed_command`, `residual_risk`, `owner`, `expiry_iso`) in `unverified_reason`, OR (3) an authorized operator/config-level override recorded with technical rationale (discouraged outside ceremony-only repositories)
- `trw_build_check(tests_passed, test_count, failure_count, static_checks_clean, scope)` — record observed project-native validation at VALIDATE and before DELIVER after code/test changes; it does not run checks
- `trw_review()` — before DELIVER for STANDARD+ complexity. The tool records an artifact; limited auto scans and empty manual/no-argument passes are stamped `substantive: false` and do not satisfy REVIEW readiness. Evidence comes from supplied reviewer findings, an independent reviewer, or—when independence is unavailable—an explicitly identified cold-context self-pass
- Completion artifacts — before claiming done
- Dirty-workspace check — before staging, committing, or delegating write work

**Flexible (triggered):**
- `trw_checkpoint()` — at milestones and before risky context changes
- `trw_pre_compact_checkpoint()` — when context compaction is imminent; persists a recovery directive the next `trw_session_start` surfaces for you to apply (admin-preset tool — when not exposed, fall back to `trw_checkpoint` with resume notes)
- `trw_learn()` — on non-obvious discoveries, gotchas, or validated patterns
- `trw_learn_update()` — when a prior learning is stale or wrong; update instead of stacking duplicates
- `trw_recall(query)` — at start or before unfamiliar/high-risk areas; prefer narrow queries over wildcard dumps
- Phase reversion — when evidence invalidates the current phase

Do NOT debate rigid tools. Execute or record why the tool was unavailable and use the manual fallback.

---

## GATES

```
VALIDATE/DELIVER boundary? → FULL GATE (tests + rubric + requirement trace; "full"/"light" describe gate depth, not ceremony tiers)
PLAN/REVIEW decision?      → LIGHT GATE (rubric + evidence check)
Quality contested?         → CRITIC / independent reviewer
None of the above          → checkpoint only
```

**Machine-enforced at delivery** (the only gates a tool computes): the build gate blocks missing verification for coding/rca/eval under the default `block_coding` policy while docs/research/planning/unknown remain advisory; hard build or review blocks require the structured acceptable-failure record above; STANDARD+ substantive reviews with `verdict=block` plus critical findings block; integration-review and >5-file/no-substantive-review scope gates block; configured missing-review policy warns or blocks. Empty and limited-scan review artifacts do not satisfy readiness. Everything below is orchestration discipline—apply it yourself; no tool computes it for you.

Manual review rubric: correctness 35, tests 20, security 15, performance 10, maintainability 10, completeness 10.
Multi-reviewer pass: at least `ceil(2n/3)` reviewers/checks support pass, with every critical dissent resolved explicitly. Do not manufacture a correlation statistic from incomparable or too-few judgments. Use independent perspectives, instruct against length preference, and swap comparison order where the format allows. If there is only one reviewer, require explicit evidence and residual-risk notes; single-judge scores are unstable.
Fail: document → revert to prior phase → retry. Two consecutive failures → escalate to user.

---

## SKILLS, COMMANDS, HOOKS, AND CLIENT ADAPTERS

Skills, slash commands, hooks, custom agents, and client config files are adapters. They MAY encapsulate best-practice tool sequences, but they MUST NOT be the only way to perform the work.

Rules:
- Every adapter MUST have a tool/manual equivalent.
- Adapter docs MUST avoid provider-only assumptions unless scoped to that provider's adapter.
- Hooks are advisory unless the runtime explicitly blocks execution.
- Skills are optional entrypoints; direct MCP tools remain canonical.
- Instruction sync targets are profile-driven (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.codex/INSTRUCTIONS.md`, `.cursor/rules/**`, etc.). The framework MUST say "client instruction file" unless a provider-specific adapter is being documented.
- Client profiles tune surface density, never protocol: full-mode clients get hooks, nudges, skills, and the framework reference; light-mode clients (small-context harnesses) get curated tool presets and instruction-file guidance only. The rigid tool set and the deliver gate are identical everywhere — for light clients the generated instruction file IS the protocol carrier, so it MUST state them.

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

Ceremony lifecycle under delegation: each session/connection gets its own run pin — a delegate that needs the parent's run adopts it explicitly (`trw_adopt_run`); long-lived parallel sessions keep pins alive with `trw_heartbeat`. Hand delegates condensed briefs (goal, constraints, output contract, paths to inspect — roughly a few hundred to 2k tokens), never full transcripts: focused context outperforms inherited context, and a reviewer fed a raw trajectory inherits its drift — reviewers get structured summaries plus the diff, not the producer's transcript.

---

## CONTEXT ENGINEERING

Context is a finite working set; treat it as a budget. P4 (small context > overloaded context) operationalized — four failure modes, four levers:

| Failure Mode | What Happens | Primary Lever |
|---|---|---|
| Poisoning | one bad output or untrusted string contaminates downstream reasoning | validate/ground before reuse; quarantine externally-sourced text |
| Distraction | attention dilutes as context grows | compress: summarize, keep file paths not pasted blobs |
| Confusion | irrelevant-but-plausible content steers decisions | select: load only task-relevant evidence |
| Clash | contradictory context resolves unpredictably | reconcile or delete the conflict explicitly; never leave both |

Rules:
- Write durable state to disk (checkpoints, findings, plans) instead of holding it in context — the write/select/compress/isolate levers all start with persistence.
- Sub-agents get condensed briefs, not inherited transcripts (see DELEGATION).
- Prefer narrow `trw_recall` queries over wildcard dumps; recall is token-budgeted by design.
- Tool-definition sprawl measurably costs accuracy — reduced tool presets on small-context clients are a feature, not a limitation.
- Before compaction: `trw_pre_compact_checkpoint` (or `trw_checkpoint` with resume notes). After compaction: reload this framework + the client instruction file, `trw_session_start`, resume from persisted state — never from memory of what you were doing.

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
- The completion artifact names changed paths, requirement outcomes, exact validation results, and residual risks; include a checked-in handoff when the task or user requires continuity.
- Final response distinguishes completed work from remaining risk.

PRD lifecycle is task-dependent. New features and broad behavior changes SHOULD have PRDs. Small fixes MAY use the user request as the governing requirement.

---

## LANGUAGE-AGNOSTIC VALIDATION & CODE QUALITY

<trw-validation-rules>
- Infer the project's language, framework, build system, package manager, and test runner from files and config before choosing commands.
- Non-trivial production behavior SHOULD have tests first or tests in the same commit, using the project's native test framework.
- Production behavior changes without nearby tests or an explicit validation rationale SHOULD fail review, regardless of language.
- Tests MUST match the contract they claim to verify. For behavioral or wiring requirements, assert output values and observable effects on the real path; existence or interaction assertions (`is not None`, `callable`, `assert_called`) are not proxies for behavior. Existence checks are valid when existence/parity is itself the requirement. A test that mocks the primary unit under test verifies the mock, not the real path; disclose and offset such isolation with an appropriate integration check. Every implemented behavior SHOULD have a real-path assertion, and every “verified/implemented” claim SHOULD name evidence that actually exists and passes.
- Coverage, type-safety, lint, formatting, security, and build targets come from package/repo config; do not invent universal percentages or single-language gates.
- Run the narrowest meaningful check first, then broaden before delivery when risk warrants it.
- Record the exact command(s), result, and residual risk with `trw_build_check` after checks run. Build evidence is agent-reported — keep it honest by preserving the raw command and its observable outcome (exit code, failure names), not a paraphrase; misreporting a check is a hard-boundary violation, not an efficiency.
- Build evidence MUST postdate the last change it claims to cover: edit after the check → re-run the check. Stale evidence is no evidence.
- When reporting static/type/lint/schema checks to `trw_build_check`, prefer the language-neutral `static_checks_clean` status. Legacy tool-specific field names are compatibility aliases, not framework concepts.
- Examples are illustrative only: choose the test runner, type checker, linter, formatter, security scanner, and build command declared by the repo you are editing; if no safe command is evident, report that uncertainty instead of inventing one.
</trw-validation-rules>

---

## VERSION CONTROL (GIT ADAPTER)

Use the project's native version-control workflow. The commands below apply only when the project uses Git; otherwise preserve the same invariants (narrow change sets, no destructive shared-state operations, reviewable history) with the active VCS.

In concurrent work, prefer an isolated worktree/index or explicit file ownership. Commit each coherent, focused, green milestone promptly so validated work is not left exposed; “frequent” is a preservation rule, not a commit-count target, and never requires broken, cosmetic, or invented commits.

```bash
git -C "$REPO_ROOT" status --short
git -C "$REPO_ROOT" diff -- <absolute-owned-paths>
git -C "$REPO_ROOT" add -- <absolute-new-paths>
git -C "$REPO_ROOT" diff --cached --name-only
git -C "$REPO_ROOT" diff --cached --check
git -C "$REPO_ROOT" commit --only -m "feat(scope): msg" -m "WHY: rationale" -- <absolute-owned-paths>
```

A path-only commit excludes unrelated staged paths but commits the complete current version of every named tracked file; use it only when you own every current byte. Mixed-ownership files require coordination or an isolated patch/index. In a shared index, verify the staged set immediately before committing and never use a plain commit that can consume another worker's staging.

Standing task authorization covers routine narrow, non-amending commits. Commands that can replace shared files/index/refs or rewrite history require command-specific operator authorization and exclusive ownership: `checkout`/`switch`/`restore`, `reset`, `clean`, `stash`, `rebase`, `merge`, `cherry-pick`, `revert`, `commit --amend`, force-push, `git rm --cached`, and related abort operations. Never use `git add -A`, `git add .`, `git add -u`, `git commit -a`, wildcard staging, or stage credentials, secrets, private runtime state, caches, or other sensitive paths. On an unexpected diff, preserve it and re-establish ownership—do not clean or overwrite it. Commit approved runtime state separately from source when both must be saved.

File paths in commands, logs, and shard outputs MUST be absolute (derived from REPO_ROOT or TASK_DIR) — cwd drifts across tool calls, and relative paths under a drifted cwd have destroyed real work.
Update the project changelog (`[Unreleased]`) for user-visible changes at DELIVER when the project maintains one.

---

## NUDGES AND ADAPTIVE GUIDANCE

Nudges are lightweight, evidence-aware reminders surfaced through MCP responses (the universal channel), hooks, or client adapters (hooks exist on full-mode profiles only). They guide behavior; they are not a substitute for tools, tests, or user instructions.

Rules:
- Nudges MUST be client-, model-, and language-neutral unless emitted by a scoped adapter.
- Nudges SHOULD point to the next concrete action, not shame, block, or over-explain.
- Nudges MUST respect the configured levers: per-pool cooldowns (ignore-count and wall-clock), density bias, character budget, and profile pool weights.
- Nudges SHOULD use current evidence: phase, missing validation, stale checkpoint, relevant learning, dirty workspace, or active PRD.
- Nudges MUST NOT assert completion, test success, security status, or empirical lift without evidence.
- Light profiles MAY suppress ceremony nudges when bootstrap instructions already cover them.
- If repeated nudges are ignored, reduce frequency or change the message; do not escalate into brittle hard blocking unless the adapter explicitly supports it and the risk is high.

Nudge pools (pool emphasis is configurable per client/task profile):

| Pool | Use For | Example Next Action |
|------|---------|---------------------|
| workflow | phase/order gaps | start session, checkpoint, deliver |
| learnings | relevant prior gotchas | recall or apply known pattern |
| ceremony | validation/review/delivery gates | run project-native check, record build result |
| context | scope and evidence hygiene | read source path, shrink prompt, preserve uncertainty |

Nudges are a supported operating-layer mechanism because they can make the right next step cheap across clients. Each nudge MUST remain conditional, configurable, and reversible; profiles MAY suppress it when redundant or empirically harmful. Do not bypass configured nudge infrastructure ad hoc as a “performance optimization”; tune or retire behavior through an explicit, measured design change.

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
- Small-context/local models: keep the rigid set (session_start, build evidence, deliver) and shed optional ceremony first — persistence and recall are where the measured value is; ritual weight is where the measured cost is.
- If a model family needs special prompting, put it in that family adapter, not the core framework.

### Eval and Transfer Discipline

TRW claims MUST be stratified before they are generalized. A result from one solver, harness, benchmark family, or prompt variant is evidence for that slice only.

Required reporting for important framework/prompt/eval changes:
- Show pooled results plus meaningful strata: deep vs shallow adoption, benchmark family, solver/model class, harness/client, and clean vs MCP-filtered runs when applicable.
- Treat knowledge-quality and ceremony metrics as diagnostics, not promotion proof. Outcome gates still require solved-task or acceptance evidence.
- Measurement alignment: before a campaign, verify the primary metric is reachable by the mechanism under test (pre-registered activation check) — measuring a cross-session mechanism on a single-shot surface produces null results by construction.
- An LLM judge used as an evidence gate MUST first be calibrated against a human-graded anchor set; an uncalibrated judge is diagnostics, never promotion proof.
- Track wall time, timeout rate, tool-call overhead, analyzer failures, and parser failures as first-class costs.
- When a solver/model changes, verify the analyzer/scorer/parser path too. Silent analyzer mismatch is a validation failure.
- After repeated non-replication, stop wording tweaks and change the harness, retrieval substrate, ordering, measurement, or task decomposition instead.
- Broad claims require replication across problem shape and capability class. If evidence is mixed, preserve the uncertainty.

---

## AUTONOMOUS & LONG-HORIZON OPERATION

When TRW governs unattended loops or campaigns (repeated deliver → session_start cycles without operator input), five rules prevent the measured loop pathologies:

- **Outcome-gated closure**: a cycle is complete when a declared success criterion measurably moved, or the closure records an explicit rationale for why structural work qualifies. Evidence-of-implementation is not evidence-of-utility; N "PASS" cycles with zero product motion is a defect, not progress.
- **Delivered means wired**: a module, tool, or CLI surface claimed complete MUST have a verified consumer or an explicit seam/backlog entry with an owner and expiry.
- **Un-suppressible escalation**: stop-signals and repeatedly-overridden recommendations MUST escalate on a channel the loop itself cannot disable. A loop that can override its own brakes has no brakes; unattended operation with no configured escalation path is a misconfiguration, not a mode.
- **No self-certification**: the worker that produced a change never owns its completion verdict at STANDARD+ (when the harness truly cannot delegate, a cold-context second pass labeled self-review is the fallback — see CEREMONY TIERS); saturation (consecutive cycles with no new movement) means stop and report, not manufacture work.
- **Gates need recourse**: safety gates SHOULD distinguish BLOCK-class from WARN-class findings and provide an operator-approved appeal path; a gate with a high false-positive rate and no recourse trains the loop to route around it.

---

## SELF-IMPROVEMENT & LEARNING

| Trigger | Action |
|---------|--------|
| Workaround >2 retries | `trw_learn` with root cause and fallback |
| Non-obvious API/runtime behavior | `trw_learn` |
| Environment-specific issue | `trw_learn` + update relevant client instructions if durable |
| Prior learning found stale or wrong | `trw_learn_update` — correct in place, never stack duplicates |
| Task/sprint completion | `trw_deliver` |
| Repeated noisy/duplicate memory | memory audit/optimization workflow when requested |

Memory discipline:
- Engineering knowledge (gotchas, root causes, validated patterns, architecture constraints) → `trw_learn`. Personal/communication preferences → the client's native memory. Episodic "what happened this run" → checkpoints and run artifacts, not learnings.
- Delivery reflection is mandatory output even when it yields no learning; `skip_reflect` is only for a reflection already completed. A clean session is a valid result: do not record routine status or invent improvements to look productive, and keep edits minimal.
- Learnings route to the project tier by default; an opt-in user tier holds machine-local cross-repo knowledge; `trw_recall` federates both.
- Background consolidation (dedup, decay, tier sweeps) runs at delivery — one more reason `trw_deliver` is rigid.

Instruction files should stay short and adapter-specific. Durable knowledge belongs in TRW memory first.

---

## FRAMEWORK ADHERENCE

**This document (`.trw/frameworks/FRAMEWORK.md`) is the methodology TRW tools implement.** Reading it is not optional when the task is non-trivial: without it, tools become disconnected rituals.

| Trigger | Action |
|---------|--------|
| Session start | Read this document before non-trivial edits |
| After compaction/resume | Reload the execution summary, current phase/gate sections, and active client instructions; reload the full document when the task or governing instruction requires it |
| Phase transition | Re-read relevant phase/gate section |
| Before delegation | Re-read Delegation and File Ownership |
| Before delivery | Re-read Rigid Tools, Gates, Requirements, and Git |

On compact: `trw_pre_compact_checkpoint` (or `trw_checkpoint` with resume notes) → commit green work when safe → reload the execution summary + relevant phase/gate sections + active client instructions → `trw_session_start(query=...)` (it replays the recovery directive) → resume from persisted state. Reload the full framework when explicitly required; do not pull 40KB of unrelated detail into a narrow continuation by reflex.

## QOL CHANGES

QOL changes are allowed only when they directly support the current requirement, remain behavior-preserving, and do not expand the validation boundary. Trace them to the task and keep them in a separate diff or commit when practical. Otherwise defer them with evidence; arbitrary line-count or effort percentages are not authorization for extra scope.

---

## END-OF-SESSION REMINDER (terminal constraints decay — this restatement is deliberate)

Before you stop: record applicable project-native validation with `trw_build_check` after the last change → `trw_learn` for non-obvious reusable discoveries → `trw_deliver` under the three-path gate in the EXECUTION MODEL SUMMARY. Unpersisted material progress is invisible to every future session.

</trw-framework>


<!-- GENERATED FILE -- do not edit. Source: framework.source.md. Regenerate: python3 scripts/compile-framework-canons.py --write. compiler_schema=1. -->
