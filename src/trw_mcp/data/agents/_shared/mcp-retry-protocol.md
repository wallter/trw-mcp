## MCP Tool Retry Protocol

When a `trw_*` MCP call fails or is unavailable (transport error, missing tool,
timeout), do not silently fall back to manual behavior:

1. **Retry once** — reissue the same call at the top of your next tool batch.
2. **If it still fails, record the gap** — one line in your output or checkpoint
   naming the step you skipped and why ("SKIPPED <the tool you called>: MCP
   unavailable after 1 retry — progress recorded here instead").
3. **Then continue.** A recorded gap is recoverable; a silent one is not.

Where a role states a stricter persistence policy (`trw-lead`: three retries,
then escalate as P0), that stricter rule wins for its persistence-critical
steps. This fragment covers the general case.
