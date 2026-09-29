---
name: trw-delegate
description: >-
  Hand work to other agent CLIs (codex, agy, grok, claude, ...) with one trw_dispatch call: review, critique, plan or implement, on one harness or fanned out to several. Use when asked to "get codex/agy/grok to review / critique / plan", or when a second opinion from another model would help.
user-invocable: true
argument-hint: "[targets] [role] [what to review]"
---

# Delegate to Other Harnesses

"Get codex, agy and grok to critique this" is ONE tool call. Never hand-roll `codex exec`, `agy -p`, `grok -p`
or a wrapper script: `trw_dispatch` already launches each CLI with the right flags, sandbox, timeout and output parsing.

## Copy-paste

```
trw_dispatch(
    client="codex,agy,grok:grok-4.7",   # one name, "client:model", or a comma list to fan out
    role="critique",                     # optional preset: review | critique | plan | audit | implement
    prompt="Critique the retry logic in src/foo/retry.py. Be specific.",
    wait=True,                           # inline, <=120s; returns every answer together
)
# -> {"status": "3/3 succeeded", "results": [{"target": "codex", "ok": true, "text": "..."}, ...]}
```

- Names: `trw_dispatch(action="clients")` lists every client, whether it is installed, aliases (`sonnet`,
  `haiku`, `opus` mean claude with that model; `antigravity` means agy) and the roles.
- The prompt goes as-is by default. A role is only a preset: `review`, `critique`, `plan` and `audit` prepend a
  reviewer contract and default to read-only; `implement` lets the child write. A role never sets a posture or
  refuses a client.
- Variants: `prompt=["variant A", "variant B"]` runs every variant (on every listed client) in parallel.
- Posture: `posture="reviewer"` asks for TRW's read-only tool surface, best effort; `posture_note` in the result
  says what the child actually got when a client cannot carry it. The trailing `!` in `"reviewer!"` means required:
  the dispatch refuses rather than run without it.
- A failed lane carries `error` (stderr tail) and `reason`; the other lanes still return.

## Long work

`wait=True` only for short work (MCP dispatch is capped at 120s). For longer work:

1. Call `trw_dispatch(prompt=..., role=..., client=..., wait=False)` — a fan-out returns one job per target and
   a ready-made `poll` call.
2. Poll `trw_dispatch(action="status", target=job_id)` (comma-separate several ids) until terminal. Never imply
   a review completed from a non-terminal job.

No MCP but a shell exists: use `trw-mcp dispatch --help` and the CLI as a fallback; its flags are not repeated here.

## When the dispatch tools are not listed

They are off, not missing. `trw_dispatch` needs `dispatch_tools_exposed: true` in `.trw/config.yaml` — refresh the
tool list after the flag flips. There is no alternative grant path. A reviewer-bounded session is refused
regardless; that is not a transient error.

## Resolution

Omitted client, model, and timeout values resolve from `.trw/config.yaml`: an explicit client, else the configured
default; disabled clients fail closed. A model rides in the client (`"grok:grok-4.7"`). Supported targets
and options come from the live runtime (`action="clients"`), not this skill.

## Safety

- Keep `read_only=True` (default) and isolation on for second opinions. Write access or reduced isolation must be explicit,
  rare, and justified.
- Some targets may still load project or user configuration; disclose that instead of claiming uniform isolation.
- The child inspects the real project. Treat its output as review evidence, not authority; verify locally.
- Prompts are redacted from returned metadata; still no secrets in prompts.
- On failure or timeout, report the lane's `error`. Never silently retry with weaker isolation or write access.
