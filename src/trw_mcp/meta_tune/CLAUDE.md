# trw_mcp/meta_tune — Sub-CLAUDE.md

Scope-local guidance for the meta-tune safety subsystem. Closes the
sub-CLAUDE.md row of PRD-HPO-SAFE-001 exit criteria.

## What Lives Here

| Module | Purpose | PRD ref | Status |
|---|---|---|---|
| `sandbox.py` | `ProbeIsolationContext` + `SandboxRunner` + `run_sandboxed()` — shared subprocess + seccomp-bpf + unshare-netns + RLIMIT primitive | SAFE-001 + CORE-144 | ✅ Landed (303 code LOC, 418 total) |
| `surface_registry.py` | Classifies which surfaces a meta-tune candidate touches (model/prompt/config/policy/weights) | SAFE-001 FR-1 | ⬜ Pending |
| `promotion_gate.py` | Outcome-correlation + Goodhart + human sign-off before a candidate can promote | SAFE-001 FR-2 | ⬜ Pending |
| `audit.py` | Hash-chained append-only audit log of every proposal | SAFE-001 FR-3 | ⬜ Pending |
| `rollback.py` | Idempotent rollback CLI (p95 ≤10s wall-clock) | SAFE-001 FR-4 | ⬜ Pending |
| `eval_gaming_detector.py` | Catches ≥5/5 synthetic DGM-attack fixtures | SAFE-001 FR-6 | ⬜ Pending |
| `boot_checks.py` | Boot-time default-resolution audit | SAFE-001 FR-15 | ⬜ Pending |

## v1 Defense-in-Depth Layers (sandbox.py)

The sandbox stacks cheap layers; each is independent, so any one layer
being unavailable degrades gracefully rather than failing closed.

1. **Subprocess** (always) — separate PID
2. **seccomp-bpf** (Linux + pyseccomp) — denies `socket`, `ptrace`, `mount`, ...
3. **Network unshare** (Linux + `allow_network=False`) — `unshare -n` / `CLONE_NEWNET`
4. **RLIMIT_AS** (POSIX) — memory cap
5. **Wall-clock timeout** (always) — `signal.alarm` + wait-timeout
6. **Filesystem audit** (always) — post-hoc mtime check on readonly paths + `writes_outside_tmp`

Non-Linux hosts run in **degraded mode**: layers 1 + 4 + 5 + 6 only.
Layer 2/3 emit a WARN log at construction time. Never silently drop
a layer — always surface it.

## Editing Rules

- `SandboxResult` is frozen dataclass — any new field requires a SAFE-001
  FR update plus a migration note. Callers pin on this shape.
- Do NOT import `pyseccomp` at module top-level — the import guard keeps
  the module importable on macOS / Windows CI. Use the module-level
  `_HAS_SECCOMP` flag.
- `run_sandboxed()` must remain synchronous — callers route async work
  through the orchestration layer, not this primitive. Async wrapping
  happens in `trw_meta_tune_rollback` (Wave 4 pending).
- All new modules in this package **must** emit their SAFE-001 audit
  events through `MetaTuneEvent` (from `trw_mcp.telemetry.event_base`),
  never writing to an ad-hoc jsonl.

## Kill Switch

`config.meta_tune.enabled` (default **false** until Sprint 99). Every new
module in this package MUST check this flag at its public entry point and
no-op with a WARN log when disabled. See SAFE-001 FR-7 + FR-13.

## Synthetic DGM-Attack Fixtures

Fixtures live at `tests/fixtures/meta_tune/dgm_attacks/` (pending).
`eval_gaming_detector.py` must detect ≥5/5. When adding a fixture, also
add a test that asserts its detection so coverage never regresses.

## Testing

```bash
pytest tests/test_sandbox.py -v             # ProbeIsolationContext
pytest tests/fixtures/meta_tune/ -v         # DGM attack corpus (pending)
mypy --strict src/trw_mcp/meta_tune/
```

## References

- PRD: `docs/requirements-aare-f/prds/agentic-hpo/PRD-HPO-SAFE-001-meta-tune-safety-gates.md`
- Design: `docs/research/agentic-hpo/sandbox-isolation-design-2026-04-17.md`
- Sprint: `docs/requirements-aare-f/sprints/active/sprint-96-agentic-hpo-foundation.md`
