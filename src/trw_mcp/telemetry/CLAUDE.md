# trw_mcp/telemetry — Sub-CLAUDE.md

Scope-local guidance for agents editing the telemetry package. Closes the
sub-CLAUDE.md row of PRD-HPO-MEAS-001 exit criteria and the partial
resolution in `ROADMAP-AUDIT-2026-04-16.md` §D10-P1-11.

## What Lives Here

| Module | Purpose | PRD |
|---|---|---|
| `event_base.py` | `HPOTelemetryEvent` + 12 subtypes (Pydantic v2, frozen, strict, extra=forbid) | HPO-MEAS-001 FR-3 |
| `models.py` | Legacy `TelemetryEvent` (4-field installation-scoped, CORE-031) | CORE-031 |
| `pipeline.py` | Event pipeline — buffer → anonymize → publish | CORE-031 |
| `publisher.py` | Writes `.jsonl` event streams under the run directory | CORE-031 |
| `sender.py` | Remote telemetry delivery (opt-in) | CORE-031 |
| `anonymizer.py` | PII scrubbing before publish | CORE-031 |
| `embeddings.py` | Text embedding for event payloads | CORE-031 |
| `remote_recall.py` | Ingests remote events for cross-installation recall | CORE-031 |
| `constants.py` | Inlined constants (was `trw-shared`) | CORE-031 |
| `client.py` | Thin public client wrapper | CORE-031 |

## Two Parallel Schemas During Phase 1

`event_base.HPOTelemetryEvent` and `models.TelemetryEvent` coexist
intentionally. Do **not** rename, re-export, or "unify" them without reading
`PRD-HPO-MEAS-001` §9 Rollout Phase 1. The HPO-prefixed subclass names
(`HPOSessionStartEvent`, `HPOSessionEndEvent`, `HPOCeremonyComplianceEvent`)
exist specifically to avoid import-shadowing the legacy CORE-031 classes that
`telemetry/__init__.py` re-exports under their bare names.

## Editing Rules

- `HPOTelemetryEvent` is **frozen + extra=forbid**. New payload fields go
  inside `payload: dict[str, Any]`, never as new top-level attributes on
  subclasses. Subclasses set only `event_type` and `emitter`.
- `parent_event_id` (FR-6) is **advisory**. `validate_parent_within_run`
  returns the dangling ids as a list — never raise.
- `surface_snapshot_id` may be the empty string **only during Phase 1**
  (PRD §9). Once `artifact_registry` lands, `session_start` resolves a
  non-empty id and all new emissions carry it. Do not default to a
  synthesized placeholder — let empty-string be the visible Phase-1 signal.
- No `event=` kwarg in structlog calls; `event` is reserved. Use
  `action=` or a descriptive name.
- Any new emitter subclass **must** be listed in `__all__` and in the
  PRD-HPO-MEAS-001 FR-3 emitter table. Emitter drift breaks the CI gate
  at `tests/ci/test_emitter_coverage.py` (Phase 2).

## Phase 2 Retrofit (Pending)

The 7 legacy emitters below still emit via the old `TelemetryEvent`
shape. When you retrofit them, emit both schemas in parallel — do not
delete the legacy emission until the Phase 2 CI gate is green and the
migration tool (`migration/v1_to_unified.py`) has processed in-flight runs.

| Legacy emitter | Target HPO subclass |
|---|---|
| `trw_session_start` | `HPOSessionStartEvent` |
| `trw_deliver` | `HPOSessionEndEvent` |
| `trw_checkpoint` | via `ObserverEvent` payload |
| `trw_learn` | via `ObserverEvent` payload |
| `trw_build_check` | via `ObserverEvent` payload |
| ceremony phase-gate | `CeremonyEvent` |
| contract validation | `ContractEvent` |

## Claude Code Flagship Note

`ToolCallEvent` rows observed against claude-code will carry tool names
prefixed `mcp__trw__` (per `models/config/_profiles.py:102`
`tool_namespace_prefix="mcp__trw__"`). Pricing-table lookups must accept
both the prefixed and un-prefixed forms. Add prefix normalization at the
emission boundary in `tool_call_timing` middleware — not in `pricing.yaml`.

## Testing

```bash
pytest tests/test_event_base.py -v          # HPO base + subtypes
pytest tests/test_telemetry_pipeline.py -v  # Legacy pipeline
mypy --strict src/trw_mcp/telemetry/
```

## References

- PRD: `docs/requirements-aare-f/prds/agentic-hpo/PRD-HPO-MEAS-001-measurement-substrate.md`
- Sprint: `docs/requirements-aare-f/sprints/active/sprint-96-agentic-hpo-foundation.md`
- Landing commit: `b7b70c31d` (event_base.py W1-A)
