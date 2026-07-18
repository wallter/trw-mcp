## MCP Tool Retry Protocol

If a `trw_*` MCP call fails or is unavailable (transport error, tool missing,
timeout), use this TRW-specific policy rather than the framework ceiling for
non-TRW transient operations. Do not silently fall back to manual behavior.
Instead:

1. **Retry once** — reissue the same `trw_*` call at the top of your next tool
   batch. Transient MCP server hiccups usually clear within one retry.
2. **If it still fails, record the gap explicitly** — add a line to your output
   or checkpoint naming which ceremony step was skipped and why
   (e.g. "SKIPPED trw_checkpoint: MCP unavailable after 1 retry — progress
   recorded here instead"). A visible, recorded gap keeps degradation loud and
   auditable.
3. **Then continue** — a recorded gap is recoverable; a silent one is not.

Never let a failed `trw_*` call disappear without a trace. Agents that carry a
stricter persistence-blocker protocol (for example `trw-lead`: three retries
then escalate, and treat persistence failures as P0) follow that stricter rule
for persistence-critical steps; role-local stricter rules win. This fragment
covers the general case.
