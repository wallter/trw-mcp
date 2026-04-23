# trw_mcp/security — Sub-CLAUDE.md

Scope-local guidance for the MCP-server authorization layer. Closes the
sub-CLAUDE.md row of PRD-INFRA-SEC-001 exit criteria.

## What Lives Here

| Module | Purpose | PRD ref | Status |
|---|---|---|---|
| `mcp_registry.py` | Signed allowlist loader, canonical vs operator overlay, `MCPServer` model, signature verify stub | INFRA-SEC-001 FR-1, FR-5, FR-8 | 🟡 Scaffolded (179 LOC) — verify fn is observe-mode stub, not wired into middleware |
| `capability_scope.py` | Capability-scope filter wrapping each MCP adapter call | INFRA-SEC-001 FR-2, FR-4 | 🟡 Scaffolded (168 LOC) — not wired |
| `anomaly_detector.py` | 3-week shadow-mode anomaly detector on `tool_call_events.jsonl` | INFRA-SEC-001 FR-3 | ⬜ Pending |

## Observe/Shadow Mode First — Do Not Short-Circuit

Every module in this package ships in **observe-mode for v1**:
- `verify_signature` always returns `True` and logs the decision it *would* have made
- `capability_scope.check(...)` logs deny decisions but does not raise
- `anomaly_detector` writes to `.jsonl` without tripping any action

This is intentional (PRD §8 Rollout Phase 1). The 3-week shadow clock
calibrates thresholds. Do not "promote to enforce mode" in this package
without a Sprint 97+ decision gate and an operator kill-switch flag. Any
PR that flips default to enforce mode MUST update PRD-INFRA-SEC-001 and
cite the decision-gate minutes.

## Two-Tier Signing Authority (FR-8)

- **Canonical allowlist** — ships with trw-mcp, signed by TRW maintainers
  - Path: `trw-mcp/src/trw_mcp/data/mcp_servers.allowlist.yaml` (pending)
- **Operator overlay** — per-project, at `$PROJECT/.trw/mcp_servers.local.yaml`
  - MAY add servers; MUST NOT downgrade canonical `trust_level`
  - Downgrade attempts are logged + dropped by `load_allowlist`

## Claude Code Flagship Requirement

The claude-code profile advertises MCP tools under `mcp__trw__`
namespace (`models/config/_profiles.py:102`). The allowlist stores
un-prefixed short names — prefix **normalization** happens at
advertise-time inside the middleware, not in the allowlist. When wiring
`middleware/mcp_security.py`, route both stdio (claude-code) and HTTP/SSE
(opencode) transports through the same normalization step so the fuzz
suite at `tests/fuzz/mcp_authorization/` covers all three reachability
paths (FR-9).

## Editing Rules

- `MCPServer` is a Pydantic v2 model — frozen. Any new field requires
  an FR update plus a migration note.
- `TrustLevel` ordering is encoded in `_TRUST_RANK`. If you add a level
  (e.g. `partner`), update both the dict and every comparison site.
- Signature verification is stubbed. When the real Ed25519 path lands,
  **do not delete the stub path** — keep it as a `test_only=True`
  shortcut to avoid gating unit tests on real key material.
- All decisions emit through `MCPSecurityEvent` (from
  `trw_mcp.telemetry.event_base`), carrying a resolved
  `surface_snapshot_id` once Phase 2 retrofit lands.

## Kill Switches

- `config.security.mcp_authorization_enabled` (default **false**)
- `config.security.mcp_shadow_mode_only` (default **true**)
  — when true, deny decisions are logged but never raise.

Sprint 97+ flips defaults after the calibration window closes.

## Testing

```bash
pytest tests/test_mcp_registry.py -v
pytest tests/test_capability_scope.py -v
pytest tests/fuzz/mcp_authorization/ -v       # FR-9 reachability (pending)
mypy --strict src/trw_mcp/security/
```

## References

- PRD: `docs/requirements-aare-f/prds/agentic-hpo/PRD-INFRA-SEC-001-mcp-server-authorization.md`
- Sprint: `docs/requirements-aare-f/sprints/active/sprint-96-agentic-hpo-foundation.md`
- Claude-code profile: `trw-mcp/src/trw_mcp/models/config/_profiles.py:102`
