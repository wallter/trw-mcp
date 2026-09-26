---
prd:
  id: PRD-CORE-293
  title: Delete the dead reward loop (q_value, q_observations, helpful/unhelpful counts)
  version: '1.0'
  status: implemented
  priority: P1
  category: CORE
  risk_level: medium
  ip_tier: public
  ip_rationale: The fields, their writers and readers live in trw-mcp and trw-memory (both BSL-1.1, published to PyPI); the removal and its tests are public code. No proprietary eval asset, judge prompt or intelligence-pipeline code is touched.
  ai_operational: false
  safety_critical: false
  functionality_level: live
  stubs: []
  evidence:
    level: strong
    sources:
    - 'trw-mcp/src/trw_mcp/tools/_deferred_steps_learning.py:45-48: _step_outcome_correlation returns {"status": "skipped", "updated": 0} by design ("R10: delivery is not per-learning usefulness evidence"); _step_recall_outcome likewise. process_outcome_for_event -> update_q_value (trw-mcp/src/trw_mcp/scoring/_correlation.py:122) is reachable only from that step.'
    - 'helpful_count/unhelpful_count are written only by trw_learn_update(feedback=...) (trw-mcp/src/trw_mcp/state/_memory_update.py:311-312); nothing calls it automatically.'
    - 'Live project store, read 2026-09-22 (sqlite3 -readonly .trw/memory/memory.db, 1,617 rows): helpful_count>0 0, unhelpful_count>0 0, q_observations>0 0, 13 distinct q_value (creation seeds from compute_initial_q_value, trw-mcp/src/trw_mcp/scoring/_correlation.py:60). The 399 non-empty outcome_history rows hold only team-sync "conflict_merged:..." markers, no outcomes.'
    - 'One reachable writer existed besides the skipped step: trw_deliver''s gate (trw-mcp/src/trw_mcp/tools/_delivery_helpers.py, check_delivery_gates) called apply_contradiction_penalty (trw-mcp/src/trw_mcp/scoring/_contradiction_penalty.py) for learnings recalled in the session and then contradicted, writing q_value/q_observations/outcome_history through scoring/_io_sqlite_sync.py. It never fired: q_observations = 0 on all 1,617 rows. apply_proximal_rewards (nudge->action rewards) had no production caller. Decision 2026-09-22 (lead): the penalty is DROPPED, not moved onto impact -- silently editing a user-owned field is worse than no penalty, and the retraction nudge (tools/_retraction_nudge.py, read-only) keeps the visible path for the agent to update or retract the learning.'
    - 'PRD-CORE-292 bisect (implementation notes): execute_recall''s rank_targeted_by_utility re-ranked retrieval output with utility ties from these seeds and dropped gold rows on 7 of 48 EngMem cells; it was removed from trw_recall.'
  confidence:
    implementation_feasibility: 0.8
    requirement_clarity: 0.85
    estimate_confidence: 0.7
    test_coverage_target: 0.85
  traceability:
    implements: []
    depends_on:
    - PRD-CORE-292
    enables: []
  metrics:
    success_criteria:
    - 'No source file in trw-mcp/src or trw-memory/src reads or writes q_value, q_observations, helpful_count or unhelpful_count, and the trw_learn_update feedback parameter is gone; a repository test enforces it.'
    - 'The export -> reinstall -> import round trip preserves every learning (id, summary, detail, tags, evidence, impact) from a store that still carries the removed fields; the gate fails on any lost or altered learning.'
    - 'EngMem at 1k/5k/20k (trw-mcp/benchmarks/engmem_gate.py against the PRD-CORE-292 post-change fixture) shows no per-query regression on trw_recall or trw_session_start.'
    - 'Net source eLOC across trw-mcp/src and trw-memory/src is negative and reported.'
    measurement_method:
    - 'A new repository test module that greps both src trees for the removed identifiers.'
    - 'A round-trip test: seed a store with the old columns populated, export, import into a fresh store, compare learnings field by field.'
    - 'trw-mcp/benchmarks/engmem_gate.py against the PRD-CORE-292 post-change fixture.'
    - 'AST effective-LOC count over changed src files (scripts/_loc_baseline.py counter).'
  quality_gates:
    ambiguity_rate_max: 0.05
    completeness_min: 0.85
    traceability_coverage_min: 0.9
    consistency_validation_min: 0.95
verification_commands:
- .venv/bin/python -m pytest -q trw-mcp/tests/test_export_import.py  # plus the new identifier-absence test module (FR01)
- .venv/bin/python trw-mcp/benchmarks/engmem_gate.py --baseline <PRD-CORE-292 post-change fixture> --candidate <run dir>
- make check
---

# PRD-CORE-293: Delete the dead reward loop

## 1. Problem Statement

TRW learnings carry a reward loop — `q_value`, `q_observations`, `helpful_count`, `unhelpful_count` — that no longer receives signal. Q-value updates run only from the delivery step `_step_outcome_correlation`, skipped by design (R10: a delivery is not evidence that a particular learning was useful). The helpful/unhelpful counters change only on an explicit `trw_learn_update(feedback=...)` that nothing issues. The live store shows zero observations in 1,617 rows.

The fields still cost: about 45 source files reference them, three rankings read them as if they carried signal, and PRD-CORE-292 measured one of those (`rank_targeted_by_utility`) dropping relevant results on seed-value ties.

## 2. Goals & Non-Goals

Goals: remove the fields, writers, readers, tuning config and the `feedback` parameter from trw-mcp and trw-memory; give each reader an explicit remaining signal or remove it; prove through the existing export/import round trip that no learning is lost.

Non-goals: designing a new usefulness signal (a future one needs its own PRD and evidence); in-place schema migration code (PLAN §0) — existing databases keep the unused columns, new ones are created without them.

## 3. User Stories

- As an agent, the ranking I receive reflects signals that exist, not seed values that look like evidence.
- As a maintainer, I do not keep ~45 files of scoring code alive for a loop nothing feeds.
- As an operator upgrading through the one major release, every learning survives export and import.

## 4. Functional Requirements

### PRD-CORE-293-FR01: Fields and writers removed [confidence: 0.85]
**Status**: active
The four fields SHALL be removed from `trw-mcp/src/trw_mcp/models/learning.py`, `trw-mcp/src/trw_mcp/models/typed_dicts/_learning.py` (the `q_value` key), the trw-memory entry model and row mappers (`trw-memory/src/trw_memory/storage/_row_mapper.py`, `trw-memory/src/trw_memory/storage/_yaml_row_mapper.py`), and from the `CREATE TABLE` in `trw-memory/src/trw_memory/storage/_schema.py` for new stores. `compute_initial_q_value`, the outcome-correlation path in `trw-mcp/src/trw_mcp/scoring/_correlation.py`, the contradiction penalty and its call from the delivery gate, the uncalled proximal-reward path, the build-check Q-learning worker and its health reporter (no compatibility stub), the skipped roster entries in `trw-mcp/src/trw_mcp/tools/_deferred_steps_learning.py`, the `feedback` parameter handled in `trw-mcp/src/trw_mcp/state/_memory_update.py`, and the config fields that only tuned the loop SHALL be deleted. A new repository test SHALL fail if either src tree names a removed identifier.

### PRD-CORE-293-FR02: Decay is an explicit function of what remains [confidence: 0.8]
**Status**: active
`feedback_decay_score` in `trw-memory/src/trw_memory/lifecycle/scoring.py` computes `importance * max(min_factor, 0.95 ** (recall_count / max(1, helpful_count)))`; with `helpful_count` always 0 it is silently `0.95 ** recall_count`. It SHALL become an explicitly named function of `importance`, `recall_count` and the existing floor, numerically identical on the live distribution, pinned by a test.

### PRD-CORE-293-FR03: Promotion, consolidation and analytics rank by a stated signal [confidence: 0.75]
**Status**: active
`trw-mcp/src/trw_mcp/state/claude_md/_promotion.py` ranks by `q_value` only when `q_observations` reaches a threshold (never) and otherwise by `impact`; it SHALL rank by `impact` explicitly. `trw-mcp/src/trw_mcp/state/consolidation/_cycle.py` (cluster max `q_value`) and `trw-mcp/src/trw_mcp/state/analytics/entries.py` (q-value filters) SHALL use `impact` or drop the q-based view. The remaining callers of `rank_targeted_by_utility` (`trw-mcp/src/trw_mcp/tools/_session_recall_helpers.py`, `trw-mcp/src/trw_mcp/tools/_session_recall_phase.py`) SHALL keep retrieval order, as `trw_recall` does since PRD-CORE-292, or name the one live signal they rank by.

### PRD-CORE-293-FR04: The round trip preserves every learning [confidence: 0.85]
**Status**: active
Two paths, two tests, each failing on any loss or import error. (a) trw-memory's own-format `trw-memory import` (lossless since main 7c0685d5d): `trw-memory/tests/test_core293_legacy_reward_columns.py` SHALL import a 2.0.0 export whose rows populate every removed key and assert every id and every kept field is identical and the removed keys are gone. (b) trw-mcp's cross-project `import_learnings` in `trw-mcp/src/trw_mcp/export.py`, which mints fresh ids by design: `trw-mcp/tests/test_export_import.py` SHALL export learnings carrying every removed field, import them into a fresh project and assert summary, detail, tags, evidence and impact are identical, with exactly one new id per learning.

## 5. Non-Functional Requirements

### PRD-CORE-293-NFR01: No ranking regression [confidence: 0.7]
**Status**: active
`trw-mcp/benchmarks/engmem_gate.py` against the PRD-CORE-292 post-change fixture SHALL pass at 1k/5k/20k for `trw_recall` and `trw_session_start`; N=16, run count and raw outcomes recorded, no parity claim beyond the fixture.

### PRD-CORE-293-NFR02: Net-negative footprint, gates green [confidence: 0.8]
**Status**: active
Net source eLOC across both packages SHALL be negative (AST counter) and `make check` SHALL pass. The removed public surface (`feedback` parameter, fields) is listed for the major release's notes.

## 6. Technical Approach

### Primary Control Points
| Control point | Change |
|---|---|
| `trw-mcp/src/trw_mcp/scoring/_correlation.py` | delete seeding and outcome correlation |
| `trw-memory/src/trw_memory/lifecycle/scoring.py` | decay as a function of importance and recall_count |
| `trw-mcp/src/trw_mcp/state/claude_md/_promotion.py` | rank by impact |
| `trw-memory/src/trw_memory/storage/_schema.py` | new stores without the columns; no migration |
| `trw-mcp/src/trw_mcp/export.py` | unchanged behaviour; round trip proven |

### Behavior Switch Matrix
| Input | Before | After |
|---|---|---|
| `trw_learn_update(feedback=...)` | increments a counter nothing reads usefully | parameter removed |
| promotion ranking | `impact` (q branch unreachable) | `impact`, stated |
| decay | `0.95 ** recall_count` via `max(1, 0)` | same value, explicit formula |
| import of a row carrying removed fields | fields dropped | fields dropped, gated |

## 7. Test Strategy
Identifier-absence test module (new), decay-formula test, updated promotion/consolidation/analytics tests, the FR04 round trip in `trw-mcp/tests/test_export_import.py`, and the EngMem gate. Tests that only asserted removed behaviour are deleted with it.

## 8. Rollout Plan
One change set in the major release. Rollback is a code revert and never rewrites stored data. Checked in slice 4 (2026-09-22): a pre-change build opens a database created without the columns but every read and write fails with `no such column: q_value`, so rollback of a store CREATED by this release goes through export/import, not a binary downgrade. A store UPGRADED from 2.0.0 keeps the columns (R1) and stays readable by the pre-change build; rows this release writes there take the column defaults.

## 9. Success Metrics
See frontmatter `success_criteria`.

## 10. Dependencies & Risks
Depends on PRD-CORE-292 (its fixture and the `rank_targeted_by_utility` removal from `trw_recall`). Risk: a platform, telemetry or sync payload serializes a removed field (see Open Questions); removing it from a wire schema belongs to the same release.

## 11. Open Questions
1. RESOLVED (slice 3): `outcome_history` STAYS. It is a live event log, not reward-only data: `trw-memory/src/trw_memory/_graph_cross_project.py` (`entry_has_cross_validation`) reads its `cross_validated:project_id=` markers as a once-per-project idempotency guard, and graph decay/boost, clusters, consolidation and sync conflict-merge append to it. Only its Q-outcome entries stopped (slices 1-2).
2. Does any backend payload serialize these fields?

## 12. Traceability Matrix

| FR/NFR | Source | Test |
|---|---|---|
| FR01 | `trw-mcp/src/trw_mcp/scoring/_correlation.py`, `trw-memory/src/trw_memory/storage/_schema.py`, `trw-mcp/src/trw_mcp/state/_memory_update.py` | identifier-absence test (new) |
| FR02 | `trw-memory/src/trw_memory/lifecycle/scoring.py` | decay-formula test |
| FR03 | `trw-mcp/src/trw_mcp/state/claude_md/_promotion.py`, `trw-mcp/src/trw_mcp/state/consolidation/_cycle.py`, `trw-mcp/src/trw_mcp/state/analytics/entries.py` | updated ranking tests |
| FR04 | `trw-memory/src/trw_memory/cli_storage.py`, `trw-mcp/src/trw_mcp/export.py` | `trw-memory/tests/test_core293_legacy_reward_columns.py`, `trw-mcp/tests/test_export_import.py` |
| NFR01 | recall paths | `trw-mcp/benchmarks/engmem_gate.py` |
| NFR02 | both src trees | AST eLOC count, `make check` |
