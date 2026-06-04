# AI-Augmented Requirements Engineering Framework (AARE-F)

**Version**: 3.0.0
**Last Updated**: 2026-05-31
**Purpose**: Project-agnostic framework for engineering requirements with AI assistance — truthful, verifiable, and aligned to current requirements-engineering standards.
**Research Basis**: ISO/IEC/IEEE 29148:2018, INCOSE *Guide to Writing Requirements* v4 (2023), EARS (Mavin et al.), 2024–2026 LLM-for-RE literature, and TRW's own empirical findings (eval iterations and the PRD-audit corpus).

> **Companion documents.** AARE-F defines *what a good requirement/PRD is and how to verify it*. [`FRAMEWORK.md`](FRAMEWORK.md) (`v25_TRW`) defines *how work is executed* (the 6-phase RESEARCH→PLAN→IMPLEMENT→VALIDATE→REVIEW→DELIVER model, gates, formations). They are complementary: AARE-F governs the **specification**; FRAMEWORK.md governs the **execution**. Neither restates the other. For how TRW operationalizes AARE-F day-to-day, see `docs/documentation/aare-f-overview.md`.

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
| **P1** | **Specification Primacy** | The approved spec is the source of truth; code and tests are its expression. A requirement with no passing test is unverified; a change with no requirement is unauthorized scope. |
| **P2** | **Traceability First** | Every requirement traces *up* to a need/source and *down* to implementation and verification. Links are created during authoring, not reconstructed later. |
| **P3** | **Human-in-the-Loop** | AI accelerates drafting, analysis, and review; humans own approval, risk acceptance, and delivery. Oversight is a checkpoint with authority, not a rubber stamp. |
| **P4** | **Risk-Based Rigor** | Effort, proof, and review scale with consequence. Not all requirements deserve equal ceremony. |
| **P5** | **Verifiable by Construction** | "Verifiable" means *an automated check exists and passes* — not that the prose sounds testable. Acceptance criteria compile to tests. |
| **P6** | **Evidence Over Assertion** | Quality is demonstrated with artifacts (tests, diffs, traces, independent review), never asserted. Uncertainty is preserved, not averaged away. |

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

### 2.2 ISO/IEC/IEEE 29148:2018 — Quality Characteristics

Each requirement (individual) and the requirement set (collective) MUST satisfy:

| Level | Characteristics |
|-------|-----------------|
| **Individual** | Necessary · Appropriate · Unambiguous · Complete · Singular · Feasible · Verifiable · Correct · Conforming |
| **Set** | Complete · Consistent · Feasible · Comprehensible · Validatable · Correct (no duplicates, no conflicts, homogeneous language) |

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

> **Implementation status (TRW reference):** the live validator **detects requirement smells** (weak modal, vague adverb, subjective, escape clause, open-ended, superlative, absolute, compound, vague pronoun) and surfaces them as **informational** `smell_findings` — each with a line number, matched text, and a fix suggestion. They are advisory: `validation_smell_weight` stays 0, so smells never change the score. A separate `ambiguity_rate` (a small vague-term ratio) is also reported. *Scoring* smells (non-zero weight) is deliberately deferred — see Section 9 — to avoid destabilizing the calibrated score.

### 2.5 Executable Acceptance Criteria

A requirement is "verifiable" (P5) only when at least one **automated** check confirms it. Author ACs in a testable form and bind them to tests:

```
Given <initial context>
When  <trigger / action>
Then  <observable, asserted outcome>
```

- Every functional requirement has ≥1 AC; every `status: implemented` requirement has ≥1 test referencing it that exercises the **real data path** (not a mock of the unit under test).
- Prefer behavioral assertions (the output contains the correct value) over existence assertions (a symbol/string is present). Existence checks prove a name compiled, not that data flowed (see §7, Anti-Pattern A1).
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
**OWASP Top-10-for-LLM focus**: prompt injection, sensitive-information disclosure, data/model poisoning. (Cite the OWASP edition you target; prevalence figures are edition- and context-specific — don't quote a bare percentage.)

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
**Isolation-first**: an independent reviewer/critic MUST NOT inherit the author's context (see §7, A4). First-pass exploration without prior-pattern priming reduces anchoring.

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
| Delivery | approval recorded, audit log, rollback path |

#### C9: Continuous Observability — [partial]
**MELT**: Metrics (coverage, novelty, AC pass-rate, requirement churn) · Events (validated/groomed/delivered) · Logs (validation + decision trail) · Traces (end-to-end flow). Use whatever telemetry stack the project already has (structured logging is the floor; OpenTelemetry/Prometheus are optional, not required).

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

| # | Truthfulness Anti-Pattern | Why text scoring misses it | Mitigation (the gate that catches it) |
|---|---------------------------|----------------------------|----------------------------------------|
| **A1** | **Existence ≠ wiring** | `grep_present`/symbol checks prove a name exists, not that the computed value reaches the output. Real bugs shipped green: an estimate computed then dropped (`estimate=None`); loaded priors silently discarded. | Behavioral assertions on output *values*; ≥1 test per FR exercising the real path. |
| **A2** | **Mock-depth blindness** | 90%+ coverage with all-green tests can mock away the unit under test; AI-written tests routinely score 30–40% on mutation testing at high coverage. | Forbid mocking the primary unit under test; require an integration test on a real (in-memory) path; consider sampled mutation testing. |
| **A3** | **`implemented` over stubs** | A status field is text; nothing in the score checks runtime completeness. Audits repeatedly find `status: implemented` with non-empty `stubs[]`. | Enforce: `status: implemented` ⇒ functionally live, `stubs: []`; verify in review/CI, not by reading the claim. |
| **A4** | **Self-review only** | The author's tests validate the author's implementation, not the spec; a same-context reviewer inherits the same blind spots. | Independent reviewer without author context; focused multi-lens review (correctness / security / tests / integration). |
| **A5** | **Score-gaming** | More file refs, more headings, more prose raise the number without raising quality. | Read the §0 operating rule; weight implementation-readiness over density; require A1/A4 evidence regardless of score. |

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
| "We must eliminate all uncertainty first." | Route uncertainty (C6); don't chase zero. |

---

## 8. Risk-Based Rigor — Live Profiles (Reference)

Priority maps to risk (P0→Critical, P1→High, P2→Medium, P3→Low) unless an explicit `risk_level` overrides. Each profile scales tier thresholds, the density floor, and dimension weights:

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
In the TRW reference: `trw_prd_create` → `/trw-prd-groom` → `/trw-prd-review` → `/trw-exec-plan` → sprint execution → `trw_build_check` → `trw_deliver` → `/trw-audit` (expected for P0/P1). The grooming gate is "ready for review" (the score); the **delivery** gate is §6.2 evidence.

### Backlog (aspirational components, honestly flagged)
Live as of v3.0.x: smell detection + EARS classification (informational, §2.4/§2.1); smell findings surfaced in grooming suggestions; behavioral-assertion vocabulary (`asserts_value`/`output_contains`, §2.5/A1); a corpus-wide status-truthfulness audit + ratchet gate (`make prd-truthfulness-gate`, FPI #7); a malformed-frontmatter validation gate; variant-aware section-count + FIX/compact scoring; a **mock-depth / weak-test static gate** (`make mock-depth`, anti-pattern A2) with opt-in mutation sampling (`make mutation-sample`); a **traceability-target existence gate** (`make ac-coverage` — implemented PRDs must not cite dangling test paths); an informational **measured traceability coverage ratio**; **status-vocabulary canonicalization** (warning-only); and **legacy-PRD truthfulness migration tooling** (`make prd-migrate`, dry-run, never fabricates `live`).

**Decided (with evidence):** *scoring* smells/EARS at a non-zero weight is **NOT** adopted — a corpus calibration (N≈2800) found smell-count *positively* correlates with score (r≈+0.58, a confound: substantive PRDs have more requirement lines to both smell and score), so scoring would inflate empty skeletons and is trivially gameable by omission. Smells stay informational. See `docs/research/aaref-smell-weight-calibration-2026-05-31.md`.

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

| | AARE-F (this doc) | FRAMEWORK.md (`v25_TRW`) |
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
| **3.0.0** | **2026-05-31** | **Truthfulness + SOTA reconciliation**: removed residual project contamination and unsourced effectiveness numbers (67%/96%/100%/+28%/+13.2%); replaced fixed confidence-% bands with calibration-first guidance; added the Requirement Quality Standard (EARS + ISO 29148 + INCOSE GtWR v4 + smells + executable ACs); added §0 "what this is/isn't" and the live quality model (real 4-dimension risk-scaled validator); added truthfulness anti-patterns A1–A5; added Specification Primacy (P1) and Evidence-over-Assertion (P6); clarified relationship to FRAMEWORK.md; per-component live/partial/guide status; single-source/sync mandate. |

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

- ISO/IEC/IEEE 29148:2018 — Requirements Engineering
- INCOSE *Guide to Writing Requirements* v4 (INCOSE-TP-2010-006-04, 2023)
- A. Mavin et al., *Easy Approach to Requirements Syntax (EARS)*, IEEE RE 2009
- OWASP Top 10 for LLM Applications (cite the edition you target)
- NIST AI RMF 1.0; EU AI Act (2024); ISO/IEC 42001
- 2024–2026 LLM-for-RE literature (ambiguity detection, requirements smells, automated repair)
- LLM calibration / uncertainty quantification: Guo et al. 2017 (*On Calibration of Modern Neural Networks*); Kadavath et al. 2022 (*Language Models (Mostly) Know What They Know*); semantic-entropy and conformal-prediction literature (2023–2025)
- Domain assurance (where applicable): DO-178C, ISO 26262, IEC 62304

## Appendix C: Related Documents

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

*AARE-F v3.0.0 — AI-Augmented Requirements Engineering Framework (Portable Edition)*
*Truthful · Standards-Aligned · Implementation-Reconciled · Last Updated: 2026-05-31*
