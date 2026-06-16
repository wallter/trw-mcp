# AI-Augmented Requirements Engineering Framework (AARE-F)

**Version**: 3.1.0
**Last Updated**: 2026-06-10
**Purpose**: Project-agnostic framework for engineering requirements with AI assistance — truthful, verifiable, and aligned to current requirements-engineering standards.
**Research Basis**: ISO/IEC/IEEE 29148:2018 (revision in progress; 2018 remains operative), INCOSE *Guide to Writing Requirements* v4 (2023), EARS (Mavin et al.), 2024–2026 LLM-for-RE literature, and TRW's own empirical findings (eval iterations and the PRD-audit corpus).

> **Companion documents.** AARE-F defines *what a good requirement/PRD is and how to verify it*. [`FRAMEWORK.md`](FRAMEWORK.md) (`v26_TRW`) defines *how work is executed* (the 6-phase RESEARCH→PLAN→IMPLEMENT→VALIDATE→REVIEW→DELIVER model, gates, formations). They are complementary: AARE-F governs the **specification**; FRAMEWORK.md governs the **execution**. Neither restates the other. For how TRW operationalizes AARE-F day-to-day, see `docs/documentation/aare-f-overview.md` (TRW monorepo path — not present in standalone deployments).

> **Operative summary (read this even under context pressure).** The real quality gate is §6.2: independent adversarial review + behavioral tests on real data paths + status truthfulness + recorded build evidence. The validator score (§5) is a drafting aid, not a verdict (§0). The anti-patterns that actually ship defects are A1 (existence ≠ wiring), A3 (`implemented` over stubs), and A4 (self-review only) — check them at test-writing, status-update, and reviewer-assignment time respectively (§7). A PRD without executable ACs is not ready to implement (§9, implementation-start gate).

---

## 0. What This Framework Is — and Is Not

Read this before using any threshold or score below.

**AARE-F is**: a discipline for writing requirements that are unambiguous, singular, verifiable, and traceable; storing them as versioned artifacts (PRDs); and verifying them with evidence rather than assertion. Its durable value is **structural**: a consistent artifact shape makes independent adversarial review tractable and makes requirement→code→test traceability checkable.

**AARE-F is NOT** a quality oracle. The validator's numeric score (Section 5) is a **drafting aid that surfaces missing structure** — it is **not** a predictor of implementation success, and a high score does **not** mean the work is correct. This is an evidence-based position, not modesty:

- No controlled study (internal or published) shows that a higher PRD score predicts fewer defects or less rework. Sub-signals (completeness, low ambiguity, real traceability) track readiness better than the aggregate score.
- Scores are **gameable** by surface formatting (more file references, more section headings, more prose) independent of implementation quality. The validator is hardened against the worst cases but cannot close the gap with text analysis alone.
- The failures that actually ship — *computed-but-discarded values, mocked-away data paths, `status: implemented` over unfinished stubs* — are **invisible to text scoring**. They are caught by **independent adversarial review and behavioral tests**, not by the score.

**Operating rule**: Treat the score as "is this draft structurally ready for review?" Treat **independent review + executable acceptance tests** as the actual quality gate. When the two disagree, trust the evidence, not the number. (Value hierarchy: **Truthfulness > Quality > Knowledge > Velocity**.)

---

## 1. Foundational Principles

| # | Principle | Description |
|---|-----------|-------------|
| **P1** | **Specification Primacy** | The approved spec is the source of truth; code and tests are its expression. A requirement with no passing test is unverified; a change with no requirement is unauthorized scope. (ISO/IEC/IEEE 29148 §5.2; INCOSE GtWR v4.) |
| **P2** | **Traceability First** | Every requirement traces *up* to a need/source and *down* to implementation and verification. Links are created during authoring, not reconstructed later. |
| **P3** | **Human-in-the-Loop** | AI accelerates drafting, analysis, and review; humans own approval, risk acceptance, and delivery. Oversight is a checkpoint with authority, not a rubber stamp. |
| **P4** | **Risk-Based Rigor** | Effort, proof, and review scale with consequence. Not all requirements deserve equal ceremony. |
| **P5** | **Verifiable by Construction** | "Verifiable" means *an automated check exists and passes* — not that the prose sounds testable. Acceptance criteria compile to tests. |
| **P6** | **Evidence Over Assertion** | Quality is demonstrated with artifacts (tests, diffs, traces, independent review), never asserted. Uncertainty is preserved, not averaged away. (TRW PRD-audit corpus; eval governance findings.) |

These mirror FRAMEWORK.md's execution principles (Evidence > assertion; Prevention > detection; External checks > self-belief). AARE-F applies them to the *requirement* artifact specifically.

---

## 2. The Requirement Quality Standard

This is the heart of AARE-F: a concrete, standards-aligned definition of a good requirement. It unifies EARS (phrasing), ISO/IEC/IEEE 29148 (characteristics), INCOSE GtWR v4 (rules), a requirements-smells taxonomy (detection), and executable acceptance criteria (verification).

### 2.1 EARS — Requirement Phrasing Patterns

EARS (Easy Approach to Requirements Syntax; Mavin et al., 2009) remains the dominant structured-NL syntax and is now the native format of spec-driven AI tooling (e.g., Amazon Kiro, GitHub Spec Kit). Every functional requirement SHOULD match one pattern:

| Pattern | Template |
|---------|----------|
| Ubiquitous | `The <system> shall <response>` |
| State-driven | `While <precondition>, the <system> shall <response>` |
| Event-driven | `When <trigger>, the <system> shall <response>` |
| Optional-feature | `Where <feature is included>, the <system> shall <response>` |
| Unwanted-behavior | `If <trigger>, then the <system> shall <response>` |
| Complex | `While <precondition>, when <trigger>, the <system> shall <response>` |

> **Implementation status (TRW reference):** the validator classifies each requirement-like line by EARS pattern (informational `ears_classifications`); a requirement-shaped line that matches no pattern is tagged `non-ears` as an authoring smell. Advisory only — `validation_ears_weight` stays 0.

EARS is sentence-level. It does **not** by itself cover NFR quantification, acceptance-criteria execution, or inter-requirement conflict — those are §2.4–§2.5 and C10.
**Layering**: EARS structures the *requirement sentence*; Given/When/Then (§2.5) structures its *acceptance criteria*. They are complementary levels, not alternatives — one requirement → one EARS sentence → one or more executable ACs. This is also how current spec-driven tooling composes them.

### 2.2 ISO/IEC/IEEE 29148:2018 — Quality Characteristics

Each requirement (individual) and the requirement set (collective) MUST satisfy:

| Level | Characteristics |
|-------|-----------------|
| **Individual** | Necessary · Appropriate · Unambiguous · Complete · Singular · Feasible · Verifiable · Correct · Conforming |
| **Set** | Complete · Consistent · Feasible · Comprehensible · Validatable · Correct (no duplicates, no conflicts, homogeneous language) |

> 29148 is under revision (committee draft in circulation since early 2026); the 2018 edition remains the operative standard — plan a conformance review when the new edition publishes.

### 2.3 INCOSE GtWR v4 (2023) — Rule Families

INCOSE-TP-2010-006-04 operationalizes the ISO characteristics as 42 checkable rules. The families most worth automating:

| Family | Enforces | Examples of what to reject |
|--------|----------|----------------------------|
| Accuracy | Active voice, defined terms | Passive without agent; undefined jargon |
| Non-ambiguity | One reading only | "/", undefined AND/OR precedence, negative phrasing |
| Singularity | One thought per requirement | AND/OR joining independent clauses; parentheticals |
| Completeness | Self-contained | Pronouns (it/they); reliance on a heading for meaning |
| Realism | Achievable | Absolutes: 100%, always, never |
| Quantification | Measurable targets | "fast", "soon", "promptly" without a number/bound |
| Abstraction | Solution-free | "how" baked in where only "what" is justified |
| Uniformity | Consistent terms/units | Same concept named two ways across artifacts |

### 2.4 Requirements Smells — Detection Taxonomy

A *smell* is a lexical/structural signal of likely low quality (Femmer et al.). AARE-F flags twelve categories. Detect with regex + LLM classification; map each to severity and an INCOSE rule:

`subjective language` · `ambiguous adverbs/adjectives` · `weak modal verbs (should/might/may vs shall)` · `passive voice without agent` · `vague pronouns` · `anaphoric ambiguity` · `coordination ambiguity (AND/OR precedence)` · `loopholes/escape clauses` · `non-verifiable terms (robust/scalable without criteria)` · `superlatives` · `negative statements` · `compound requirements`.

> **Implementation status (TRW reference):** the live validator **detects requirement smells** (weak modal, vague adverb, subjective, escape clause, open-ended, superlative, absolute, compound, vague pronoun) and surfaces them as **informational** `smell_findings` — each with a line number, matched text, and a fix suggestion. They are advisory: `validation_smell_weight` stays 0, so smells never change the score. A separate `ambiguity_rate` (a small vague-term ratio) is also reported. *Scoring* smells (non-zero weight) is deliberately deferred — see Section 9 — to avoid destabilizing the calibrated score. Newer external evidence (RE 2025: smell density measurably degrades LLM performance on downstream traceability tasks) strengthens the case for an eventual non-zero weight; revisit at the next corpus calibration — until then the documented smell-count/score confound controls.

### 2.5 Executable Acceptance Criteria

A requirement is "verifiable" (P5) only when at least one **automated** check confirms it. Author ACs in a testable form and bind them to tests:

```
Given <initial context>
When  <trigger / action>
Then  <observable, asserted outcome>
```

- Every functional requirement has ≥1 AC; every `status: implemented` requirement has ≥1 test referencing it that exercises the **real data path** (not a mock of the unit under test).
- Prefer behavioral assertions (the output contains the correct value) over existence assertions (a symbol/string is present). Existence checks prove a name compiled, not that data flowed (see §7, Anti-Pattern A1).
- Tests authored by the same agent in the same session as the implementation are **self-review artifacts** (confidence: medium) until independently reviewed or mutation-tested — they tend to validate the implementation, not the specification (A2, A4).
- For Critical/High-risk requirements, supplement behavioral tests with **sampled mutation testing**: full line/branch coverage is compatible with very low mutation scores, so coverage alone never demonstrates AC satisfaction. LLM-assisted mutant generation is now practical at production scale.
- *Advanced / aspirational*: for high-risk requirements, auto-formalize ACs and check joint satisfiability with a solver (the neuro-symbolic path now appearing in spec-driven IDEs). Reserve for Critical/High risk.

---

## 3. Framework Architecture

```
+------------------------------------------------------------------+
|                      FOUNDATION LAYER                            |
|  [C1: Traceability Infrastructure] [C4: Semantic Infrastructure] |
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      GOVERNANCE LAYER                            |
|  [C2: AI Governance] [C3: Risk Scaling] [C8: Guardrails]         |
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      EXECUTION LAYER                             |
|  [C5: Multi-Agent Orchestration] [C6: Uncertainty] [C10: Conflicts]|
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      OPERATIONS LAYER                            |
|  [C7: Requirements-as-Code] [C9: Observability]                 |
+------------------------------------------------------------------+
```

**Component status legend** (truthfulness — what is real vs. designed-for): **[live]** implemented and load-bearing in the TRW reference; **[partial]** partly implemented; **[guide]** design guidance, not enforced by tooling.

---

## 4. Core Components

### Foundation Layer

#### C1: End-to-End Traceability Infrastructure — [partial]
**Purpose**: Audit, impact analysis, and provenance.
**Key requirements**: stable IDs for artifacts; bidirectional links (`implements / depends_on / enables / conflicts_with`); link-integrity validation on change; requirement→test→code linkage that is **executable** (a failing linked test = a failing requirement, per P5).
**Reference status**: link schema, a traceability matrix, and a traceability *score* are live; coverage as a real percentage and automated impact analysis are not yet first-class. Treat "coverage" qualitatively until measured.

#### C4: Semantic Infrastructure Layer — [guide]
**Purpose**: Semantic search/dedup/novelty as substrate for requirements work.
**Guidance (choose per project, do not hardcode):**
```yaml
embedding:   choose a current model from a public benchmark (e.g., MTEB); fix dimensions/normalization to that choice
store:       any vector store (pgvector, sqlite-vec, FAISS, hosted) — engine is not load-bearing
retrieval:   hybrid lexical+semantic; the blend is regime-dependent — calibrate, do not assume a fixed ratio
chunking:    prefer semantic/structure-aware chunking; ~512 tokens is a reasonable default for dense technical text
dedup:       cosine ≈0.85 is a starting point — calibrate on your corpus
```
**Note**: For fault-localization-style retrieval, max-reciprocal-rank fusion (CombMAX) preserves complementary single-rankers better than RRF-sum on hard tails (TRW retrieval finding) — but this is regime-dependent. Calibrate; don't copy a constant.

### Governance Layer

#### C2: AI-in-the-Loop Governance — [partial]
**Purpose**: Keep humans in authority over AI-assisted decisions.
**Human checkpoints**: input validation → confidence-aware routing → output verification → override authority.

> **Confidence routing — calibration first (revised in v3.0.0).** Earlier versions published fixed percentage bands (">95% automate", etc.). **Do not use fixed verbalized-confidence thresholds.** Calibration research (Appendix B) finds aligned models are frequently miscalibrated — a stated "90%" need not correspond to 90% accuracy, and the size of the gap varies by model and task — so a band tuned on one model/task does not transfer. Measure, don't assume. Instead:
> 1. **Measure** calibration (ECE) on a held-out set for *your* model and task before setting any band.
> 2. Use **temperature scaling** as a cheap baseline correction.
> 3. For factual claims, prefer **semantic-entropy / semantic-energy** signals; for safety-critical routing, prefer **conformal prediction** (distribution-free coverage guarantees).
> 4. Express routing qualitatively (high / moderate / low / insufficient) defined *relative to the calibration curve*, and document the basis of any operational threshold.

**Regulatory alignment** (where applicable): EU AI Act Art. 14 (human oversight), FDA SaMD (decision support, not replacement), ISO/IEC 42001, NIST AI RMF 1.0.

> **Autonomous-loop oversight.** When requirements work runs inside unattended loops, human checkpoints degrade to theater unless escalation is structurally protected: a stop-recommendation overridden repeatedly MUST escalate on a channel the loop cannot disable, and cycle closure MUST be outcome-gated (a declared success criterion moved, or a documented rationale) rather than throughput-gated. A loop that can suppress its own diagnostics inverts the authority hierarchy (P3).

#### C3: Risk-Based Rigor Scaling — [live]
**Purpose**: Scale effort and proof to consequence. This is AARE-F's strongest, fully-implemented component (Section 8 gives the live profiles).

| Level | Documentation | Verification | Review |
|-------|---------------|--------------|--------|
| Critical | Full spec + ACs | Independent V&V | Multi-party |
| High | Structured spec | Peer + automated | Technical lead |
| Medium | Standard template | Self + unit/integration | Team |
| Low | Lightweight notes | Basic validation | Optional |

#### C8: AI Guardrails & Safety — [partial]
**Minimal set**: input sanitizer (prompt-injection detection) · output filter (harmful-content / PII) · tool allowlist · token/cost budgets · immutable audit log · periodic adversarial testing.
**OWASP focus**: for agent-executed workflows treat the **Top 10 for Agentic Applications** (Dec 2025) as primary — goal hijack, tool misuse, identity/privilege abuse, memory/context poisoning, excessive agency — alongside the LLM edition (prompt injection, sensitive-information disclosure, data/model poisoning). Cite the edition you target; prevalence figures are edition- and context-specific — don't quote a bare percentage.
**Requirement-level guardrail**: a requirement that directs an agent to read external content (files, URLs, retrieval results, tool outputs) SHOULD carry an injection-resistance acceptance criterion — indirect injection through content is the production exploit class.

### Execution Layer

#### C5: Multi-Agent Orchestration — [partial]
**Purpose**: Coordinate specialized roles for robustness. Domain-neutral supervisor pattern:
```
                ORCHESTRATOR (coordinator)
                       |
   +---------+---------+---------+---------+
   |         |         |         |         |
 EXPLORE  IMPLEMENT  CRITIQUE  SYNTHESIZE  SHARED
                                          MEMORY
```
This maps onto FRAMEWORK.md formations (SINGLE-TRACK / MAP-REDUCE / PIPELINE / DEBATE+CRITIC+JUDGE). **Decision protocols** — voting (reasoning), consensus (knowledge), majority (latency) — improve robustness; reported accuracy gains vary by task and model, so calibrate rather than quoting a fixed lift.
**Isolation-first**: an independent reviewer/critic MUST NOT inherit the author's context (see §7, A4). First-pass exploration without prior-pattern priming reduces anchoring. Iteration policy for multi-pass review: pass 1 fully independent (no prior context), pass 2 compares against known patterns, pass 3+ targets the specific gaps the earlier passes surfaced. Stop when a pass yields no new material findings.
**Critical-risk requirement sets**: a dialectical pass with NFR-specialized agents (one per quality family — safety, security, performance, …) that must surface and resolve inter-quality conflicts *before* approval has recent empirical support (single-study evidence — calibrate locally); run it as the DEBATE+CRITIC+JUDGE formation with requirements-level roles.

#### C6: Uncertainty Management over Zero-Defects — [guide]
**Paradigm**: "eliminate all hallucination" is unreachable → **quantify and route uncertainty** instead.

| Tier | When | Relative cost | Methods |
|------|------|---------------|---------|
| 1 | Always | low | token probability, source grounding, self-consistency |
| 2 | High stakes | medium | semantic entropy / semantic energy, cross-checker validation, human review |
| 3 | Periodic | high | calibration audits (e.g., LM-Polygraph-style), multi-model spot-checks, conformal prediction |

**Critical warning**: high-confidence hallucinations cannot be caught by entropy alone — external grounding is required.
**On effectiveness numbers**: do not publish bare "N% hallucination reduction" figures (prior versions cited 67%/96% — both unsourced and untransferable). Reported reductions depend entirely on the hallucination definition, model, task, and dataset. State the method and require local measurement.

#### C10: Conflict Detection & Resolution — [partial]
**Conflict types & detection**: intra-requirement (rule-based NLP — high precision when rules are logically sound and maintained; do **not** claim 100%) · inter-domain (multi-criteria analysis) · cross-artifact (semantic comparison) · NFR trade-offs (trade-off catalogue).
**Resolution**: risk-based (higher-risk wins for safety/security) · AHP-TOPSIS (multi-stakeholder) · IBIS (wicked problems).
**Aspirational**: solver-checked joint satisfiability (auto-formalize → SMT) for Critical-risk requirement sets.

### Operations Layer

#### C7: Requirements-as-Code with DevOps — [live]
**Practices**: PRDs as versioned Markdown+YAML in git; semantic versioning + history; PR-based change; schema/consistency checks runnable on demand and in CI.

| Stage | Checks |
|-------|--------|
| Pre-commit | schema validity, required fields, ID uniqueness |
| CI | structure + traceability + (target) executable-AC pass-rate; no unresolved conflicts |
| Delivery | approval recorded, audit log, rollback path, **wiring verified** — a surface marked `implemented` has ≥1 verified consumer (import, registration, route, or end-to-end test) or an explicit seam entry with owner + expiry. Existence without consumption is A1 at architecture scale: delivery criteria that only check "code exists" produce integration islands. |

#### C9: Continuous Observability — [partial]
**MELT**: Metrics (coverage, novelty, AC pass-rate, requirement churn, **false-completion rate** — delivers whose claimed outcome was later contradicted ÷ total delivers) · Events (validated/groomed/delivered) · Logs (validation + decision trail) · Traces (end-to-end flow). Use whatever telemetry stack the project already has (structured logging is the floor; OpenTelemetry/Prometheus are optional, not required).
**Verifier identity**: for P0/P1 requirements whose ACs test code the implementing agent wrote, record *who verified* — self-reported verification is confidence-medium evidence; an independent verifier (or captured raw check output) is required for confidence-high (P3, A4).

---

## 5. The Live Quality Model (Reference Implementation)

This section documents exactly what TRW's `trw_prd_validate` computes today, so the framework never overstates the tooling. **Adapt thresholds to your project; the shape is the portable part.**

### 5.1 Scored dimensions (default, medium-risk)

| Dimension | Weight | What it rewards |
|-----------|-------:|-----------------|
| `traceability` | 35 | real links to source files, tests, dependencies, behavior-proof surfaces |
| `implementation_readiness` | 25 | control points, behavior-switch matrix, key files, proof tests, completion evidence |
| `content_density` | 20 | substance over filler (a hygiene signal, *not* the goal) |
| `structural_completeness` | 20 | required sections + frontmatter present and coherent |

`total_score = (Σ dimension_score / Σ dimension_max) × 100`, capped at 100. Three further dimensions (`smell`, `readability`, `ears_coverage`) carry **weight 0** so they never affect the score. As of v3.0.0, smell and EARS detection ARE computed but surfaced only as informational diagnostics (`smell_findings`, `ears_classifications`, §2.4/§2.1); `readability` remains reserved. `ambiguity_rate` is also reported for information and is **not** part of the score.

### 5.2 Tiers & grades

| Score (medium-risk default) | Tier | Grade |
|---|---|---|
| ≥ 85 | `approved` | A |
| ≥ 60 | `review` | B |
| ≥ 30 | `draft` | D |
| < 30 | `skeleton` | F |

(There is intentionally no "C".) A DRAFT→REVIEW transition additionally requires content density ≥ the risk-scaled minimum (this density floor gates DRAFT→REVIEW only — not REVIEW→APPROVED). Caution: in the TRW config the threshold field names are historically offset by one tier — `validation_review_threshold` sets the **approved** gate (85), `validation_draft_threshold` the **review** gate (60), and `validation_skeleton_threshold` the **draft** gate (30); trust `risk_profiles.py`, not the field name.

### 5.3 Anti-gaming features in the live scorer
File-path grounding penalty (score decays per hallucinated path); density deliberately down-weighted vs. implementation-readiness so prose can't buy a passing score; AI/agentic-PRD operational-evidence scoring. These exist precisely because the score is gameable (Section 0) — they raise the cost of gaming but do not replace independent review.

---

## 6. Quality Gates & Verification

### 6.1 PRD authoring gates (text-level — necessary, not sufficient)
| Gate | Target | Notes |
|------|--------|-------|
| Structure | required sections present | per category variant (see §9) |
| Ambiguity | low | vague-term scan today; full smell taxonomy is backlog |
| Traceability | every FR has source + impl path + test path | links are *claims* until verified by 6.2 |
| Tier | meets risk-scaled threshold | a drafting bar, not a quality verdict |

### 6.2 The real quality gate (evidence-level — the one that counts)
1. **Independent adversarial review** by a reviewer that did *not* author the work (P3, §7-A4). This — not the score — is what catches false completions in practice.
2. **Behavioral / wiring tests** that exercise real data paths for every `status: implemented` FR (§2.5, §7-A1/A2).
3. **Status truthfulness**: `status: implemented` requires the work be functionally live with no undisclosed stubs (§7-A3).
4. **Project-native build/test/type/lint** recorded as evidence before delivery (FRAMEWORK.md VALIDATE gate; `trw_build_check`).

A PRD passes when 6.2 holds — not merely when 6.1's score is high.

---

## 7. Anti-Patterns (including the truthfulness failures that ship)

The first group is classic RE; the second (A1–A5) is the set TRW has learned the hard way — each has caused real shipped defects and is invisible to text scoring.

| Anti-Pattern | Symptom | Mitigation |
|--------------|---------|------------|
| Zero-Hallucination Goal | Paralysis / over-validation | Uncertainty management (C6) |
| Post-Hoc Compliance | Audit failures at the end | Continuous verification in CI |
| Traceability Theater | Frontmatter links exist; FRs don't map to files/tests | Fill behavior-switch matrix + real test refs; verify by execution |
| Confidence Theater | Everything is "high confidence" | Uniform high confidence signals miscalibration; require evidence |
| Historical Anchoring | A stale snapshot overrides the live contract | Canonical doc first, then research/snapshots |

| # | Truthfulness Anti-Pattern | Check when | Why text scoring misses it | Mitigation (the gate that catches it) |
|---|---------------------------|------------|----------------------------|----------------------------------------|
| **A1** | **Existence ≠ wiring** | writing/reviewing tests; declaring a surface done | `grep_present`/symbol checks prove a name exists, not that the computed value reaches the output. Real bugs shipped green: an estimate computed then dropped (`estimate=None`); loaded priors silently discarded. | Behavioral assertions on output *values*; ≥1 test per FR exercising the real path; a consumer or seam for every new surface (C7). |
| **A2** | **Mock-depth blindness** | judging coverage claims | 90%+ coverage with all-green tests can mock away the unit under test; published industrial reports find AI-written tests can score very low on mutation testing despite high coverage. | Forbid mocking the primary unit under test; require an integration test on a real (in-memory) path; sampled mutation testing (§2.5). |
| **A3** | **`implemented` over stubs** | updating any status field | A status field is text; nothing in the score checks runtime completeness. Audits repeatedly find `status: implemented` with non-empty `stubs[]`. | Enforce: `status: implemented` ⇒ functionally live, `stubs: []`; verify in review/CI, not by reading the claim. |
| **A4** | **Self-review only** | assigning review | The author's tests validate the author's implementation, not the spec; a same-context reviewer inherits the same blind spots. | Independent reviewer without author context; focused multi-lens review (correctness / security / tests / integration). |
| **A5** | **Score-gaming** | reading a validator score | More file refs, more headings, more prose raise the number without raising quality. | Read the §0 operating rule; weight implementation-readiness over density; require A1/A4 evidence regardless of score. |

---

## 7b. Rationalization Watchlist

When you catch one of these thoughts, stop — it precedes an anti-pattern (mirrors FRAMEWORK.md's implementation watchlist):

| Rationalization | Counter |
|-----------------|---------|
| "Traceability can be filled in later." | Late traceability is incomplete; links made during authoring are far more accurate. |
| "The tests pass and coverage is high, so it's correct." | Coverage and green ≠ correct (A1/A2). Verify behavior on real paths. |
| "It's basically implemented — I'll mark it done." | `implemented` is a truthfulness claim (A3). Disclose stubs or don't claim it. |
| "I wrote it, so I can review it." | Self-review inherits your blind spots (A4). Get an independent pass. |
| "Score is 85, it's ready." | The score is a drafting aid, not a verdict (§0, A5). |
| "The PRD is close enough — start coding." | Implementation against a PRD with no executable ACs guarantees an unverifiable "done" (§2.5, §9 implementation-start gate). Groom first. |
| "We must eliminate all uncertainty first." | Route uncertainty (C6); don't chase zero. |

---

## 8. Risk-Based Rigor — Live Profiles (Reference)

Priority maps to risk (P0→Critical, P1→High, P2→Medium, P3→Low) unless an explicit `risk_level` in the PRD frontmatter overrides it (use the override when priority and consequence diverge — e.g., a P1-priority but low-blast-radius UX change). Each profile scales tier thresholds, the density floor, and dimension weights:

| Risk | Approved | Review | Draft | Density floor | Weights (density / structure / readiness / traceability) |
|------|---------:|-------:|------:|--------------:|-----------------------------------------------------------|
| Critical | 92 | 75 | 45 | 0.50 | 15 / 20 / 30 / 35 |
| High | 88 | 70 | 35 | 0.40 | 18 / 20 / 27 / 35 |
| Medium | 85 | 60 | 30 | 0.30 | 20 / 20 / 25 / 35 |
| Low | 75 | 50 | 20 | 0.20 | 25 / 20 / 20 / 35 |

Higher risk raises the bar **and** shifts weight toward implementation-readiness — proof matters more as consequence grows. Traceability stays at 35 across all tiers: links to evidence are never optional.

---

## 9. Getting Started in a New Project

### Minimum viable setup
1. Copy `AARE-F-FRAMEWORK.md` (this file) and `FRAMEWORK.md` to the project.
2. Scaffold the requirements directory:
   ```
   docs/requirements-aare-f/
   ├── INDEX.md            # PRD catalogue + status
   ├── ROADMAP.md          # phased delivery plan
   ├── prds/TEMPLATE.md    # PRD template (category variants below)
   ├── prompts/            # creation / elicitation / validation / traceability / conflict
   └── sprints/{active,completed}/
   ```
3. First PRD id: `PRD-{CATEGORY}-{SEQ:03d}` (CORE, QUAL, INFRA, FIX, LOCAL, EXPLR, RESEARCH, …).
4. Configure tooling paths (e.g., `.trw/config.yaml`) if using TRW MCP tools.

### Template variants (sections required scale to category)
| Variant | Categories | Required sections |
|---------|-----------|------------------:|
| Feature | CORE, QUAL, EVAL | 12 |
| Infrastructure | INFRA, LOCAL | 9 |
| Fix | FIX | 8 |
| Research | RESEARCH, EXPLR | 7 |

(There is no single fixed "12-section" rule — section count is category-dependent.)

### Lifecycle
```
create → groom → review (independent) → exec-plan → IMPLEMENT → VALIDATE → REVIEW → DELIVER → audit
```
In the TRW reference: `trw_prd_create` → grooming → independent review → execution planning → sprint execution → `trw_build_check` → `trw_deliver` → post-delivery adversarial audit (expected for P0/P1). Client adapters provide shorthand for the middle stages (Claude Code: `/trw-prd-groom`, `/trw-prd-review`, `/trw-exec-plan`, `/trw-audit`; other full-mode profiles have equivalent skills; light clients use the underlying MCP tools directly — `trw_prd_create`, `trw_prd_validate` — or the manual lifecycle). Sprint grouping of reviewed PRDs is likewise an adapter concern. The grooming gate is "ready for review" (the score); the **delivery** gate is §6.2 evidence.

**Implementation-start gate** (the inverse of A3): if a PRD lacks executable ACs (§2.5) when implementation begins, STOP and groom before writing code. A3 catches false completion at the end; this catches unverifiable scope at the start.

### Adoption sequence (for a new project — honest tiers)
1. **Structural floor** (days): PRD template, schema validation, ID uniqueness in CI.
2. **Traceability floor** (weeks): bidirectional links, executable ACs, independent review on P0/P1.
3. **Intelligence layer** (when justified): semantic search/dedup (C4), multi-agent review (C5), calibrated confidence routing (C2). Skipping straight to layer 3 without the floors produces score theater.

### Backlog (aspirational components, honestly flagged)
Live as of v3.0.x: smell detection + EARS classification (informational, §2.4/§2.1); smell findings surfaced in grooming suggestions; behavioral-assertion vocabulary (`asserts_value`/`output_contains`, §2.5/A1); a corpus-wide status-truthfulness audit + ratchet gate (`make prd-truthfulness-gate`, FPI #7); a malformed-frontmatter validation gate; variant-aware section-count + FIX/compact scoring; a **mock-depth / weak-test static gate** (`make mock-depth`, anti-pattern A2) with opt-in mutation sampling (`make mutation-sample`); a **traceability-target existence gate** (`make ac-coverage` — implemented PRDs must not cite dangling test paths); an informational **measured traceability coverage ratio**; **status-vocabulary canonicalization** (warning-only); and **legacy-PRD truthfulness migration tooling** (`make prd-migrate`, dry-run, never fabricates `live`).

**Decided (with evidence):** *scoring* smells/EARS at a non-zero weight is **NOT** adopted — a corpus calibration (N≈2800) found smell-count *positively* correlates with score (r≈+0.58, a confound: substantive PRDs have more requirement lines to both smell and score), so scoring would inflate empty skeletons and is trivially gameable by omission. Smells stay informational. See `docs/research/aaref-smell-weight-calibration-2026-05-31.md` (TRW-internal research artifact).

Remaining items to track as PRDs rather than implying they exist: a full executable-AC *test-runner* (the existence gate checks the cited test exists, not that it passes); a blocking mutation-testing gate (currently opt-in/advisory); solver-checked conflict satisfiability; and *executing* the legacy migration — the tooling exists but the ~286 PRDs missing `functionality_level` (status `done` family needs human review) and ~169–184 with malformed frontmatter still need remediation.

---

## 10. Regulatory Alignment

| Regulation | Component | Requirement |
|------------|-----------|-------------|
| EU AI Act Art. 14 | C2 | Human oversight |
| FDA SaMD | C2 | Decision support, not replacement |
| HIPAA (if PHI) | C2, C8 | Protected-health-information handling |
| OWASP Top 10 for LLM | C8 | Injection / disclosure / poisoning defense |
| ISO/IEC 42001 | C1, C9 | AI management system |
| NIST AI RMF 1.0 | C3, C8 | Risk management documentation |

---

## 11. Relationship to FRAMEWORK.md

| | AARE-F (this doc) | FRAMEWORK.md (`v26_TRW`) |
|---|---|---|
| Governs | the **specification** (requirement/PRD quality, traceability, verification) | the **execution** (phases, gates, formations, persistence, learning) |
| Key artifact | the PRD | the run (phases + checkpoints + evidence) |
| "Verify" means | an automated check confirms the requirement (P5) | VALIDATE gate: project-native tests recorded via `trw_build_check` |
| Shared spine | Evidence over assertion; independent review; risk-scaled rigor | same |

Use them together: AARE-F says *what good looks like and how to prove it*; FRAMEWORK.md says *how to run the work that produces and verifies it*.

---

## 12. Maintenance & Versioning

| Review | Frequency | Trigger |
|--------|-----------|---------|
| Standards refresh | ~quarterly | new ISO/INCOSE/EARS or LLM-RE research |
| Major revision | as needed | framework posture change |
| On-trigger | as needed | regulatory change, audit finding, major incident |

**Single-source rule**: this file ships from one bundled source and is deployed verbatim; the repo-root copy and any vendored copies MUST stay byte-identical to it, and the recorded framework version MUST match this header. (A sync check enforces this — drift between copies is how prior versions shipped a stale body under a newer version stamp.)

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-24 | Initial release |
| 1.1.0 | 2026-02-02 | Prompts, indexes, quality gates |
| 2.0.0 | 2026-02-21 | Project-agnostic portable edition; removed hardcoded project references; Getting Started |
| 3.0.0 | 2026-05-31 | **Truthfulness + SOTA reconciliation**: removed residual project contamination and unsourced effectiveness numbers (67%/96%/100%/+28%/+13.2%); replaced fixed confidence-% bands with calibration-first guidance; added the Requirement Quality Standard (EARS + ISO 29148 + INCOSE GtWR v4 + smells + executable ACs); added §0 "what this is/isn't" and the live quality model (real 4-dimension risk-scaled validator); added truthfulness anti-patterns A1–A5; added Specification Primacy (P1) and Evidence-over-Assertion (P6); clarified relationship to FRAMEWORK.md; per-component live/partial/guide status; single-source/sync mandate. |
| **3.1.0** | **2026-06-10** | **Operational sharpening** (research-grounded refinement run): operative summary front-loaded; EARS/Given-When-Then layering (§2.1); ISO 29148 revision note (§2.2); smell-weight evidence update — RE 2025 (§2.4); agent-authored-test caveat + sampled mutation testing (§2.5); autonomous-loop oversight (C2); reviewer iteration policy + NFR-specialized dialectical review for Critical sets (C5); delivered≠wired consumer/seam check (C7); OWASP agentic edition + injection-resistance ACs (C8); false-completion rate metric + verifier identity (C9); A1–A5 "check when" column; implementation-start gate + adoption sequence + portable lifecycle naming (§9); principle citations (P1/P6); risk_level override location (§8). |

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| AARE-F | AI-Augmented Requirements Engineering Framework |
| EARS | Easy Approach to Requirements Syntax |
| AC | Acceptance Criterion (Given/When/Then) |
| ECE | Expected Calibration Error |
| HITL | Human-in-the-Loop |
| PRD | Product Requirements Document |
| RAG | Retrieval-Augmented Generation |
| MCDM | Multi-Criteria Decision Making |
| SMT | Satisfiability Modulo Theories (solver class) |
| NFR | Non-Functional Requirement |

## Appendix B: References

- ISO/IEC/IEEE 29148:2018 — Requirements Engineering (revision in committee draft as of early 2026)
- INCOSE *Guide to Writing Requirements* v4 (INCOSE-TP-2010-006-04, 2023)
- A. Mavin et al., *Easy Approach to Requirements Syntax (EARS)*, IEEE RE 2009
- OWASP Top 10 for LLM Applications; OWASP Top 10 for Agentic Applications (Dec 2025) — cite the edition you target
- NIST AI RMF 1.0; EU AI Act (2024); ISO/IEC 42001
- 2024–2026 LLM-for-RE literature (ambiguity detection, requirements smells, automated repair; RE 2025 smell-impact-on-LLM-performance findings)
- LLM-assisted mutation testing at production scale (Meta ACH, 2025–2026)
- LLM calibration / uncertainty quantification: Guo et al. 2017 (*On Calibration of Modern Neural Networks*); Kadavath et al. 2022 (*Language Models (Mostly) Know What They Know*); semantic-entropy and conformal-prediction literature (2023–2025)
- Domain assurance (where applicable): DO-178C, ISO 26262, IEC 62304

## Appendix C: Related Documents

Paths below are TRW-monorepo locations; new projects create their own per §9 (the scaffold), and standalone deployments should treat them as scaffold targets, not existing files.

| Document | Location | Purpose |
|----------|----------|---------|
| Execution framework | `FRAMEWORK.md` | 6-phase execution methodology |
| TRW-applied overview | `docs/documentation/aare-f-overview.md` | how TRW operationalizes AARE-F |
| PRD template | `docs/requirements-aare-f/prds/TEMPLATE.md` (and bundled `prd_template.md`) | category-variant PRD template |
| Prompts | `docs/requirements-aare-f/prompts/` | requirements-work prompts |
| PRD catalogue | `docs/requirements-aare-f/INDEX.md` | status tracking |
| Roadmap | `docs/requirements-aare-f/ROADMAP.md` | phased delivery |
| Execution plans | `docs/requirements-aare-f/exec-plans/` | FR→micro-task decompositions |

## Appendix D: Execution Plan Artifact

An execution plan bridges a reviewed PRD to implementation by decomposing FRs into micro-tasks with file paths, test names, verification commands, and a dependency graph.

- **When**: after review returns READY, before implementation; recommended for P0/P1.
- **Storage**: `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`.
- **Principles**: short waves (small, verifiable units of work); function-level granularity (file-level planning misses secondary paths); a verification command per micro-task (never "verify manually"); explicit dependencies.

---

*AARE-F v3.1.0 — AI-Augmented Requirements Engineering Framework (Portable Edition)*
*Truthful · Standards-Aligned · Implementation-Reconciled · Last Updated: 2026-06-10*
