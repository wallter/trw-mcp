---
name: trw-decision
description: >-
  Ask trw_decision for a calibrated probability instead of deciding by prose
  alone on a reasoning-level judgment call: delegate or solo, whether a finding
  is worth recording, update an existing learning vs record a new one, a
  complexity self-check, whether a PRD needs preflight, an N/A verdict check,
  revert vs retry, or finding severity. Advisory only; opt-in.
user-invocable: false
---

# trw-decision

`trw_decision(questions, state)` sends a small, redacted state plus typed questions
to a calibrated decision backend and returns probabilities. It is **advisory**: it
never gates, never satisfies a RIGID obligation, and never replaces a
deterministic check. Design and evidence: the trw-jev decision-backend design (PRD-CORE-288).

## When to use it

At a judgment call you would otherwise settle in free text, where a calibrated
number helps: a threshold, a second look, or a reason to escalate.
Do **not** use it for anything a rule, schema, test, or gate already decides, or
for security, deliver, review or phase-gate decisions.

## Question types

- `noul`: yes/no. Returns `noul` = P(true). `criteria: {"true": "...", "false": "..."}`.
- `choice`: pick one option. `criteria` maps each option name to its rubric.
  Returns `choice`, per-option `probabilities` and `confidence`.
- `score`: an ordered rubric. `criteria` is a list from lowest to highest.
  Returns a probability-weighted `score`, `probabilities` and `confidence`.

Ask several questions about the same state in one call; they are answered together.

## Recommended decision ids

These ids are a naming convention; the tool does not enforce them. Using the same
ids keeps the decision log comparable across sessions.

| id | Type | Question |
|---|---|---|
| `delegation.should_delegate` | noul | Independent, parallelizable chunks with disjoint ownership, worth delegating? |
| `learning.worth_recording` | noul | Non-obvious and reusable enough for `trw_learn`? |
| `memory.update_vs_new` | choice {update, new, skip} | Does this finding amend a recalled learning or stand alone? |
| `ceremony.complexity_crosscheck` | noul per risk signal | Does the task text imply a risk signal I did not report? Use it only to escalate |
| `prd.preflight_needed` | noul | Is the request ambiguous or novel enough to need preflight research? |
| `audit.na_verdict_check` | noul | Is this "N/A" verdict actually justified by the evidence? |
| `phase.revert_or_retry` | choice {fix_in_place, revert_implement, revert_plan, escalate} | What should happen after a repeated failure? |
| `review.finding_severity` | choice {blocker, major, minor, nit} | A second opinion on a finding's severity |

Example:

```python
trw_decision(
    questions={"learning.worth_recording": {
        "type": "noul",
        "instructions": "Is this observation worth recording as a durable project learning?",
        "criteria": {"true": "Non-obvious, reusable, saves a future agent time",
                     "false": "Routine, obvious, or only relevant to this session"}}},
    state={"observation": "The OpenRouter alias ~typesafe/jev-latest needs the leading tilde."},
)
```

## Reading the answer

- `status: "disabled"`: the tool is not enabled here. Decide as you normally would.
- `status: "abstained"`: no backend answered (not configured, timeout, or error).
  **Keep your own judgment.** Do not retry in a loop, and do not block on it.
- `status: "answered"`: use `answers[id]` as one input. Treat a `noul` near 0.5, or a
  low-`confidence` choice, as "no signal". State the probability when it changes
  what you do. Never let it override a deterministic rule or gate.

`backend` reports what ran (`jev` or `null`), including a Jev call that failed.
Each call is logged to the active run as a `decision` event, containing question
ids and answers but never the state. That log is the evidence used to decide
whether a decision id deserves more trust.

## What state to send

Send the minimum: booleans, counts, short enums, and short excerpts. State is
PII/secret-stripped before it leaves the machine, but that is a backstop, not
permission to over-share. Never include credentials, customer data, or whole
files. Keep state well under the backend's context limit (a few KB is plenty).

## Enabling (operator)

1. `.trw/config.yaml`: `decision_enabled: true`. This exposes the tool; it is off by default.
   Without it, a session can still request a single call with
   `trw_request_tool_access(tool_name="trw_decision", reason=...)`.
2. For the Jev backend: `TRW_JEV_ENABLED=true` in the **process env** (a project
   `.env` cannot enable it), plus `OPENROUTER_API_KEY` — the one setting a `.env`
   may supply. `TRW_JEV_BASE_URL` and `TRW_JEV_MODEL` (default
   `~typesafe/jev-latest`) are process-env only too, and the base URL must be
   `https` on an allowlisted host (`openrouter.ai`) or the call abstains.
   Without these, calls abstain and nothing leaves the machine.
