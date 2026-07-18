---
name: trw-simplify
description: >
  Behavior-preserving cleanup of recently modified functionality and its tests.
  Trace usages across changed and surrounding files, then remove proven dead
  code, duplicate logic, stale test scaffolding, and unnecessary complexity.
---

# TRW Code Simplifier

Treat the modified **behavior slice**—source, consumers, configuration, and tests—as one unit. Preserve behavior, public
contracts, safeguards, telemetry, and meaningful coverage. Do not add features or unrelated cleanup.

## Workflow

### 1. Scope and trace

- Fix the target and baseline: named files, owned working-tree paths, staged diff, or commit range. In a shared or dirty workspace, distinguish HEAD, staged, worktree, and untracked changes; never reset, clean, stage, or overwrite unrelated work.
- Read the diff and smallest relevant neighborhood: callers, callees, imports, exports, configuration, registrations, and
  nearest tests. Mark generated or client projections, frozen vendors, and standalone deployment artifacts; required
  boundaries are not duplicate behavior by default.
- Trace candidates across packages and indirect paths: entry points, re-exports, registries, reflection, decorators,
  plugins, manifests, side effects, generated inputs, and release scripts. Prove value flow through resolution to invocation;
  names or file co-occurrence are not reachability.
- Use risk, duplication, dead-code, and hotspot intelligence (including `trw-distill` when available) to prioritize, not
  decide. Static findings are evidence, not proof. Refresh stale results, disclose partial failures, and test
  representative positive and negative controls for custom scanners.
- Inspect exact clone blocks. Imports, re-exports, schemas, signatures, docstrings, and client boundaries repeat policy only
  when they contain executable behavior or create a drift source.
- Run reachability from the package or project root so tests, manifests, and entry points remain visible.
- Test-only use is neither proof of life nor death. Verify registration, contracts, and assertions; corroborate scanner
  labels with a repo-root literal/symbol search, including non-code consumers.

### 2. Decide and simplify

- Classify each candidate as **remove**, **consolidate**, **retain**, or **uncertain**. Remove only with proof of
  no consumer, no unique behavior, and no contract. An approved PRD or execution plan is a contract; retain or defer its code unless retired.
- If live behavior supersedes a current contract, reconcile or version it before deletion; preserve historical evidence.
- Consolidate under the clearest existing owner. Reduce needless nesting, indirection, state, and pass-through wrappers.
  For test-covered code with no production composition, retain/defer a current contract or remove the code and exclusive
  tests—do not retain two sources of truth.
- Simplify production and tests together. Delete tests only with proven-dead non-contractual behavior or equivalent remaining
  proof. Update affected classifiers, inventories, CI selectors, and PRD mappings. Verify fixtures, hooks, registrations, and
  mocks activate. Never weaken regression, boundary, error-path, security, integration, or public-contract coverage.
- Prefer a small cohesive diff that reduces code, duplication, state, or cognitive load without relocating complexity.

### 3. Verify preservation

- Review the final diff for drift, lost safeguards, contract changes, and weaker test evidence.
- Recheck failure, concurrency, cleanup, resource, schema, and path-shape boundaries. Normalize, validate, and filter before
  ranking or limiting so rejected items cannot consume a first-N budget.
- Run the narrowest project-native tests and static checks. Name any unrun orchestrator-owned checks; never imply they passed.
- Stop when no further net-positive cleanup is proven. A justified no-change result is better than churn.

## Mandatory preservation invariants

1. **Contracts and data:** preserve exported names, signatures, types, configuration defaults, flags, schemas, aliases,
   serialization, wire formats, persisted formats, and compatibility. Consolidate declarations only with parity evidence.
2. **Safety mechanics:** preserve atomic writes, flushes, locks, transactions, idempotency, retries, cancellation, and
   cleanup/finally behavior. Preserve filesystem root containment and symlink policy. Treat cleanup, rollback, and deletion
   as destructive: close check/use gaps with pinned,
   no-follow paths when parents can change; skip advisory cleanup when safe traversal is unavailable.
3. **Indirect dependencies:** retain type, generation, reflection, registration, decorator, macro, plugin, and import-side-
   effect dependencies until every runtime path is traced.
4. **Observability:** preserve log and telemetry names, fields, redaction markers, trace/span context, and schema compatibility.
5. **Intent evidence:** `pass`, `...`, `NotImplementedError`, no-op hooks, TODO/FIXME, PRD/FR references, and intent markers
   are not deadness proof. Remove them only with the proven-dead behavior or resolved obligation they describe.

## Output

Report scope, evidence, test treatment, validation, and retained uncertainties.
