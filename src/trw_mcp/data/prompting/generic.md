# TRW Portable Prompting Guide

This guide is intentionally model-agnostic. TRW core prompts should work across
frontier hosted models, local/open-weight coding models, and future coding
harnesses without relying on provider-specific syntax.

## Core Assumptions

- Context windows, tool-call formats, and helper-agent support vary by harness.
- Stronger models still need evidence, project-native validation, nudges, and persistence.
- Provider-specific tricks belong in adapter notes, not the core workflow.
- Small, path-based prompts outperform large context dumps for most coding work.

## Recommended Patterns

### When to Delegate

- Use focused helpers only when the active client supports them reliably.
- Split by explicit file ownership and verification boundaries.
- If helper support is absent, execute the same shards sequentially.
- Keep the orchestrator responsible for final integration and validation.

### When to Self-Implement

- Trivial edits (≤3 lines, 1 file)
- Tightly coupled fixes where handoff would add risk
- Debug loops where the next command depends on the previous result

### Prompt Shape

Use concise sections:

```text
Context: repo/path and governing requirement
Task: exact change or question
Constraints: files to avoid, compatibility, security, style
Output: changed paths, validation run, risks
```

Prefer file paths and commands over pasted file bodies. Ask for schema-shaped
outputs when results must be merged or resumed.

## Session Protocol

1. **Start**: call `trw_session_start()` to load prior learnings and active state.
2. **Scope**: identify requirements, files, language/toolchain, and validation commands from repo config.
3. **Implement**: keep diffs small; checkpoint at milestones.
4. **Verify**: run targeted project-native checks; broaden when risk warrants.
5. **Learn**: call `trw_learn(summary, detail)` for reusable discoveries.
6. **Finish**: call `trw_deliver()` to persist work for future sessions.

## Portability Gotchas

- Do not assume a fixed token budget; inspect or stay conservative.
- Do not assume hooks block execution; treat them as advisory unless proven.
- Do not assume helper agents exist; TRW tools and manual fallbacks are canonical.
- Do not assume eval results transfer across model class, client, or benchmark family.
- Preserve uncertainty when evidence is mixed.


## Nudge Policy

Nudges are short, evidence-grounded reminders. Treat them as guidance for the next concrete action, not as proof that work is complete. Respect profile density, budget, and cooldown; if nudges become noisy, tune them rather than adding more prompt text.
