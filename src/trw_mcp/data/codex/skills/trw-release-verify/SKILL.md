---
name: trw-release-verify
description: >
  Pre-release verification GATE. Runs deterministic CI-parity gates, then a
  fan-out adversarial review (bugs, security-invariants, test quality, API/IP,
  performance) over the release diff, each finding independently verified, and
  ends in a GO / NO-GO verdict. READ-ONLY — never edits code. Use before any
  publish, or on any branch/diff/PR. The release process offers it as an opt-in
  gate; it is also independently invocable in Claude and Codex.
  Use: /trw-release-verify [trw-mcp|trw-memory|all] [--since <tag/ref>]
user-invocable: true
context: fork
argument-hint: "[trw-mcp|trw-memory|all] [--since <tag/ref>]"
---
<!-- ultrathink -->

# trw-release-verify — pre-release verification gate

**Use when:** before any package publish/release, or to review any branch, PR, or
diff on demand — runs deterministic CI-parity gates plus an adversarial fan-out
review (bugs, security-invariants, tests, API, IP) ending in a GO/NO-GO verdict.
Read-only; never edits code.

## Why this exists (read first)

`mypy --strict` + a green test suite are **necessary, not sufficient.** The
2026-07-16 "simplify" incident shipped **130 independently-verified regressions
(43 P0)** — cross-namespace auth bypass, a whitespace-padding privilege
escalation to admin, Ed25519 signing-key symlink/TOCTOU, a provenance hash-chain
that forks under concurrency, the SQLite WAL-reset corruption race — and **every
one of them passed mypy --strict AND the full test suite.** They passed because
the removed invariants are not expressed in types and are not exercised by tests:
no unit test plants a symlink mid-operation or contends two processes on a WAL
file. Green means "behavior preserved for the inputs we thought to check," which
is not the same as "safe."

This gate adds the missing layer: an **adversarial fan-out review that assumes
the diff removed something load-bearing and goes looking for it.** Full incident
+ evidence: `docs/research/simplify-campaign-audit-2026-07-16.md`.

## When to run

- **Before any publish** — the release process (the `trw-release` skill, `trw-release.mjs`)
  MUST offer this as an opt-in gate and proceed to publish only on GO or a
  recorded operator waiver.
- **On demand** — any branch, PR, or diff, independently, in Claude or Codex.

## Scope

Default scope is the diff of the target package(s) **since the last release tag**
(compute with `scripts/prerelease-review-scope.sh <pkg>` → prints the base ref
and the changed source/test files). Override the base with `--since <ref>`.
If verifying a reconciliation/revert (not a forward diff), scope is HEAD vs the
candidate tree.

## The protocol — three layers, fail-closed

### Layer 1 — Deterministic gates (cheap, run FIRST; if any fails, STOP)

Do not burn review tokens on a tree that fails the mechanical gates. Compose the
existing checks (do NOT reimplement them):

1. **Publishability**: `scripts/check-version-parity.py` (all packages in parity);
   target local version **>** what PyPI actually serves (`curl pypi.org/pypi/<pkg>/json`);
   CHANGELOG has a heading for the target version (`check-release-changelog-parity.py`);
   the release tag will target the **subtree-split** SHA, not a monorepo SHA.
2. **Dependency-floor skew** (the B1 lesson): for every cross-package symbol the
   target newly imports, its declared floor in `pyproject.toml` must be satisfiable
   by the version **PyPI actually serves** — not merely the editable dev install.
   A dev venv with the sibling installed editable will hide a too-low floor; test
   against `pip download <pkg>==<declared-floor>` or reason from the PyPI JSON.
3. **Types + tests, CI-parity**: `mypy --strict` on the target `src/`; the target's
   full test suite in an **isolated venv** (no dev-venv optional-dep masking, no
   monorepo-path masking). The `trw-release.mjs` `preflight` phase already does
   this in a clean subtree checkout — reuse it.
4. **Public surface**: `make public-changelog-ip-check`; `scripts/check-release-leak-boundary.py`;
   `bundle-sync`; `inventory-check`.

### Layer 2 — Fan-out adversarial review (the core value; READ-ONLY, parallel)

Slice the release diff and **fan out one reviewer per dimension** (scale slices
to diff size). Each reviewer diffs `<release-base>..HEAD` for its files and reports
findings; a **second, independent skeptic verifies each P0/P1** (CONFIRMED /
BOUNDED / REFUTED) so one reviewer's opinion never counts alone — this attrition
rate (aim for it to knock down 20-40% of raw findings) is your over-flagging
control.

Dimensions (one agent-slice each):

1. **correctness / bugs** — logic errors, edge cases, null/exception/rollback paths, off-by-one, error handling.
2. **security-invariants** — hunt REMOVED guards with the 9-pattern checklist below. **This is the dimension that catches simplify-style de-hardening; it is mandatory and gets the most reviewers.**
3. **test-quality** — are new/changed tests REAL (assert behavior with non-default inputs) or facades (`assert x is not None`, `assert callable(f)`)? Does the diff have coverage? Were guard-exercising tests deleted alongside the guard? (A deleted test + a deleted guard is invisible to every other gate.)
4. **API / contract / back-compat** — public signatures, Pydantic schemas, wire formats, removed params, changed defaults, renamed exports.
5. **performance / resource** — fd/thread/connection leaks, unbounded growth, dropped admission caps, N+1, removed batching.
6. **public-surface IP + deps** — proprietary-package references in public output; dependency-floor and lockfile-floor regressions.

#### The 9 security-invariant pattern classes (grep + review checklist)

From the audit — these are the classes that passed types+tests but were
exploitable. For EACH: does HEAD have the guard and does the diff remove/weaken
it? Is the guarded path reachable? Is there a real compensating control? If a
security claim, reproduce the exploit condition when it is cheap.

1. **fd-based TOCTOU-safe file ops → naive path ops.** grep removed: `O_NOFOLLOW`, `O_DIRECTORY`, `O_EXCL`, `dir_fd=`, `mkstemp`, `is_symlink`, `follow_symlinks=False`, `ftruncate`, `fstat`/`lstat` inode re-check, `fsync` before `os.replace`.
2. **cross-process locks dropped, in-process thread lock left behind.** grep removed: `flock`, `lock_for_rmw`, `*_file_lock`, `fcntl`. Fatal in the one-OS-process-per-MCP-client model — re-exposes the SQLite WAL-reset corruption race.
3. **isolation / scope parameters stripped from signatures.** diff every security-decision function's arity for removed `namespace=`, `session_id=`, `current_phase=`, tenant/org scoping. A dropped param silently defeats per-tenant/per-connection isolation.
4. **fail-closed → fail-open / lax type boundaries.** grep removed `isinstance(...)` guards in auth/validation; removed admission caps (`max_keys`, bind-chunk ceilings); removed `top_k<=0`/`limit<=0` short-circuits (SQLite treats negative LIMIT as unlimited); a check that raises only `if x is not None` (omit the value → guard skipped).
5. **verified-ownership / signing / provenance collapsed to caller-trusted input** (e.g. a two-phase prepare/finalize ownership protocol flattened to trusting caller-selected bytes; a hash-chain append that lost its lock and can fork).
6. **tamper / supply-chain fingerprint downgraded** (loaded-module bytecode digest → version-string equality).
7. **lifecycle / resource cleanup dropped → fd/thread/conn leaks** (ContextVar reset, thread+loop teardown, close-connection-on-open-failure, atexit unregister).
8. **legacy-tolerance / migration losslessness removed → data lockout** (a schema/row mapper that stops accepting older on-disk formats).
9. **crypto boundary loosened + SILENT dependency-floor downgrade** (e.g. `bcrypt>=4.0` which raises past 72 bytes → `passlib` which truncates; a missing 72-byte length gate). **Diff `pyproject.toml`/lockfiles, not just code** — floor downgrades hide in metadata.

### Layer 3 — Synthesis + GO / NO-GO

Aggregate the CONFIRMED findings. Severity rollup (P0/P1 counts, by dimension and
by file). Report the BOUNDED/REFUTED attrition as the precision signal.

- **NO-GO** if any CONFIRMED **P0**, or any CONFIRMED **P1** without an explicit,
  recorded operator waiver.
- **GO** only when Layer-1 gates pass AND Layer-2 has zero unwaived CONFIRMED P0/P1.

Write the report to `docs/research/release-verify-<pkg>-<YYYY-MM-DD>.md` (or the
active run dir): the verdict, severity rollup, every CONFIRMED finding with a fix
hint, and the attrition count. **NEVER auto-fix** — report + verdict only; fixes
go through the normal review path and re-trigger this gate.

## Cross-client execution (Claude + Codex)

The 3-layer protocol and the 9-pattern checklist are identical across clients;
only the fan-out primitive differs.

- **Claude Code**: run the reference workflow `.claude/workflows/trw-release-verify.mjs`
  via the Workflow tool (dynamic: per-dimension slices → verify skeptics →
  completeness critic → opus synthesis), or, for a small diff, parallel Task
  agents (reviewers `model: sonnet`, synthesis `model: opus`).
- **Codex**: fan out parallel Codex agents over the same dimension slices, a final
  agent synthesizes. Same three layers, same checklist, same GO/NO-GO rule.

Both clients, non-negotiable: **read-only** review agents; **explicit model**
(opus/sonnet/haiku — never fable); **stagger spawns** to avoid throttling; **every
P0/P1 independently verified** before it counts toward the verdict.

## Integration with the release process

The `trw-release` skill and `trw-release.mjs` MUST, before any publish, offer:

> "Run pre-release verification (`/trw-release-verify`) first? [recommended]"

Publish only on **GO**, or on an explicit operator waiver recorded with the
release. This gate is the thing that would have caught the 2026-07-16 incident;
skipping it is a decision, not a default.
