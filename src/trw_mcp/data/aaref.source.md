<!-- trw:span id=af-title dest=both class=normative -->
# AI-Augmented Requirements Engineering Framework (AARE-F)

**Version**: 3.2.0
**Last Updated**: 2026-07-09
**Purpose**: Project-agnostic framework for engineering requirements with AI assistance — truthful, verifiable, and aligned to current requirements-engineering standards.
**Research Basis**: ISO/IEC/IEEE 29148:2018 (confirmed current in 2024 and marked for revision in 2026), INCOSE *Guide to Writing Requirements* v4 (2023), EARS (Mavin et al.), requirements-engineering V&V practice, and TRW's empirical findings (eval iterations and the PRD-audit corpus).

> **Companion documents.** AARE-F defines *what a good requirement/PRD is and how to verify it*. [`FRAMEWORK.md`](FRAMEWORK.md) (`v26.1_TRW`) defines *how work is executed* (the 6-phase RESEARCH→PLAN→IMPLEMENT→VALIDATE→REVIEW→DELIVER model, gates, formations). They are complementary: AARE-F governs the **specification**; FRAMEWORK.md governs the **execution**. Neither restates the other. For how TRW operationalizes AARE-F day-to-day, see `docs/documentation/aare-f-overview.md` (TRW monorepo path — not present in standalone deployments).

> **Operative summary (read this even under context pressure).** Verified closure is §6.2: risk-appropriate independent review + requirement-matched verification evidence + status truthfulness + recorded project-native validation. A delivery override may ship known risk, but it does not make the requirement verified. The validator score (§5) is a drafting aid, not a verdict (§0). The anti-patterns that actually ship defects are A1 (existence ≠ wiring), A3 (`implemented` over stubs), and A4 (self-review only) — check them at evidence design, status update, and reviewer assignment respectively (§7). A PRD without acceptance criteria and declared verification methods is not ready to implement (§9).

---

<!-- trw:span id=af-0-what-this-framework-is-and-is-not dest=core class=normative -->
## 0. What This Framework Is — and Is Not

Read this before using any threshold or score below.

**AARE-F is**: a discipline for writing requirements that are unambiguous, singular, verifiable, and traceable; storing them as versioned artifacts (PRDs); and verifying them with evidence rather than assertion. Its durable value is **structural**: a consistent artifact shape makes independent adversarial review tractable and makes requirement→implementation→verification traceability checkable.

**AARE-F is NOT** a quality oracle. The validator's numeric score (Section 5) is a **drafting aid that surfaces missing structure** — it is **not** a predictor of implementation success, and a high score does **not** mean the work is correct. This is an evidence-based position, not modesty:

- No controlled study (internal or published) shows that a higher PRD score predicts fewer defects or less rework. Sub-signals (completeness, low ambiguity, real traceability) track readiness better than the aggregate score.
- Scores are **gameable** by surface formatting (more file references, more section headings, more prose) independent of implementation quality. The validator is hardened against the worst cases but cannot close the gap with text analysis alone.
- The failures that actually ship — *computed-but-discarded values, mocked-away data paths, `status: implemented` over unfinished stubs* — are **invisible to text scoring**. They are caught by **independent adversarial review and behavioral tests**, not by the score.

**Operating rule**: Treat the score as "is this draft structurally ready for review?" Treat **risk-appropriate independent review + requirement-matched verification evidence** as the actual quality gate. When the two disagree, trust the evidence, not the number. (Value hierarchy: **Truthfulness > Quality > Knowledge > Velocity**.)

---

<!-- trw:span id=af-1-foundational-principles dest=core class=normative -->
## 1. Foundational Principles

| # | Principle | Description |
|---|-----------|-------------|
| **P1** | **Specification Primacy** | The approved source of intent is the contract; code and evidence are its expression. A source may be a PRD, issue, incident, user request, or other versioned decision appropriate to scope. A requirement with no accepted verification evidence is unverified; an untraceable change is unauthorized scope. (ISO/IEC/IEEE 29148 §5.2; INCOSE GtWR v4.) |
| **P2** | **Traceability First** | Every requirement traces *up* to a need/source and *down* to implementation and verification. Links are created during authoring, not reconstructed later. |
| **P3** | **Human Authority** | AI accelerates drafting, analysis, and review. Humans set policy, delegation bounds, and residual-risk authority; automation may approve or deliver only inside those explicit bounds, with human override preserved. Oversight is authority, not a rubber stamp. |
| **P4** | **Risk-Based Rigor** | Effort, proof, and review scale with consequence. Not all requirements deserve equal ceremony. |
| **P5** | **Verification by Construction** | Every requirement declares a feasible verification method, evidence artifact, and pass condition while it is authored. Automate machine-observable behavior; use analysis, inspection, or demonstration when those methods fit the requirement better. |
| **P6** | **Evidence Over Assertion** | Quality is demonstrated with artifacts (tests, diffs, traces, independent review), never asserted. Uncertainty is preserved, not averaged away. (TRW PRD-audit corpus; eval governance findings.) |

These mirror FRAMEWORK.md's execution principles (Evidence > assertion; Prevention > detection; External checks > self-belief). AARE-F applies them to the *requirement* artifact specifically.

---

<!-- trw:span id=af-2-the-requirement-quality-standard dest=core class=normative -->
## 2. The Requirement Quality Standard

This is the heart of AARE-F: a concrete, standards-aligned definition of a good requirement. It unifies EARS (phrasing), ISO/IEC/IEEE 29148 (characteristics), INCOSE GtWR v4 (rules), a requirements-smells taxonomy (detection), and acceptance criteria with declared verification evidence.

<!-- trw:span id=af-2-1-ears-requirement-phrasing-patterns dest=reference class=reference -->
### 2.1 EARS — Requirement Phrasing Patterns

EARS (Easy Approach to Requirements Syntax; Mavin et al., 2009) is an established structured-natural-language syntax. Every functional requirement SHOULD match one pattern when EARS fits the domain:

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
**Layering**: EARS structures the *requirement sentence*; Given/When/Then (§2.5) can structure behavioral *acceptance criteria*. They are complementary, not mandatory pairs — one requirement maps to one or more acceptance/verification criteria using the method that fits it.

<!-- trw:span id=af-2-2-iso-iec-ieee-29148-2018-quality-char dest=core class=normative -->
### 2.2 ISO/IEC/IEEE 29148:2018 — Quality Characteristics

Each requirement (individual) and the requirement set (collective) MUST satisfy:

| Level | Characteristics |
|-------|-----------------|
| **Individual** | Necessary · Appropriate · Unambiguous · Complete · Singular · Feasible · Verifiable · Correct · Conforming |
| **Set** | Complete · Consistent · Feasible · Comprehensible · Validatable · Correct (no duplicates, no conflicts, homogeneous language) |

> ISO lists the 2018 edition as current (confirmed in 2024) and at stage 90.92 “to be revised” since February 2026, with a committee draft under development. Treat 2018 as operative until a replacement publishes, then run a conformance review.

<!-- trw:span id=af-2-3-incose-gtwr-v4-2023-rule-families dest=reference class=reference -->
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

<!-- trw:span id=af-2-4-requirements-smells-detection-taxono dest=reference class=reference -->
### 2.4 Requirements Smells — Detection Taxonomy

A *smell* is a lexical/structural signal of likely low quality (Femmer et al.). AARE-F flags twelve categories. Detect with regex + LLM classification; map each to severity and an INCOSE rule:

`subjective language` · `ambiguous adverbs/adjectives` · `weak modal verbs (should/might/may vs shall)` · `passive voice without agent` · `vague pronouns` · `anaphoric ambiguity` · `coordination ambiguity (AND/OR precedence)` · `loopholes/escape clauses` · `non-verifiable terms (robust/scalable without criteria)` · `superlatives` · `negative statements` · `compound requirements`.

> **Implementation status (TRW reference):** the live validator **detects requirement smells** (weak modal, vague adverb, subjective, escape clause, open-ended, superlative, absolute, compound, vague pronoun) and surfaces them as **informational** `smell_findings` — each with a line number, matched text, and a fix suggestion. They are advisory: `validation_smell_weight` stays 0, so smells never change the score. A separate `ambiguity_rate` is also reported and is not scored. *Scoring* smells (non-zero weight) remains deferred: the corpus calibration found a size/content confound that would reward omission and make the score easier to game.

<!-- trw:span id=af-2-5-acceptance-criteria-and-verification dest=core class=normative -->
### 2.5 Acceptance Criteria and Verification Evidence

A requirement is verifiable only when it names a feasible method, inspectable evidence, and an objective pass condition. Given/When/Then is preferred for observable behavior; it is not a universal syntax for analysis, inspection, or demonstration:

```
Given <initial context>
When  <trigger / action>
Then  <observable, asserted outcome>
```

- Every requirement has ≥1 AC and a verification mapping:

| Method | Use when | Required evidence |
|--------|----------|-------------------|
| Test | behavior is machine-observable | executable check, asserted outcome, raw result |
| Analysis | a quantitative/model-based argument establishes compliance | input set, method/tool, result, reviewer |
| Inspection | a static property, document, configuration, or record can be examined | artifact/version, checklist or rule, reviewer, disposition |
| Demonstration | an operational workflow or human-visible outcome must be shown | procedure, environment, observations, approver |

- Every `status: implemented` software-behavior requirement has ≥1 automated behavioral test on the **real data path** unless automation is infeasible; any exception states why and uses another declared method with durable evidence.
- Prefer behavioral assertions (the output contains the correct value) over existence assertions when the contract is behavior or wiring. Existence is valid only when existence itself is the requirement; it is never a proxy for data flow (see §7, A1).
- Tests authored by the same agent in the same session as the implementation are **self-review artifacts** (confidence: medium) until independently reviewed — they tend to validate the implementation, not the specification (A4). Mutation testing strengthens assertion sensitivity (A2) but does not create reviewer independence.
- For Critical/High-risk executable behavior, SHOULD supplement behavioral tests with **sampled mutation testing** when the toolchain supports it: coverage alone does not demonstrate assertion strength.
- *Advanced / aspirational*: for high-risk requirements, auto-formalize ACs and check joint satisfiability with a solver (the neuro-symbolic path now appearing in spec-driven IDEs). Reserve for Critical/High risk.

---

<!-- trw:span id=af-3-framework-architecture dest=reference class=example -->
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
|  [C5: Independent Role Separation] [C6: Uncertainty] [C10: Conflicts]|
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      OPERATIONS LAYER                            |
|  [C7: Requirements-as-Code] [C9: Observability]                 |
+------------------------------------------------------------------+
```

**Component status legend** (truthfulness — what is real vs. designed-for): **[live]** implemented and load-bearing in the TRW reference; **[partial]** partly implemented; **[guide]** design guidance, not enforced by tooling.

---

<!-- trw:span id=af-4-core-components dest=reference class=reference -->
## 4. Core Components

<!-- trw:span id=af-foundation-layer dest=core class=normative -->
### Foundation Layer

<!-- trw:span id=af-c1-end-to-end-traceability-infrastructur dest=reference class=reference -->
#### C1: End-to-End Traceability Infrastructure — [partial]
**Purpose**: Audit, impact analysis, and provenance.
**Key requirements**: stable IDs for artifacts; maintained links (`implements / depends_on / enables` plus project-defined typed relations); link-integrity validation on change; requirement→implementation→verification linkage whose evidence can be executed or independently inspected.
**Reference status**: the TRW reference has a link schema, traceability matrix,
and separate declared/measured coverage fields exposed by `trw_prd_validate`.
Generated PRDs do not retain a first-class `conflicts_with` relation, and
automated impact analysis is not first-class. Treat those as partial, not live.

<!-- trw:span id=af-c4-semantic-infrastructure-layer-guide dest=reference class=reference -->
#### C4: Semantic Infrastructure Layer — [guide]
**Purpose**: Semantic search/dedup/novelty as substrate for requirements work.
**Guidance**: use semantic retrieval only when it improves a declared requirements task (discovery, deduplication, impact analysis). Preserve source/provenance, combine lexical and semantic evidence when useful, and calibrate models, chunking, fusion, and similarity thresholds on the project's own corpus. A vector store, embedding model, or fixed threshold is an adapter choice, not part of AARE-F.

<!-- trw:span id=af-governance-layer dest=core class=normative -->
### Governance Layer

<!-- trw:span id=af-c2-ai-in-the-loop-governance-partial dest=core class=normative -->
#### C2: AI-in-the-Loop Governance — [partial]
**Purpose**: Keep human authority explicit over AI-assisted decisions while allowing bounded automation.
**Authority checkpoints**: input validation → confidence-aware routing → output verification → residual-risk acceptance → human override. A project MAY pre-authorize automation inside defined risk and evidence bounds; it MUST preserve an accountable human escalation path for consequential exceptions.

> **Confidence routing — evidence first.** Earlier versions published fixed percentage bands (">95% automate", etc.). Do not use uncalibrated verbalized-confidence thresholds: a number from one model/task/harness does not transfer automatically. Define the observable signal, local error evidence, routing action, and fallback. When a harness exposes suitable probabilities, calibration or conformal methods MAY help; when it does not, use source grounding, independent checks, explicit unknowns, and human escalation. Technique names are optional recipes, not portable requirements.

**Governance/readiness alignment** (where applicable; not compliance): EU AI Act human oversight, FDA SaMD decision support, ISO/IEC 42001, NIST AI RMF.

> **Autonomous-loop oversight.** When requirements work runs inside unattended loops, human checkpoints degrade to theater unless escalation is structurally protected: a stop-recommendation overridden repeatedly MUST escalate on a channel the loop cannot disable, and cycle closure MUST be outcome-gated (a declared success criterion moved, or a documented rationale) rather than throughput-gated. A loop that can suppress its own diagnostics inverts the authority hierarchy (P3).

<!-- trw:span id=af-c3-risk-based-rigor-scaling-partial dest=core class=normative -->
#### C3: Risk-Based Rigor Scaling — [partial]
**Purpose**: Scale effort and proof to consequence. Risk-scaled score thresholds, density floors, and dimension weights are live in the TRW validator (Section 8). The review/V&V assignments below are normative guidance; the current review tool does not enforce them from `risk_level`.

| Level | Documentation | Verification | Review |
|-------|---------------|--------------|--------|
| Critical | Full spec + ACs | Declared method + independent V&V | Multi-party |
| High | Structured spec | Declared method + independent verification | Technical lead or delegated authority |
| Medium | Standard template | Declared method + project-native evidence | STANDARD review rule or team policy |
| Low | Lightweight source/ACs | Basic requirement-matched evidence | Optional unless execution tier requires it |

Apply the stricter of requirement risk and FRAMEWORK.md execution tier.

<!-- trw:span id=af-c8-ai-guardrails-safety-partial dest=reference class=reference -->
#### C8: AI Guardrails & Safety — [partial]
**Minimal set**: input sanitizer (prompt-injection detection) · output filter (harmful-content / PII) · tool allowlist · token/cost budgets · immutable audit log · periodic adversarial testing.
**OWASP focus**: for agent-executed workflows treat the **Top 10 for Agentic Applications 2026** (published Dec 2025) as primary — goal hijack, tool misuse, identity/privilege abuse, memory/context poisoning, excessive agency — alongside the LLM Applications 2025 edition. Cite the edition you target; prevalence figures are edition- and context-specific — don't quote a bare percentage.
**Requirement-level guardrail**: a requirement that directs an agent to read external content (files, URLs, retrieval results, tool outputs) SHOULD carry an injection-resistance acceptance criterion — indirect injection through content is the production exploit class.

<!-- trw:span id=af-execution-layer dest=core class=normative -->
### Execution Layer

<!-- trw:span id=af-c5-independent-analysis-role-separation- dest=core class=normative -->
#### C5: Independent Analysis & Role Separation — [partial]
**Purpose**: separate authoring, critique, verification, and risk acceptance so one context does not self-certify its own assumptions.

Roles are logical, not a mandate for multiple agents: humans, independent sessions, tools, or sub-agents MAY fill them. A low-risk set may use one actor sequentially with an explicit cold pass; Critical/High-risk sets require an independent critic/verifier. When a harness supports delegation, this maps onto FRAMEWORK.md formations (SINGLE-TRACK / MAP-REDUCE / PIPELINE / DEBATE+CRITIC+JUDGE).

**Isolation-first**: an independent reviewer gets the specification, diff/artifacts, and evidence — not the author's private trajectory. Pass 1 challenges the work without the author's conclusions; later passes reconcile evidence and target concrete gaps. Stop when a pass yields no new material finding. For Critical-risk sets, assign lenses by relevant quality family (for example safety, security, performance) and resolve cross-quality conflicts before verified closure.

<!-- trw:span id=af-c6-uncertainty-management-over-zero-defe dest=reference class=reference -->
#### C6: Uncertainty Management over Zero-Defects — [guide]
**Paradigm**: "eliminate all hallucination" is unreachable → **quantify and route uncertainty** instead.

| Tier | When | Relative cost | Methods |
|------|------|---------------|---------|
| 1 | Always | low | source grounding, provenance, explicit unknowns, requirement-matched checks |
| 2 | High stakes | medium | independent cross-checker/verifier, human review, adversarial examples |
| 3 | Periodic | high | calibration audits and statistically justified routing methods when the required signals exist |

**Critical warning**: high-confidence hallucinations cannot be caught by entropy alone — external grounding is required.
**On effectiveness numbers**: do not publish bare "N% hallucination reduction" figures (prior versions cited 67%/96% — both unsourced and untransferable). Reported reductions depend entirely on the hallucination definition, model, task, and dataset. State the method and require local measurement.

<!-- trw:span id=af-c10-conflict-detection-resolution-partia dest=reference class=reference -->
#### C10: Conflict Detection & Resolution — [partial]
**Conflict types & detection**: intra-requirement (rule-based NLP — high precision when rules are logically sound and maintained; do **not** claim 100%) · inter-domain (multi-criteria analysis) · cross-artifact (semantic comparison) · NFR trade-offs (trade-off catalogue).
**Resolution**: risk-based (higher-risk wins for safety/security) · AHP-TOPSIS (multi-stakeholder) · IBIS (wicked problems).
**Reference status**: conflict-analysis prompts are live guidance; there is no automated cross-requirement conflict detector or CI gate. Automated detection/resolution and solver-checked joint satisfiability remain aspirational.

<!-- trw:span id=af-operations-layer dest=core class=normative -->
### Operations Layer

<!-- trw:span id=af-c7-requirements-as-code-with-devops-part dest=core class=normative -->
#### C7: Requirements-as-Code with DevOps — [partial]
**Practices**: PRDs as versioned Markdown+YAML in git; semantic versioning + history; PR-based change; schema/consistency checks runnable on demand and in CI.

| Stage | Checks |
|-------|--------|
| Pre-commit | schema validity, required fields, ID uniqueness |
| CI | structure + traceability + (target) executable-AC pass-rate; conflict checking is a target until C10 has an automated gate |
| Delivery | approval recorded, audit log, rollback path, requirement-matched evidence, and **wiring verified** for public behavior — a surface marked `implemented` has ≥1 behavior-proven consumer or an explicit seam entry with owner + expiry. Existence without consumption is A1 at architecture scale. |

**Reference status**: versioned PRDs and several schema/consistency checks are live. Consumer/wiring analysis is advisory v1: presence can satisfy some checks without proving behavior, a seam can suppress warnings broadly, and expiry is not a universal delivery gate. Treat “wiring verified” as the required target state, not a claim that current tooling enforces it fully.

<!-- trw:span id=af-c9-continuous-observability-partial dest=core class=normative -->
#### C9: Continuous Observability — [partial]
**MELT**: Metrics (coverage, novelty, AC pass-rate, requirement churn, **false-completion rate** — delivers whose claimed outcome was later contradicted ÷ total delivers) · Events (validated/groomed/delivered) · Logs (validation + decision trail) · Traces (end-to-end flow). Use whatever telemetry stack the project already has (structured logging is the floor; OpenTelemetry/Prometheus are optional, not required).
**Verifier identity**: for P0/P1 requirements whose ACs test code the implementing agent wrote, record *who verified*. Raw check output proves what executed and its outcome; it does not prove specification adequacy or reviewer independence. Confidence-high closure requires both inspectable execution evidence and an independent verifier appropriate to risk (P3, A4).

---

<!-- trw:span id=af-5-the-live-quality-model-reference-imple dest=reference class=reference -->
## 5. The Live Quality Model (Reference Implementation)

This section documents exactly what TRW's `trw_prd_validate` computes today, so the framework never overstates the tooling. **Adapt thresholds to your project; the shape is the portable part.**

<!-- trw:span id=af-5-1-scored-dimensions-default-medium-ris dest=reference class=reference -->
### 5.1 Scored dimensions (default, medium-risk)

| Dimension | Weight | What it rewards |
|-----------|-------:|-----------------|
| `traceability` | 35 | real links to source files, tests, dependencies, behavior-proof surfaces |
| `implementation_readiness` | 25 | control points, behavior-switch matrix, key files, proof tests, completion evidence |
| `content_density` | 20 | substance over filler (a hygiene signal, *not* the goal) |
| `structural_completeness` | 20 | required sections + frontmatter present and coherent |

`total_score = (Σ dimension_score / Σ dimension_max) × 100`, capped at 100. Three further dimensions (`smell`, `readability`, `ears_coverage`) carry **weight 0** so they never affect the score. As of v3.0.0, smell and EARS detection ARE computed but surfaced only as informational diagnostics (`smell_findings`, `ears_classifications`, §2.4/§2.1); `readability` remains reserved. `ambiguity_rate` is also reported for information and is **not** part of the score.

<!-- trw:span id=af-5-2-tiers-grades dest=reference class=reference -->
### 5.2 Tiers & grades

| Score (medium-risk default) | Tier | Grade |
|---|---|---|
| ≥ 85 | `approved` | A |
| ≥ 60 | `review` | B |
| ≥ 30 | `draft` | D |
| < 30 | `skeleton` | F |

(There is intentionally no "C".) A DRAFT→REVIEW transition additionally requires content density ≥ the risk-scaled minimum (this density floor gates DRAFT→REVIEW only — not REVIEW→APPROVED). Caution: in the TRW config the threshold field names are historically offset by one tier — `validation_review_threshold` sets the **approved** gate (85), `validation_draft_threshold` the **review** gate (60), and `validation_skeleton_threshold` the **draft** gate (30); trust `risk_profiles.py`, not the field name.

<!-- trw:span id=af-5-3-anti-gaming-features-in-the-live-sco dest=reference class=reference -->
### 5.3 Anti-gaming features in the live scorer
File-path grounding penalty (score decays per hallucinated path); density deliberately down-weighted vs. implementation-readiness so prose can't buy a passing score; AI/agentic-PRD operational-evidence scoring. These exist precisely because the score is gameable (Section 0) — they raise the cost of gaming but do not replace independent review.

---

<!-- trw:span id=af-6-quality-gates-verified-closure dest=core class=normative -->
## 6. Quality Gates & Verified Closure

<!-- trw:span id=af-6-1-prd-authoring-gates-text-level-neces dest=reference class=reference -->
### 6.1 PRD authoring gates (text-level — necessary, not sufficient)
| Gate | Target | Notes |
|------|--------|-------|
| Structure | required sections present | per category variant (see §9) |
| Ambiguity | low | vague-term scan today; full smell taxonomy is backlog |
| Traceability | every FR has source + implementation path + verification evidence path | links are *claims* until verified by 6.2 |
| Tier | meets risk-scaled threshold | a drafting bar, not a quality verdict |

<!-- trw:span id=af-6-2-verified-closure-standard-evidence-l dest=core class=normative -->
### 6.2 Verified-closure standard (evidence-level — the one that counts)
1. **Risk/tier-appropriate review.** Critical/High requirements and any STANDARD+ execution require an independent adversarial reviewer that did *not* author the work (P3, §7-A4). Low-risk/MINIMAL work MAY omit a separate review phase, but self-authored verification remains confidence-medium and the evidence still must be inspectable.
2. **Requirement-matched verification** using the declared method from §2.5. Implemented machine-observable behavior requires behavioral/wiring tests on real paths; analysis, inspection, or demonstration is valid when it is the appropriate method and leaves durable evidence.
3. **Status truthfulness**: `status: implemented` requires the work be functionally live with no undisclosed stubs (§7-A3).
4. **Applicable configured project-native checks** recorded as evidence before delivery (FRAMEWORK.md VALIDATE gate; `trw_build_check`).

A PRD is verified when 6.2 holds — not merely when 6.1's score is high. FRAMEWORK.md's three delivery paths still govern shipment: an acceptable-failure or explicit override may deliver known risk, but it MUST leave the affected requirements unverified and disclose the residual risk.

---

<!-- trw:span id=af-7-anti-patterns-including-the-truthfulne dest=core class=normative -->
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
| **A1** | **Existence ≠ wiring** | writing/reviewing evidence; declaring a surface done | `grep_present`/symbol checks prove a name exists, not that the computed value reaches the output. Real bugs shipped green: an estimate computed then dropped (`estimate=None`); loaded priors silently discarded. | For implemented behavior, assert output *values* on the real path; use the declared method for non-behavioral requirements; require a behavior-proven consumer or disclosed seam for every new public surface (C7). |
| **A2** | **Mock-depth blindness** | judging coverage claims | 90%+ coverage with all-green tests can mock away the unit under test; published industrial reports find AI-written tests can score very low on mutation testing despite high coverage. | Forbid mocking the primary unit under test; require an integration test on a real (in-memory) path; sampled mutation testing (§2.5). |
| **A3** | **`implemented` over stubs** | updating any status field | A status field is text; nothing in the score checks runtime completeness. Audits repeatedly find `status: implemented` with non-empty `stubs[]`. | Enforce: `status: implemented` ⇒ functionally live, `stubs: []`; verify in review/CI, not by reading the claim. |
| **A4** | **Self-review only** | assigning review | The author's tests validate the author's implementation, not the spec; a same-trajectory reviewer inherits the same blind spots. | When risk/tier requires independence, give the reviewer the spec, diff, and evidence but not the author's private trajectory; use focused lenses (correctness / security / evidence / integration). |
| **A5** | **Score-gaming** | reading a validator score | More file refs, more headings, more prose raise the number without raising quality. | Read the §0 operating rule; weight implementation-readiness over density; require A1/A4 evidence regardless of score. |

---

<!-- trw:span id=af-7b-rationalization-watchlist dest=reference class=rationale -->
## 7b. Rationalization Watchlist

When you catch one of these thoughts, stop — it precedes an anti-pattern (mirrors FRAMEWORK.md's implementation watchlist):

| Rationalization | Counter |
|-----------------|---------|
| "Traceability can be filled in later." | Late traceability is incomplete; links made during authoring are far more accurate. |
| "The tests pass and coverage is high, so it's correct." | Coverage and green ≠ correct (A1/A2). Verify behavior on real paths. |
| "It's basically implemented — I'll mark it done." | `implemented` is a truthfulness claim (A3). Disclose stubs or don't claim it. |
| "I wrote it, so I can review it." | Self-review inherits your blind spots (A4). Get an independent pass. |
| "Score is 85, it's ready." | The score is a drafting aid, not a verdict (§0, A5). |
| "The PRD is close enough — start coding." | A PRD with no acceptance criteria, verification methods, or pass conditions makes “done” unverifiable (§2.5, §9). Groom first. |
| "We must eliminate all uncertainty first." | Route uncertainty (C6); don't chase zero. |

---

<!-- trw:span id=af-8-risk-based-rigor-live-profiles-referen dest=reference class=reference -->
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

<!-- trw:span id=af-9-getting-started-in-a-new-project dest=core class=normative -->
## 9. Getting Started in a New Project

<!-- trw:span id=af-minimum-viable-setup dest=core class=normative -->
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

<!-- trw:span id=af-template-variants-sections-required-scal dest=core class=normative -->
### Template variants (sections required scale to category)
| Variant | Categories | Required sections |
|---------|-----------|------------------:|
| Feature | CORE, QUAL, EVAL | 12 |
| Infrastructure | INFRA, LOCAL | 9 |
| Fix | FIX | 8 |
| Research | RESEARCH, EXPLR | 7 |

(There is no single fixed "12-section" rule — section count is category-dependent.)

<!-- trw:span id=af-lifecycle dest=core class=normative -->
### Lifecycle
```
create → groom → risk/tier-required review → exec-plan → IMPLEMENT → VALIDATE → REVIEW when required → DELIVER → audit
```
In the TRW reference: `trw_prd_create` → grooming → independent review when risk/tier requires it → execution planning → sprint execution → `trw_build_check` → `trw_deliver` → post-delivery adversarial audit for P0/P1. Client adapters MAY provide shorthand for the middle stages; light clients use the MCP tools or the manual lifecycle. Sprint grouping is also an adapter concern. The grooming gate is “ready for review” (the score); the **verified-closure** standard is §6.2, while FRAMEWORK.md owns delivery and override semantics.

**Implementation-start gate** (the inverse of A3): if a PRD lacks acceptance criteria, declared verification methods, or objective pass conditions (§2.5) when implementation begins, STOP and groom before writing code. A3 catches false completion at the end; this catches unverifiable scope at the start.

<!-- trw:span id=af-adoption-sequence-for-a-new-project-hone dest=core class=normative -->
### Adoption sequence (for a new project — honest tiers)
1. **Structural floor** (days): PRD template, schema validation, ID uniqueness in CI.
2. **Traceability floor** (weeks): maintained typed links with integrity checks, acceptance criteria + declared verification, independent review on P0/P1.
3. **Intelligence layer** (when justified): semantic search/dedup (C4), role-separated review (C5), calibrated confidence routing (C2). Skipping straight to layer 3 without the floors produces score theater.

<!-- trw:span id=af-reference-implementation-status-not-norm dest=core class=normative -->
### Reference implementation status (not normative)

Keep mutable inventory out of this portable canon; source, Make targets, and the requirements roadmap are authoritative for current counts.

| Surface | Current mode | Scope / known limit |
|---------|--------------|---------------------|
| Core scoring + risk profile | live validator | Structure/readiness/density/traceability scores; does not prove closure. |
| Functionality/status truthfulness | validator + brownfield ratchet | Stronger for new/changed PRDs; legacy aliases and migration remain. |
| Smell/EARS | report/advisory | Informational weight 0. |
| Wiring/seams | warn/advisory v1 | Presence can pass without behavior; seams can suppress broadly; expiry is not universal. |
| Mock-depth / mutation | report or opt-in | Not a universal blocking policy. |
| AC runner | targeted Python/pytest check | Not language-agnostic or a universal CI gate. |
| Measured traceability | live validator field | Exposed separately from declared coverage; still a structural signal, not proof that an artifact is correct. |
| §2.5 verification-method mapping | live 3.2 contract | Typed AC/method/evidence/pass-condition mappings round-trip through template/creator/validator. Missing or malformed mappings block 3.2 Critical/High PRDs; lower-risk and legacy PRDs receive migration warnings. |

- **Remaining gaps:** semantic quality checks for verification mappings, blocking mutation policy, solver-checked conflict satisfiability, stronger wiring/seam enforcement, cross-language AC execution, and reviewed legacy migration.
- **Calibration decision:** smell/EARS scoring remains weight 0 because the corpus calibration found a document-size/content confound that makes non-zero scoring gameable by omission. See `docs/research/aaref-smell-weight-calibration-2026-05-31.md`.

---

<!-- trw:span id=af-10-governance-readiness-mapping-not-comp dest=reference class=reference -->
## 10. Governance-Readiness Mapping (not compliance)

| Instrument / practice | Component | Readiness concern |
|------------|-----------|-------------|
| EU AI Act Art. 14 | C2 | Human oversight |
| FDA SaMD | C2 | Decision support, not replacement |
| HIPAA (if PHI) | C2, C8 | Protected-health-information handling |
| OWASP Top 10 for LLM Applications 2025 + Agentic Applications 2026 | C8 | Injection / disclosure / poisoning defense |
| ISO/IEC 42001 | C1, C9 | AI management system |
| NIST AI RMF 1.0 | C3, C8 | Risk management documentation |

---

<!-- trw:span id=af-11-relationship-to-framework-md dest=reference class=reference -->
## 11. Relationship to FRAMEWORK.md

| | AARE-F (this doc) | FRAMEWORK.md (`v26.1_TRW`) |
|---|---|---|
| Governs | the **specification** (requirement/PRD quality, traceability, verification) | the **execution** (phases, gates, formations, persistence, learning) |
| Key artifact | the PRD | the run (phases + checkpoints + evidence) |
| "Verify" means | the declared test/analysis/inspection/demonstration produces accepted evidence (P5) | VALIDATE gate: project-native checks recorded via `trw_build_check` |
| Shared spine | Evidence over assertion; independent review; risk-scaled rigor | same |

Use them together: AARE-F says *what good looks like and how to prove it*; FRAMEWORK.md says *how to run the work that produces and verifies it*.

---

<!-- trw:span id=af-12-maintenance-versioning dest=core class=compatibility -->
## 12. Maintenance & Versioning

| Review | Frequency | Trigger |
|--------|-----------|---------|
| Standards refresh | ~quarterly | new ISO/INCOSE/EARS or LLM-RE research |
| Major revision | as needed | framework posture change |
| On-trigger | as needed | regulatory change, audit finding, major incident |

**Single-source rule**: this file ships from `trw-mcp/src/trw_mcp/data/aaref.md` and is deployed verbatim; source mirrors MUST stay byte-identical and the recorded framework version MUST match this header. `framework_canons.json` declares the authoring source and every tracked mirror; `scripts/check-aaref-sync.py` enforces that manifest and config-default version. Deployed runtime copies are checked separately by `scripts/check-framework-runtime.py` and the doctor integrity probe.

---

<!-- trw:span id=af-version-history dest=reference class=reference -->
## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-24 | Initial release |
| 1.1.0 | 2026-02-02 | Prompts, indexes, quality gates |
| 2.0.0 | 2026-02-21 | Project-agnostic portable edition; removed hardcoded project references; Getting Started |
| 3.0.0 | 2026-05-31 | **Truthfulness + SOTA reconciliation**: removed residual project contamination and unsourced effectiveness numbers (67%/96%/100%/+28%/+13.2%); replaced fixed confidence-% bands with calibration-first guidance; added the Requirement Quality Standard (EARS + ISO 29148 + INCOSE GtWR v4 + smells + executable ACs); added §0 "what this is/isn't" and the live quality model (real 4-dimension risk-scaled validator); added truthfulness anti-patterns A1–A5; added Specification Primacy (P1) and Evidence-over-Assertion (P6); clarified relationship to FRAMEWORK.md; per-component live/partial/guide status; single-source/sync mandate. |
| **3.1.0** | **2026-06-10** | **Operational sharpening** (research-grounded refinement run): operative summary front-loaded; EARS/Given-When-Then layering (§2.1); ISO 29148 revision note (§2.2); smell-weight evidence update — RE 2025 (§2.4); agent-authored-test caveat + sampled mutation testing (§2.5); autonomous-loop oversight (C2); reviewer iteration policy + NFR-specialized dialectical review for Critical sets (C5); delivered≠wired consumer/seam check (C7); OWASP agentic edition + injection-resistance ACs (C8); false-completion rate metric + verifier identity (C9); A1–A5 "check when" column; implementation-start gate + adoption sequence + portable lifecycle naming (§9); principle citations (P1/P6); risk_level override location (§8). |
| **3.2.0** | **2026-07-09** | **Verification-method, portability, and executable-contract correction**: verification now selects test, analysis, inspection, or demonstration; automated real-path tests remain required for machine-observable implemented behavior unless infeasible. Added typed requirement→AC→method→evidence→pass-condition mappings, risk-aware validator enforcement, measured-traceability output, and one byte-identical template source. Reconciled risk-tier review, human authority, and delivery overrides; reframed C5 as role separation; downgraded C7/wiring enforcement to partial; removed stale recipes/counts/client examples and corrected OWASP naming plus source/runtime sync scope. |

---

<!-- trw:span id=af-appendix-a-glossary dest=reference class=reference -->
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

<!-- trw:span id=af-appendix-b-references dest=reference class=reference -->
## Appendix B: References

- [ISO/IEC/IEEE 29148:2018 — Requirements Engineering](https://www.iso.org/standard/72089.html) (current edition; marked to be revised)
- [INCOSE *Guide to Writing Requirements* v4 summary](https://www.incose.org/docs/default-source/working-groups/requirements-wg/guidetowritingrequirements/incose_rwg_gtwr_v4_summary_sheet.pdf) (INCOSE-TP-2010-006-04, 2023)
- A. Mavin et al., *Easy Approach to Requirements Syntax (EARS)*, IEEE RE 2009
- [NASA Systems Engineering Handbook — verification methods](https://www.nasa.gov/reference/system-engineering-handbook-appendix/) (analysis, inspection, demonstration, test)
- [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) (published Dec 2025); OWASP Top 10 for LLM Applications 2025 — cite the edition you target
- NIST AI RMF 1.0; EU AI Act (2024); ISO/IEC 42001
- 2024–2026 LLM-for-RE literature (ambiguity detection, requirements smells, automated repair; RE 2025 smell-impact-on-LLM-performance findings)
- LLM-assisted mutation testing at production scale (Meta ACH, 2025–2026)
- LLM calibration / uncertainty quantification: Guo et al. 2017 (*On Calibration of Modern Neural Networks*); Kadavath et al. 2022 (*Language Models (Mostly) Know What They Know*); semantic-entropy and conformal-prediction literature (2023–2025)
- Domain assurance (where applicable): DO-178C, ISO 26262, IEC 62304

<!-- trw:span id=af-appendix-c-related-documents dest=reference class=reference -->
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

<!-- trw:span id=af-appendix-d-execution-plan-artifact dest=reference class=reference -->
## Appendix D: Execution Plan Artifact

An execution plan bridges a reviewed PRD to implementation by decomposing FRs into micro-tasks with file paths, test names, verification commands, and a dependency graph.

- **When**: after review returns READY, before implementation; recommended for P0/P1.
- **Storage**: `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`.
- **Principles**: short waves (small, verifiable units of work); function-level granularity (file-level planning misses secondary paths); a declared verification procedure and expected evidence per micro-task (use a command when automatable, a documented analysis/inspection/demonstration protocol otherwise); explicit dependencies.

---

*AARE-F v3.2.0 — AI-Augmented Requirements Engineering Framework (Portable Edition)*
*Truthful · Standards-Aligned · Implementation-Reconciled · Last Updated: 2026-07-09*
