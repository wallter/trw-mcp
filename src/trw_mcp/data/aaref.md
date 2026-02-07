# AI-Augmented Requirements Engineering Framework (AARE-F)

**Version**: 1.1.0
**Last Updated**: 2026-02-02
**Research Basis**: 26-wave systematic research (Waves 7-26)
**Knowledge Entries**: ~150 entries across 5 categories (from ai_v7 source research)
**Target System**: ai_v7 Multi-Agent Psychological Analysis System

---

## Quick Reference

| Attribute | Value |
|-----------|-------|
| **Core Principle** | AI augments human judgment; it does not replace it |
| **Components** | 10 (across 4 layers) |
| **Foundational Principles** | 5 |
| **Evidence Strength** | 68% strong, 24% moderate, 8% limited/theoretical |
| **Implementation Phases** | 4 (Quick Wins -> Compliance) |

---

## Executive Summary

AARE-F is a comprehensive framework for integrating AI/LLM capabilities into requirements engineering while maintaining quality, safety, and compliance. It synthesizes findings from 26 waves of systematic research into actionable components.

**Key Insight**: AI accelerates requirements work but humans must remain in control of decisions. The framework provides structured approaches for confidence routing, uncertainty management, and continuous verification.

---

## 1. Foundational Principles

| # | Principle | Description | Source Waves |
|---|-----------|-------------|--------------|
| **P1** | Traceability First | Every artifact traces to sources and downstream impacts | 8, 10-15 |
| **P2** | Human-in-the-Loop | AI accelerates but humans decide; oversight is mandatory | 9, 14, 15, 19, 20 |
| **P3** | Risk-Based Rigor | Effort scales with consequence; not all requirements need equal treatment | 13-15, 20 |
| **P4** | Semantic Understanding | Embeddings replace keywords as computational substrate | 17-19, 21-22 |
| **P5** | Continuous Verification | Compliance is engineered in, not audited after | 10-11, 14-15, 23 |

---

## 2. Framework Architecture

```
+------------------------------------------------------------------+
|                      FOUNDATION LAYER                             |
|  [C1: Traceability Infrastructure] [C4: Semantic Infrastructure] |
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      GOVERNANCE LAYER                             |
|  [C2: LLM Governance] [C3: Risk Scaling] [C8: Guardrails]        |
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      EXECUTION LAYER                              |
|  [C5: Multi-Agent Orchestration] [C6: Uncertainty] [C10: Conflicts]|
+------------------------------------------------------------------+
                              |
+------------------------------------------------------------------+
|                      OPERATIONS LAYER                             |
|  [C7: Requirements-as-Code] [C9: Observability]                  |
+------------------------------------------------------------------+
```

---

## 3. Core Components

### Foundation Layer

#### C1: End-to-End Traceability Infrastructure
**Purpose**: Enable audit, impact analysis, and provenance tracking.

**Key Requirements**:
- Unique identifiers (URIs) for all artifacts
- Bidirectional link storage (graph or embedded references)
- Automated link integrity validation on change
- Coverage metrics: >= 90% linked artifacts

**Quality Thresholds**:
| Metric | Target |
|--------|--------|
| Trace coverage | >= 90% |
| Impact analysis time | < 5 seconds |
| Audit preparation reduction | 50-70% |

**Knowledge Entries**: KE-SYNTH-001

---

#### C4: Semantic Infrastructure Layer
**Purpose**: Enable semantic understanding as computational substrate.

**Technology Stack**:
```yaml
embedding:
  model: all-mpnet-base-v2
  dimensions: 768
  normalization: L2

storage:
  engine: PostgreSQL + pgvector
  index: HNSW (ef_construction=128, m=16)
  scale: Up to 50M vectors

retrieval:
  search: Hybrid (70% semantic + 30% keyword)
  chunking: 512 tokens, 10-15% overlap
  dedup_threshold: 0.85 cosine similarity
```

**Capabilities**:
1. Semantic similarity search
2. Novelty detection (> 0.85 unique)
3. Deduplication (+28% recall vs. syntactic)
4. Trace link recovery
5. Adaptive retrieval by query complexity

**Knowledge Entries**: KE-SYNTH-008

---

### Governance Layer

#### C2: LLM-in-the-Loop Governance Pattern
**Purpose**: Establish human oversight for AI-assisted decisions.

**Confidence Routing Thresholds**:
| Confidence | Action | Rationale |
|------------|--------|-----------|
| > 95% | Automate with logging | High reliability |
| 85-95% | Flag for expedited review | May need confirmation |
| 70-85% | Required human validation | Significant uncertainty |
| < 70% | Reject or request clarification | Insufficient confidence |

**Human Checkpoints**:
- Input validation (security)
- Confidence threshold routing
- Output verification (quality)
- Override capability (disagreement)

**Regulatory Alignment**: EU AI Act Article 14, FDA SaMD, HIPAA

**Knowledge Entries**: KE-SYNTH-002

---

#### C3: Risk-Based Requirements Rigor Scaling
**Purpose**: Scale engineering effort proportional to impact.

**Risk Classification**:
| Level | Documentation | Verification | Review |
|-------|---------------|--------------|--------|
| Critical | Full formal spec | Independent V&V | Multi-party |
| High | Structured spec | Peer + automated | Technical lead |
| Medium | Standard template | Self + unit tests | Team |
| Low | Lightweight notes | Basic validation | Optional |

**Effort Formula**:
```
Effort = BaseEffort x RiskMultiplier x ComplexityFactor

RiskMultiplier: Critical=4, High=2, Medium=1, Low=0.5
ComplexityFactor: Simple=1, Moderate=1.5, Complex=2
```

**Knowledge Entries**: KE-SYNTH-003

---

#### C8: AI System Guardrails and Safety
**Purpose**: Implement defense-in-depth for AI/LLM systems.

**Minimal Guardrail Set (2025 Standard)**:
1. Input Sanitizer: Prompt injection detection, content validation
2. Output Filter: Harmful content filtering, PII redaction
3. Tool Allowlist: Restrict agentic capabilities
4. Token/Cost Budgets: Prevent resource exhaustion
5. Immutable Logging: Audit trail for all interactions
6. Periodic Attack Testing: Quarterly red team evaluation

**OWASP Top 10 for LLM Focus**:
- Prompt Injection (73% prevalence - CRITICAL)
- Sensitive Information Disclosure
- Data and Model Poisoning

**Knowledge Entries**: KE-SYNTH-013

---

### Execution Layer

#### C5: Multi-Agent Orchestration Pattern
**Purpose**: Coordinate specialized agents for robust analysis.

**Architecture**: Supervisor pattern with 4 threads + shared pool
```
     ORCHESTRATOR (meta-cognitive coordinator)
              |
+------+------+------+------+------+
|      |      |      |      |      |
ANALYSIS  CRITIQUE  SYNTHESIS  LONGITUD.  SHARED
                                          POOL
```

**Decision Protocols**:
| Protocol | Use Case | Performance Gain |
|----------|----------|------------------|
| Voting | Reasoning tasks | +13.2% accuracy |
| Consensus | Knowledge tasks | +2.8% accuracy |
| Majority | Fast decisions | Lowest latency |

**Iteration Strategy**:
| Iteration | Mode | KB Access | Purpose |
|-----------|------|-----------|---------|
| 1 | Isolation | None | Fresh observation, no bias |
| 2 | Contextualization | Comparison | Frame as "what changed?" |
| 3-5 | Dynamic Exploration | Gap-targeted | Fill domain blind spots |

**Stopping Criteria**:
- Patterns >= 5
- Domains >= 4
- Novelty > 50%
- OR: Max 5 iterations

**Knowledge Entries**: KE-SYNTH-010, KE-FRAME-002

---

#### C6: Uncertainty Management Over Zero Defects
**Purpose**: Accept imperfection, quantify confidence, route appropriately.

**Paradigm Shift**: "Zero hallucinations" (impossible) -> "Uncertainty management" (practical)

**Tiered Detection**:
| Tier | When | Cost | Methods |
|------|------|------|---------|
| 1 | Always | 1x | Token probability, source grounding, self-consistency |
| 2 | High stakes | 5x | Semantic entropy, cross-thread validation, human review |
| 3 | Periodic | 10x | LM-Polygraph, multi-model spot-check, accuracy tracking |

**Critical Warning**: High-confidence hallucinations cannot be detected by entropy alone. External grounding required.

**Effectiveness**:
- Multi-agent verification: 67% hallucination reduction
- RAG + RLHF + guardrails: 96% reduction

**Knowledge Entries**: KE-SYNTH-011, KE-FRAME-003

---

#### C10: Conflict Detection and Resolution
**Purpose**: Detect and resolve contradictions.

**Conflict Types**:
| Type | Detection Method |
|------|------------------|
| Intra-domain | Rule-based NLP (100% precision) |
| Inter-domain | MCDM analysis |
| Cross-framework | Semantic comparison |
| NFR Conflicts | Trade-off catalogue |

**Resolution Strategies**:
| Strategy | Use Case |
|----------|----------|
| Risk-based | Safety/security conflicts (higher-risk wins) |
| AHP-TOPSIS | Multi-stakeholder priorities |
| IBIS | Wicked problems (structured argumentation) |

**Knowledge Entries**: KE-SYNTH-006

---

### Operations Layer

#### C7: Requirements-as-Code with DevOps Integration
**Purpose**: Apply software engineering practices to requirements.

**Core Practices**:
```yaml
storage: Git repository (YAML/Markdown)
versioning: Semantic versioning with history
review: PR-based change workflow
validation: Schema + consistency checks in CI
```

**Quality Gates**:
| Stage | Checks |
|-------|--------|
| Pre-commit | Schema validation, required fields, ID uniqueness |
| CI Pipeline | Traceability >= 100%, completeness >= 85%, no conflicts |
| CD Deployment | Approval workflow, audit logging, rollback capability |

**Tool Recommendations**: Doorstop (YAML+LLM), Sphinx-Needs (safety-critical), OpenFastTrace (tracing)

**Knowledge Entries**: KE-SYNTH-012

---

#### C9: Continuous Observability
**Purpose**: Enable continuous monitoring and quality improvement.

**MELT Model**:
| Signal | Examples | Collection |
|--------|----------|------------|
| Metrics | Pattern count, novelty %, coverage | Prometheus |
| Events | Analysis completed, KB updated | Structured events |
| Logs | Validation errors, agent decisions | Structured logging |
| Traces | End-to-end analysis flow | OpenTelemetry |

**Alert Thresholds**:
```yaml
alerts:
  - name: LowTraceabilityCoverage
    condition: trace_coverage < 0.9
    severity: warning
  - name: AnalysisQualityDegradation
    condition: novelty_ratio < 0.5
    severity: critical
```

**Knowledge Entries**: KE-SYNTH-015

---

## 4. Implementation Roadmap

### Phase 1: Quick Wins (Weeks 1-4)
**Effort**: 1 developer | **Impact**: Quality visibility, compliance foundation

| Item | Effort | Priority |
|------|--------|----------|
| Confidence scoring for patterns | 2-3 days | P0 |
| Citation density tracking | 1-2 days | P0 |
| Human oversight documentation | 1 day | P0 |
| Audit logging | 2 days | P0 |
| Token/cost budgets | 1 day | P0 |
| Metrics definition | 1-2 days | P0 |

### Phase 2: Core Infrastructure (Weeks 5-12)
**Effort**: 1-2 developers | **Impact**: 90% context reduction, 50% cost savings

| Item | Effort | Priority |
|------|--------|----------|
| KB embeddings (pgvector) | 3-4 days | P1 |
| Semantic chunking | 2-3 days | P1 |
| Novelty detection | 2-3 days | P1 |
| Adaptive retrieval | 2-3 days | P1 |
| Quality gates in pipeline | 2-3 days | P1 |

### Phase 3: Advanced Capabilities (Weeks 13-24)
**Effort**: 2 developers | **Impact**: 67% hallucination reduction, 30% token efficiency

| Item | Effort | Priority |
|------|--------|----------|
| Multi-agent voting | 3-4 days | P2 |
| Hallucination detection pipeline | 1 week | P2 |
| Memory hierarchy (STM/MTM/LTM) | 1 week | P2 |
| GitOps for KB | 2-3 days | P2 |

### Phase 4: Compliance & Governance (Weeks 25+)
**Effort**: 1 developer + compliance | **Impact**: Regulatory readiness

| Item | Effort | Priority |
|------|--------|----------|
| EU AI Act preparation | 2 weeks | P3 |
| Full guardrails architecture | 1 week | P3 |
| Formal traceability infrastructure | 2 weeks | P3 |

---

## 5. Quality Metrics

### PRD Quality Gates

| Metric | Target | Measurement |
|--------|--------|-------------|
| Ambiguity rate | <= 5% | Count "might", "should consider", "possibly" |
| Completeness score | >= 85% | Required sections present |
| Traceability coverage | >= 90% | Requirements linked to implementation |
| Consistency validation | >= 95% | No internal contradictions |

### Analysis Quality Gates

| Metric | Target | Measurement |
|--------|--------|-------------|
| Output word count | 6000-8000 | wc -w output.md |
| Novel patterns | >= 50% | Patterns not in KB |
| Domain coverage | >= 4 | Unique domains addressed |
| Confidence distribution | Documented | % high/medium/low |

---

## 6. Key Knowledge Entries

### Framework Entries (KE-FRAME-*)
| Entry | Title | Impact | Effort |
|-------|-------|--------|--------|
| KE-FRAME-001 | AI-Augmented RE Framework | 5/5 | 4/5 |
| KE-FRAME-002 | Isolation-First Pattern | 5/5 | 1/5 |
| KE-FRAME-003 | Tiered Hallucination Detection | 5/5 | 3/5 |
| KE-FRAME-004 | Semantic-First Infrastructure | 5/5 | 4/5 |

### Synthesis Entries (KE-SYNTH-*)
| Entry | Title | Component |
|-------|-------|-----------|
| KE-SYNTH-001 | End-to-End Traceability | C1 |
| KE-SYNTH-002 | LLM-in-the-Loop Governance | C2 |
| KE-SYNTH-003 | Risk-Based Rigor Scaling | C3 |
| KE-SYNTH-006 | Conflict Detection | C10 |
| KE-SYNTH-008 | Semantic Infrastructure | C4 |
| KE-SYNTH-010 | Multi-Agent Orchestration | C5 |
| KE-SYNTH-011 | Uncertainty Management | C6 |
| KE-SYNTH-012 | Req-as-Code with DevOps | C7 |
| KE-SYNTH-013 | AI System Guardrails | C8 |
| KE-SYNTH-015 | Continuous Observability | C9 |

---

## 7. Anti-Patterns to Avoid

| Anti-Pattern | Symptom | Mitigation |
|--------------|---------|------------|
| KB Overfitting | All analyses have same patterns | Isolation-first (no KB in Iteration 1) |
| Pattern Entrenchment | No evolution detected | KB as COMPARISON not FILTER |
| Loss of Granularity | Generic, no timestamps | Minimal instruction in isolation |
| Domain Blind Spots | All cognitive/emotional | Dynamic exploration with gap directives |
| Zero Hallucination Goal | Paralysis, over-validation | Uncertainty management paradigm |
| Post-Hoc Compliance | Audit failures | Continuous verification in CI/CD |

---

## 8. Regulatory Alignment

| Regulation | AARE-F Component | Requirement |
|------------|------------------|-------------|
| EU AI Act Article 14 | C2 | Human oversight mandatory |
| FDA SaMD Guidance | C2 | Decision support, not replacement |
| HIPAA | C2, C8 | Psychotherapy notes authorization |
| OWASP Top 10 LLM | C8 | Prompt injection defense |
| ISO/IEC 42001 | C1, C9 | AI management system |
| NIST AI RMF 1.0 | C3, C8 | Risk management documentation |

---

## 9. Maintenance Schedule

| Review Type | Frequency | Trigger |
|-------------|-----------|---------|
| Quarterly | Every 3 months | New research integration |
| Annually | Yearly | Major version revision |
| On Trigger | As needed | Regulatory change, major incident |

---

## 10. Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0.0 | 2026-01-24 | Initial release from 26-wave research |
| 1.1.0 | 2026-02-02 | Added prompts, improved indexes, quality gates |

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| AARE-F | AI-Augmented Requirements Engineering Framework |
| KB | Knowledge Base |
| HITL | Human-in-the-Loop |
| RAG | Retrieval-Augmented Generation |
| MCDM | Multi-Criteria Decision Making |
| STPA | Systems-Theoretic Process Analysis |
| GSN | Goal Structuring Notation |
| SOUP | Software of Unknown Provenance |

## Appendix B: References

- ISO/IEC/IEEE 29148:2018 (Requirements Engineering)
- EU AI Act (2024)
- OWASP Top 10 for LLM (2025)
- NIST AI RMF 1.0
- DO-178C (Aerospace), ISO 26262 (Automotive), IEC 62304 (Medical)

## Appendix C: Related Documents

| Document | Location | Purpose |
|----------|----------|---------|
| Full Research Report | `docs/req-mgmt-research/runs/20260124T060536-fce83e02/reports/final_comprehensive_report.md` | Complete findings |
| Comprehensive Framework | `docs/req-mgmt-research/runs/20260124T060536-fce83e02/shards/wave25/comprehensive_framework.md` | Detailed specifications |
| Knowledge Entries | `docs/knowledge-catalogue/` | Structured entry database |
| PRD Template | `docs/requirements-aare-f/prds/TEMPLATE.md` | AARE-F compliant template |
| Prompts | `docs/requirements-aare-f/prompts/` | Requirements management prompts |

---

*AARE-F v1.1.0 - AI-Augmented Requirements Engineering Framework*
*Research Basis: 26-wave systematic research initiative*
*Last Updated: 2026-02-02*
