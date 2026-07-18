# AI-Augmented Requirements Engineering Framework (AARE-F)

**Version**: 3.2.0
**Last Updated**: 2026-07-09
**Purpose**: Project-agnostic framework for engineering requirements with AI assistance — truthful, verifiable, and aligned to current requirements-engineering standards.
**Research Basis**: ISO/IEC/IEEE 29148:2018 (confirmed current in 2024 and marked for revision in 2026), INCOSE *Guide to Writing Requirements* v4 (2023), EARS (Mavin et al.), requirements-engineering V&V practice, and TRW's empirical findings (eval iterations and the PRD-audit corpus).

> **Companion documents.** AARE-F defines *what a good requirement/PRD is and how to verify it*. [`FRAMEWORK.md`](FRAMEWORK.md) (`v26.1_TRW`) defines *how work is executed* (the 6-phase RESEARCH→PLAN→IMPLEMENT→VALIDATE→REVIEW→DELIVER model, gates, formations). They are complementary: AARE-F governs the **specification**; FRAMEWORK.md governs the **execution**. Neither restates the other. For how TRW operationalizes AARE-F day-to-day, see `docs/documentation/aare-f-overview.md` (TRW monorepo path — not present in standalone deployments).

> **Operative summary (read this even under context pressure).** Verified closure is §6.2: risk-appropriate independent review + requirement-matched verification evidence + status truthfulness + recorded project-native validation. A delivery override may ship known risk, but it does not make the requirement verified. The validator score (§5) is a drafting aid, not a verdict (§0). The anti-patterns that actually ship defects are A1 (existence ≠ wiring), A3 (`implemented` over stubs), and A4 (self-review only) — check them at evidence design, status update, and reviewer assignment respectively (§7). A PRD without acceptance criteria and declared verification methods is not ready to implement (§9).

---

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

> **Implementation status (TRW reference):** the live validator **detects requirement smells** (weak modal, vague adverb, subjective, escape clause, open-ended, superlative, absolute, compound, vague pronoun) and surfaces them as **informational** `smell_findings` — each with a line number, matched text, and a fix suggestion. They are advisory: `validation_smell_weight` stays 0, so smells never change the score. A separate `ambiguity_rate` is also reported and is not scored. *Scoring* smells (non-zero weight) remains deferred: the corpus calibration found a size/content confound that would reward omission and make the score easier to game.

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

## 4. Core Components

#### C1: End-to-End Traceability Infrastructure — [partial]
**Purpose**: Audit, impact analysis, and provenance.
**Key requirements**: stable IDs for artifacts; maintained links (`implements / depends_on / enables` plus project-defined typed relations); link-integrity validation on change; requirement→implementation→verification linkage whose evidence can be executed or independently inspected.
**Reference status**: the TRW reference has a link schema, traceability matrix,
and separate declared/measured coverage fields exposed by `trw_prd_validate`.
Generated PRDs do not retain a first-class `conflicts_with` relation, and
automated impact analysis is not first-class. Treat those as partial, not live.

#### C4: Semantic Infrastructure Layer — [guide]
**Purpose**: Semantic search/dedup/novelty as substrate for requirements work.
**Guidance**: use semantic retrieval only when it improves a declared requirements task (discovery, deduplication, impact analysis). Preserve source/provenance, combine lexical and semantic evidence when useful, and calibrate models, chunking, fusion, and similarity thresholds on the project's own corpus. A vector store, embedding model, or fixed threshold is an adapter choice, not part of AARE-F.

#### C8: AI Guardrails & Safety — [partial]
**Minimal set**: input sanitizer (prompt-injection detection) · output filter (harmful-content / PII) · tool allowlist · token/cost budgets · immutable audit log · periodic adversarial testing.
**OWASP focus**: for agent-executed workflows treat the **Top 10 for Agentic Applications 2026** (published Dec 2025) as primary — goal hijack, tool misuse, identity/privilege abuse, memory/context poisoning, excessive agency — alongside the LLM Applications 2025 edition. Cite the edition you target; prevalence figures are edition- and context-specific — don't quote a bare percentage.
**Requirement-level guardrail**: a requirement that directs an agent to read external content (files, URLs, retrieval results, tool outputs) SHOULD carry an injection-resistance acceptance criterion — indirect injection through content is the production exploit class.

#### C6: Uncertainty Management over Zero-Defects — [guide]
**Paradigm**: "eliminate all hallucination" is unreachable → **quantify and route uncertainty** instead.

| Tier | When | Relative cost | Methods |
|------|------|---------------|---------|
| 1 | Always | low | source grounding, provenance, explicit unknowns, requirement-matched checks |
| 2 | High stakes | medium | independent cross-checker/verifier, human review, adversarial examples |
| 3 | Periodic | high | calibration audits and statistically justified routing methods when the required signals exist |

**Critical warning**: high-confidence hallucinations cannot be caught by entropy alone — external grounding is required.
**On effectiveness numbers**: do not publish bare "N% hallucination reduction" figures (prior versions cited 67%/96% — both unsourced and untransferable). Reported reductions depend entirely on the hallucination definition, model, task, and dataset. State the method and require local measurement.

#### C10: Conflict Detection & Resolution — [partial]
**Conflict types & detection**: intra-requirement (rule-based NLP — high precision when rules are logically sound and maintained; do **not** claim 100%) · inter-domain (multi-criteria analysis) · cross-artifact (semantic comparison) · NFR trade-offs (trade-off catalogue).
**Resolution**: risk-based (higher-risk wins for safety/security) · AHP-TOPSIS (multi-stakeholder) · IBIS (wicked problems).
**Reference status**: conflict-analysis prompts are live guidance; there is no automated cross-requirement conflict detector or CI gate. Automated detection/resolution and solver-checked joint satisfiability remain aspirational.

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

### 6.1 PRD authoring gates (text-level — necessary, not sufficient)
| Gate | Target | Notes |
|------|--------|-------|
| Structure | required sections present | per category variant (see §9) |
| Ambiguity | low | vague-term scan today; full smell taxonomy is backlog |
| Traceability | every FR has source + implementation path + verification evidence path | links are *claims* until verified by 6.2 |
| Tier | meets risk-scaled threshold | a drafting bar, not a quality verdict |

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

## 11. Relationship to FRAMEWORK.md

| | AARE-F (this doc) | FRAMEWORK.md (`v26.1_TRW`) |
|---|---|---|
| Governs | the **specification** (requirement/PRD quality, traceability, verification) | the **execution** (phases, gates, formations, persistence, learning) |
| Key artifact | the PRD | the run (phases + checkpoints + evidence) |
| "Verify" means | the declared test/analysis/inspection/demonstration produces accepted evidence (P5) | VALIDATE gate: project-native checks recorded via `trw_build_check` |
| Shared spine | Evidence over assertion; independent review; risk-scaled rigor | same |

Use them together: AARE-F says *what good looks like and how to prove it*; FRAMEWORK.md says *how to run the work that produces and verifies it*.

---

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
- **Principles**: short waves (small, verifiable units of work); function-level granularity (file-level planning misses secondary paths); a declared verification procedure and expected evidence per micro-task (use a command when automatable, a documented analysis/inspection/demonstration protocol otherwise); explicit dependencies.

---

*AARE-F v3.2.0 — AI-Augmented Requirements Engineering Framework (Portable Edition)*
*Truthful · Standards-Aligned · Implementation-Reconciled · Last Updated: 2026-07-09*


<!-- GENERATED FILE -- do not edit. Source: aaref.source.md. Regenerate: python3 scripts/compile-framework-canons.py --write. compiler_schema=1. -->
