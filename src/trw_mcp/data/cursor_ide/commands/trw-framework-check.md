# /trw-framework-check

Check TRW framework compliance for the current or specified work.

## When to use

- When you're unsure if the work follows TRW ceremony protocols.
- Before delivering a session to catch missing ceremony steps.
- After onboarding to verify the project is correctly configured.
- When the CI framework-check gate is failing.

## What it does

Invokes the TRW `trw-framework-check` skill via the MCP server:

1. Checks that `trw_session_start()` was called at session start.
2. Verifies the active run is in a valid state.
3. Checks for uncommitted checkpoints.
4. Validates that learnings are being persisted (not accumulating silently).
5. Reviews ceremony compliance metrics for the session.
6. Reports gaps with specific remediation steps.

## Usage

Type `/trw-framework-check` with no arguments to check the current session.
Optionally specify a run ID:

```
/trw-framework-check
/trw-framework-check run-id:20260413T120000Z-abc12345
```
