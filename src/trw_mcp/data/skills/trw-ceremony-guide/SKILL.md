---
name: trw-ceremony-guide
description: >-
  Load the TRW ceremony reference with the tool lifecycle table, execution phases, and example flows. Use this to resolve questions about tool selection and execution timing within the TRW framework. Invoke: /trw-ceremony-guide
user-invocable: true
argument-hint: ""
---

# TRW Ceremony Guide

Use when: looking up which lifecycle tool or governance rule applies next.

Use this as a compact timing reference. The current framework and live tool schema are authoritative; if an adapter drifts, preserve the framework's evidence obligations and report the mismatch.

## Phases and tiers

`RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER`

| Tier | Required phases | Notes |
|---|---|---|
| MINIMAL | IMPLEMENT, VALIDATE, DELIVER | `trw_session_start` still happens first. A run directory is optional when continuity is unnecessary. |
| STANDARD | PLAN, IMPLEMENT, VALIDATE, REVIEW, DELIVER | Default for non-trivial work; review must be substantive. |
| COMPREHENSIVE | All six | Use for architectural/high-risk work. Delegation is optional and harness-dependent. |

VALIDATE is never skipped. Match evidence to the requirement: tests for executable behavior, or analysis/inspection/demonstration for other work. Use project-configured quality gates; do not invent universal thresholds.

## Tool timing

| Tool | Timing | Contract |
|---|---|---|
| `trw_session_start(query?)` | First TRW action | Load relevant learnings and recover active run state. |
| `trw_init(...)` / `trw_status()` | Start tracked work / inspect resumed work | Create a run when continuity is useful, or inspect current run state. |
| `trw_recall(query)` | Before unfamiliar or high-risk work | Retrieve focused prior knowledge. |
| `trw_checkpoint(message)` | After meaningful milestones or before context risk | Persist resumable progress. |
| `trw_learn(...)` / `trw_learn_update(...)` | On a durable discovery or stale entry | Record or correct reusable knowledge, not routine status. |
| `trw_prd_create` / `trw_prd_validate` | When requirements need a durable contract | Create and validate AARE-F requirements before implementation. |
| `trw_build_check(...)` | After project-native checks | Record only observed results; include `command_results` when evidence enforcement requires it. It does not run tests, lint, types, builds, or coverage. |
| `trw_review(...)` | Before delivery at STANDARD+ | Record a substantive review; use an independent reviewer when available, otherwise label a cold-context self-review. |
| `trw_deliver(...)` | Last TRW action | Persist delivery state and sync supported instruction surfaces. It does not promote learnings into `AGENTS.md`. |

Concrete build evidence:
`trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope="<exact command>")`

## Example flows

**Quick change**

`session_start -> inspect/work -> project-native validation -> record applicable evidence -> deliver`

**Tracked STANDARD code change**

`session_start -> init/plan -> implement + checkpoint/learn when triggered -> run native checks -> build_check -> substantive review -> deliver`

Parallel helpers may replace independent steps when the harness and policy allow them. The same sequence remains valid in one session.

## Delivery boundary

Call `trw_deliver` only with one of the framework's three paths:

1. a recorded passing build check;
2. `allow_unverified=true` with an unexpired acceptable-failure record naming `failed_command`, `residual_risk`, `owner`, and `expiry_iso`; or
3. an authorized operator/config override with technical rationale.

A review label or free-text excuse is not an acceptable-failure record.

## Rigid vs triggered

**Rigid:** session start, appropriate validation, applicable build evidence after code/test changes, substantive review at STANDARD+, completion evidence, and delivery.

**Triggered:** initialize a run when continuity warrants it; checkpoint at milestones; learn on durable discoveries; recall for unfamiliar/high-risk work; delegate only when useful and supported.
