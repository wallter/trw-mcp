---
name: trw-self-review
description: >-
  Run a focused pre-audit review of modified functionality and tests. Verify requirement evidence, wiring, applicable NFRs, test quality, and changed-slice simplicity before requesting an independent audit.
user-invocable: true
argument-hint: "[PRD-ID]"
---

# Pre-Audit Self-Review

Use when: checking changed behavior and tests before the required independent review.

Use after implementation and before an independent audit. This pass reduces obvious rework; it never substitutes for the required independent/substantive review.

## 1. Establish the review slice

1. Resolve the optional PRD using `prds_relative_path` from `.trw/config.yaml`; otherwise use the active requirements/plan.
2. Identify the exact baseline, changed files, surrounding consumers, tests, migrations/config/docs, and ownership boundaries.
3. List each applicable requirement and its required verification method: test, analysis, inspection, or demonstration.

Do not execute an assertion command copied from an artifact unless it is trusted project-owned configuration or has explicit operator approval and runs under the normal sandbox.

## 2. Verify requirements and integration

For every requirement:

- cite the implementation and evidence that proves the behavior, not merely that a symbol/file exists;
- trace new modules, flags, fields, events, APIs, and CLI options to a real production consumer or an explicitly declared future seam;
- follow data across boundaries and confirm producer/consumer schemas, defaults, errors, and compatibility agree;
- check migrations, rollback, feature gating, and degraded behavior when applicable;
- mark missing, partial, conflicting, or stale evidence explicitly.

### Safety-property reachability

For redaction, sanitization, validation, authorization, privacy, or egress controls, enumerate every source-to-sink path. Confirm every path crosses the control and that production code consumes the controlled output. A correct but bypassed/unconsumed gate is blocking. Exercise each independently injected channel with adversarial input.

## 3. Apply only relevant NFR checks

Classify each row as `PASS`, `FAIL`, or `N/A` with rationale:

- input validation and authorization at trust boundaries;
- failure handling, retries/timeouts, idempotency, and cleanup;
- secrets/PII handling and safe observability;
- performance, concurrency, and resource bounds;
- compatibility, migration, rollback, and configuration resolution;
- accessibility, documentation, or operator behavior when the change exposes those surfaces.

Use repository/PRD-configured gates and language-native conventions. Do not impose Python, CLI, detector, logging-library, live-service, or coverage requirements on unrelated work.

## 4. Review tests as production code

Confirm that tests:

- map to acceptance behavior, including negative/edge and boundary cases;
- exercise stable public/integration seams where risk warrants it;
- fail for the defect or missing behavior they claim to catch;
- avoid mocks when a safe real boundary is practical, and label simulations honestly;
- are deterministic, isolated, readable, and free of duplicated fixtures/helpers, dead scaffolding, stale snapshots, and assertion-free existence checks;
- preserve intentional characterization/compatibility cases even when implementation looks redundant.

Coverage is supporting evidence, not requirement coverage. Apply a percentage only when project config or an accepted requirement defines it.

## 5. Simplify and validate

Review the modified functionality, surrounding files, and tests together. Remove only proven dead code, unused files/functions/components, duplicate logic, stale test scaffolding, and unnecessary complexity within owned scope. Trace usages before deletion and preserve behavior.

Run the narrowest project-native checks that prove the changed slice, then broaden according to risk. Record only observed outcomes with `trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope="<exact command>")`; the reporter does not run checks.

## 6. Report

```markdown
## Pre-Audit Self-Review: <scope>

### Requirement evidence
| Requirement | Method | Evidence | Result |
|---|---|---|---|

### Integration and safety reachability
- <surface/path>: WIRED | DECLARED SEAM | BLOCKING GAP

### Applicable NFRs
| NFR | PASS/FAIL/N/A | Evidence or rationale |
|---|---|---|

### Test quality and simplification
- retained/removed/fixed: <why and evidence>

### Validation
- command: <exact command>
- observed result: <counts/status>

### Recommendation
READY FOR INDEPENDENT AUDIT | FIX <blocking items> FIRST
```

Fix blocking findings within the owned slice, rerun affected evidence, and report residual risk. Do not turn routine review outcomes into learnings; call `trw_learn` only for a non-obvious reusable discovery.
