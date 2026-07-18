---
name: trw-delegate
description: >-
  Dispatch an isolated, read-only second-opinion review to another coding-agent CLI. Use an explicit client or model when provider diversity matters; defaults may select the same client as the host.
---

# Cross-Client Second-Opinion Delegate

Use for a bias-breaking review or focused audit. Dispatch is read-only by default and sanitizes the child environment;
it does not guarantee a different provider or model unless the caller explicitly selects one.

## Run

Prefer MCP, especially in shell-less harnesses:

1. Call `trw_dispatch(prompt=..., role=..., client=..., wait=False)`.
2. Poll `trw_dispatch_status(job_id)` until `succeeded`, `failed`, `timed_out`, or `cancelled`.
3. Read the redacted result and report failures or isolation limitations. Never imply a review completed from a
   non-terminal job.

Use `wait=True` only for short work; synchronous MCP dispatch is capped at 120 seconds. When MCP dispatch is unavailable
and a shell exists, use `trw-mcp dispatch --help` and the CLI as a fallback rather than reproducing its mutable flags here.

## Resolution and roles

Omitted client, model, and timeout values resolve from `.trw/config.yaml`. Client precedence is explicit selection,
then the selected role's mapping, then the configured default; disabled clients fail closed. Supported targets and exact
options come from the live runtime/config, not this skill.

Optional roles are `code-review`, `design-audit`, `architectural-audit`, and `adversarial-audit`. A role adds the
runtime-owned read-only review contract; omit it for a bare prompt.

## Safety

- Keep `read_only=True` and isolation enabled for second opinions. Write access or reduced isolation must be explicit,
  rare, and justified by the caller.
- Isolation is strongest where the target supports config/MCP isolation. Some targets may still load project or user
  configuration; disclose that limitation instead of claiming uniform independence.
- The child runs against the real project directory so it can inspect the requested code. Treat its output as review
  evidence, not authority; verify actionable findings locally.
- Prompts are redacted from returned argv/log metadata. Do not place secrets in prompts anyway.
- On failure or timeout, report the terminal status and available redacted diagnostics. Do not silently retry with
  weaker isolation or write access.
