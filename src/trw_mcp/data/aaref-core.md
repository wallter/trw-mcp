# AI-Augmented Requirements Engineering Framework (AARE-F)

**Version**: 3.2.0
**Last Updated**: 2026-07-09
**Purpose**: Project-agnostic framework for engineering requirements with AI assistance — truthful, verifiable, and aligned to current requirements-engineering standards.
**Research Basis**: ISO/IEC/IEEE 29148:2018 (confirmed current in 2024 and marked for revision in 2026), INCOSE *Guide to Writing Requirements* v4 (2023), EARS (Mavin et al.), requirements-engineering V&V practice, and TRW's empirical findings (eval iterations and the PRD-audit corpus).

> **Companion documents.** AARE-F defines *what a good requirement/PRD is and how to verify it*. [`FRAMEWORK.md`](FRAMEWORK.md) (`v26.1_TRW`) defines *how work is executed* (the 6-phase RESEARCH→PLAN→IMPLEMENT→VALIDATE→REVIEW→DELIVER model, gates, formations). They are complementary: AARE-F governs the **specification**; FRAMEWORK.md governs the **execution**. Neither restates the other. For how TRW operationalizes AARE-F day-to-day, see `docs/documentation/aare-f-overview.md` (TRW monorepo path — not present in standalone deployments).

> **Operative summary (read this even under context pressure).** Verified closure is §6.2: risk-appropriate independent review + requirement-matched verification evidence + status truthfulness + recorded project-native validation. A delivery override may ship known risk, but it does not make the requirement verified. The validator score (§5) is a drafting aid, not a verdict (§0). The anti-patterns that actually ship defects are A1 (existence ≠ wiring), A3 (`implemented` over stubs), and A4 (self-review only) — check them at evidence design, status update, and reviewer assignment respectively (§7). A PRD without acceptance criteria and declared verification methods is not ready to implement (§9).

---

## 0. What This Framework Is — and Is Not

Read this before using any threshold or score below.

**AARE-F is**: a discipline for writing requirements that are unambiguous, singular, verifiable, and traceable; storing them as versioned artifacts (PRDs); and verifying them with evidence rather than assertion. Its durable value is **structural**: a consistent artifact shape makes independent adversarial review tractable and makes requirement→implementation→verification traceability checkable.

**AARE-F is NOT** a quality oracle. The validator's numeric score (Section 5) is a **drafting aid that surfaces missing structure** — it is **not** a predictor of implementation success, and a high score does **not** mean the work is correct. This is an evidence-based position, not modesty:

- No controlled study (internal or published) shows that a higher PRD score predicts fewer defects or less rework. Sub-signals (completeness, low ambiguity, real traceability) track readiness better than the aggregate score.
- Scores are **gameable** by surface formatting (more file references, more section headings, more prose) independent of implementation quality. The validator is hardened against the worst cases but cannot close the gap with text analysis alone.
- The failures that actually ship — *computed-but-discarded values, mocked-away data paths, `status: implemented` over unfinished stubs* — are **invisible to text scoring**. They are caught by **independent adversarial review and behavioral tests**, not by the score.

**Operating rule**: Treat the score as "is this draft structurally ready for review?" Treat **risk-appropriate independent review + requirement-matched verification evidence** as the actual quality gate. When the two disagree, trust the evidence, not the number. (Value hierarchy: **Truthfulness > Quality > Knowledge > Velocity**.)

---

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

## 2. The Requirement Quality Standard

This is the heart of AARE-F: a concrete, standards-aligned definition of a good requirement. It unifies EARS (phrasing), ISO/IEC/IEEE 29148 (characteristics), INCOSE GtWR v4 (rules), a requirements-smells taxonomy (detection), and acceptance criteria with declared verification evidence.

### 2.2 ISO/IEC/IEEE 29148:2018 — Quality Characteristics

Each requirement (individual) and the requirement set (collective) MUST satisfy:

| Level | Characteristics |
|-------|-----------------|
| **Individual** | Necessary · Appropriate · Unambiguous · Complete · Singular · Feasible · Verifiable · Correct · Conforming |
| **Set** | Complete · Consistent · Feasible · Comprehensible · Validatable · Correct (no duplicates, no conflicts, homogeneous language) |

> ISO lists the 2018 edition as current (confirmed in 2024) and at stage 90.92 “to be revised” since February 2026, with a committee draft under development. Treat 2018 as operative until a replacement publishes, then run a conformance review.

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

### Foundation Layer

### Governance Layer

#### C2: AI-in-the-Loop Governance — [partial]
**Purpose**: Keep human authority explicit over AI-assisted decisions while allowing bounded automation.
**Authority checkpoints**: input validation → confidence-aware routing → output verification → residual-risk acceptance → human override. A project MAY pre-authorize automation inside defined risk and evidence bounds; it MUST preserve an accountable human escalation path for consequential exceptions.

> **Confidence routing — evidence first.** Earlier versions published fixed percentage bands (">95% automate", etc.). Do not use uncalibrated verbalized-confidence thresholds: a number from one model/task/harness does not transfer automatically. Define the observable signal, local error evidence, routing action, and fallback. When a harness exposes suitable probabilities, calibration or conformal methods MAY help; when it does not, use source grounding, independent checks, explicit unknowns, and human escalation. Technique names are optional recipes, not portable requirements.

**Governance/readiness alignment** (where applicable; not compliance): EU AI Act human oversight, FDA SaMD decision support, ISO/IEC 42001, NIST AI RMF.

> **Autonomous-loop oversight.** When requirements work runs inside unattended loops, human checkpoints degrade to theater unless escalation is structurally protected: a stop-recommendation overridden repeatedly MUST escalate on a channel the loop cannot disable, and cycle closure MUST be outcome-gated (a declared success criterion moved, or a documented rationale) rather than throughput-gated. A loop that can suppress its own diagnostics inverts the authority hierarchy (P3).

#### C3: Risk-Based Rigor Scaling — [partial]
**Purpose**: Scale effort and proof to consequence. Risk-scaled score thresholds, density floors, and dimension weights are live in the TRW validator (Section 8). The review/V&V assignments below are normative guidance; the current review tool does not enforce them from `risk_level`.

| Level | Documentation | Verification | Review |
|-------|---------------|--------------|--------|
| Critical | Full spec + ACs | Declared method + independent V&V | Multi-party |
| High | Structured spec | Declared method + independent verification | Technical lead or delegated authority |
| Medium | Standard template | Declared method + project-native evidence | STANDARD review rule or team policy |
| Low | Lightweight source/ACs | Basic requirement-matched evidence | Optional unless execution tier requires it |

Apply the stricter of requirement risk and FRAMEWORK.md execution tier.

### Execution Layer

#### C5: Independent Analysis & Role Separation — [partial]
**Purpose**: separate authoring, critique, verification, and risk acceptance so one context does not self-certify its own assumptions.

Roles are logical, not a mandate for multiple agents: humans, independent sessions, tools, or sub-agents MAY fill them. A low-risk set may use one actor sequentially with an explicit cold pass; Critical/High-risk sets require an independent critic/verifier. When a harness supports delegation, this maps onto FRAMEWORK.md formations (SINGLE-TRACK / MAP-REDUCE / PIPELINE / DEBATE+CRITIC+JUDGE).

**Isolation-first**: an independent reviewer gets the specification, diff/artifacts, and evidence — not the author's private trajectory. Pass 1 challenges the work without the author's conclusions; later passes reconcile evidence and target concrete gaps. Stop when a pass yields no new material finding. For Critical-risk sets, assign lenses by relevant quality family (for example safety, security, performance) and resolve cross-quality conflicts before verified closure.

### Operations Layer

#### C7: Requirements-as-Code with DevOps — [partial]
**Practices**: PRDs as versioned Markdown+YAML in git; semantic versioning + history; PR-based change; schema/consistency checks runnable on demand and in CI.

| Stage | Checks |
|-------|--------|
| Pre-commit | schema validity, required fields, ID uniqueness |
| CI | structure + traceability + (target) executable-AC pass-rate; conflict checking is a target until C10 has an automated gate |
| Delivery | approval recorded, audit log, rollback path, requirement-matched evidence, and **wiring verified** for public behavior — a surface marked `implemented` has ≥1 behavior-proven consumer or an explicit seam entry with owner + expiry. Existence without consumption is A1 at architecture scale. |

**Reference status**: versioned PRDs and several schema/consistency checks are live. Consumer/wiring analysis is advisory v1: presence can satisfy some checks without proving behavior, a seam can suppress warnings broadly, and expiry is not a universal delivery gate. Treat “wiring verified” as the required target state, not a claim that current tooling enforces it fully.

#### C9: Continuous Observability — [partial]
**MELT**: Metrics (coverage, novelty, AC pass-rate, requirement churn, **false-completion rate** — delivers whose claimed outcome was later contradicted ÷ total delivers) · Events (validated/groomed/delivered) · Logs (validation + decision trail) · Traces (end-to-end flow). Use whatever telemetry stack the project already has (structured logging is the floor; OpenTelemetry/Prometheus are optional, not required).
**Verifier identity**: for P0/P1 requirements whose ACs test code the implementing agent wrote, record *who verified*. Raw check output proves what executed and its outcome; it does not prove specification adequacy or reviewer independence. Confidence-high closure requires both inspectable execution evidence and an independent verifier appropriate to risk (P3, A4).

---

## 6. Quality Gates & Verified Closure

### 6.2 Verified-closure standard (evidence-level — the one that counts)
1. **Risk/tier-appropriate review.** Critical/High requirements and any STANDARD+ execution require an independent adversarial reviewer that did *not* author the work (P3, §7-A4). Low-risk/MINIMAL work MAY omit a separate review phase, but self-authored verification remains confidence-medium and the evidence still must be inspectable.
2. **Requirement-matched verification** using the declared method from §2.5. Implemented machine-observable behavior requires behavioral/wiring tests on real paths; analysis, inspection, or demonstration is valid when it is the appropriate method and leaves durable evidence.
3. **Status truthfulness**: `status: implemented` requires the work be functionally live with no undisclosed stubs (§7-A3).
4. **Applicable configured project-native checks** recorded as evidence before delivery (FRAMEWORK.md VALIDATE gate; `trw_build_check`).

A PRD is verified when 6.2 holds — not merely when 6.1's score is high. FRAMEWORK.md's three delivery paths still govern shipment: an acceptable-failure or explicit override may deliver known risk, but it MUST leave the affected requirements unverified and disclose the residual risk.

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
| **A1** | **Existence ≠ wiring** | writing/reviewing evidence; declaring a surface done | `grep_present`/symbol checks prove a name exists, not that the computed value reaches the output. Real bugs shipped green: an estimate computed then dropped (`estimate=None`); loaded priors silently discarded. | For implemented behavior, assert output *values* on the real path; use the declared method for non-behavioral requirements; require a behavior-proven consumer or disclosed seam for every new public surface (C7). |
| **A2** | **Mock-depth blindness** | judging coverage claims | 90%+ coverage with all-green tests can mock away the unit under test; published industrial reports find AI-written tests can score very low on mutation testing despite high coverage. | Forbid mocking the primary unit under test; require an integration test on a real (in-memory) path; sampled mutation testing (§2.5). |
| **A3** | **`implemented` over stubs** | updating any status field | A status field is text; nothing in the score checks runtime completeness. Audits repeatedly find `status: implemented` with non-empty `stubs[]`. | Enforce: `status: implemented` ⇒ functionally live, `stubs: []`; verify in review/CI, not by reading the claim. |
| **A4** | **Self-review only** | assigning review | The author's tests validate the author's implementation, not the spec; a same-trajectory reviewer inherits the same blind spots. | When risk/tier requires independence, give the reviewer the spec, diff, and evidence but not the author's private trajectory; use focused lenses (correctness / security / evidence / integration). |
| **A5** | **Score-gaming** | reading a validator score | More file refs, more headings, more prose raise the number without raising quality. | Read the §0 operating rule; weight implementation-readiness over density; require A1/A4 evidence regardless of score. |

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
create → groom → risk/tier-required review → exec-plan → IMPLEMENT → VALIDATE → REVIEW when required → DELIVER → audit
```
In the TRW reference: `trw_prd_create` → grooming → independent review when risk/tier requires it → execution planning → sprint execution → `trw_build_check` → `trw_deliver` → post-delivery adversarial audit for P0/P1. Client adapters MAY provide shorthand for the middle stages; light clients use the MCP tools or the manual lifecycle. Sprint grouping is also an adapter concern. The grooming gate is “ready for review” (the score); the **verified-closure** standard is §6.2, while FRAMEWORK.md owns delivery and override semantics.

**Implementation-start gate** (the inverse of A3): if a PRD lacks acceptance criteria, declared verification methods, or objective pass conditions (§2.5) when implementation begins, STOP and groom before writing code. A3 catches false completion at the end; this catches unverifiable scope at the start.

### Adoption sequence (for a new project — honest tiers)
1. **Structural floor** (days): PRD template, schema validation, ID uniqueness in CI.
2. **Traceability floor** (weeks): maintained typed links with integrity checks, acceptance criteria + declared verification, independent review on P0/P1.
3. **Intelligence layer** (when justified): semantic search/dedup (C4), role-separated review (C5), calibrated confidence routing (C2). Skipping straight to layer 3 without the floors produces score theater.

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

## 12. Maintenance & Versioning

| Review | Frequency | Trigger |
|--------|-----------|---------|
| Standards refresh | ~quarterly | new ISO/INCOSE/EARS or LLM-RE research |
| Major revision | as needed | framework posture change |
| On-trigger | as needed | regulatory change, audit finding, major incident |

**Single-source rule**: this file ships from `trw-mcp/src/trw_mcp/data/aaref.md` and is deployed verbatim; source mirrors MUST stay byte-identical and the recorded framework version MUST match this header. `framework_canons.json` declares the authoring source and every tracked mirror; `scripts/check-aaref-sync.py` enforces that manifest and config-default version. Deployed runtime copies are checked separately by `scripts/check-framework-runtime.py` and the doctor integrity probe.

---


<!-- GENERATED FILE -- do not edit. Source: aaref.source.md. Regenerate: python3 scripts/compile-framework-canons.py --write. compiler_schema=1. -->
