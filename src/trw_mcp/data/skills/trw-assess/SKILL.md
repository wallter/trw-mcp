---
name: trw-assess
description: >-
  Score a judgment call with trw_assess (calibrated probabilities) instead of
  settling it in prose. Use at a fork with two or more defensible options; to
  triage whether a failure is real or a flake / harness artifact, whether a
  review finding is a blocker or a follow-up, or whether evidence is
  sufficient to call work done; to screen, rank or triage many items at once;
  and at a go/no-go, accept/hold or decide-vs-escalate point, before handing a
  ruling up. Not for facts you can verify by reading or running something,
  trivial choices, or restating a decision already made.
user-invocable: false
---

# trw-assess

`trw_assess(questions, state, items=None)` returns calibrated probabilities for typed questions about a small,
factual state, in under a second for a fraction of a cent. It is advisory: it never gates, and never overrides a
test, a deterministic rule, or a security, permission or delivery decision.

## When to call it

When you are about to decide by eye and a wrong call costs rework: a fork with 2+ defensible options; real defect
vs flake or harness artifact; blocker vs follow-up; is the evidence enough for done or READY; go/no-go; decide vs
escalate (score it before you hand the ruling up); N items to screen, rank or triage. Recurring classes:
**Phase transitions**, **PRD authoring**, **Using a learning**, **Review triage**, **Execution**,
**Review scoping**, **Messages**. Skip it when reading a file or running a command answers the question, for
trivial or cheaply reversed choices, and for a decision already made.

## Shape the questions

- **One call per state.** Collect every question you have about it first; types mix. ~10x cheaper per question.
- **Many things, same questions:** `items={"id": item_state, ...}` (`state` becomes shared context). Answers land
  under `items[id][question_id]`. Give each item its own text; identical items regress to the mean.
- `noul`: yes/no, returns P(true). Criteria keys must be exactly `"true"` and `"false"`.
- `choice`: one of N, criteria `{label: rubric}`. Add `"other"` whenever the input might fit none.
- `score`: an ordered rubric, lowest first, varying one attribute; returns an expected index. `probabilities` is
  keyed by strings `"0"`, `"1"`, ...
- **Criteria describe what makes each answer true**, never bare labels (measured AUC 0.74 labels vs 0.96 prose).
- **State is facts** (counts, booleans, short excerpts), never your lean: framing moved contested answers by
  0.1-0.3. No secrets, customer data or whole files.

```python
trw_assess(
    state={"test": "test_sync_retry", "ci_failures": "3 of 20 runs", "fails_locally": False, "diff_touches_it": False},
    questions={
        "real": {"type": "noul", "instructions": "Is this failure caused by a defect in the code under test?",
                 "criteria": {"true": "a logic error a user would also hit",
                              "false": "timing, environment or harness artifact unrelated to the code"}},
        "next": {"type": "choice", "instructions": "What should happen next?",
                 "criteria": {"fix_now": "blocks this change; fix before merge",
                              "follow_up": "real but outside this change; record it and continue",
                              "other": "neither fits"}}},
)
```

## Read the answer

- Branch on the probability, not the label. A near tie (`margin` < 0.05, a `noul` near your threshold) or low
  `confidence` means: take the safer, smaller or reversible option, or gather the missing evidence.
- Across items, rank rather than threshold; absolute numbers do not transfer between tasks.
- When stakes are high, confirm with a test or a source read before acting. Ask once; re-ask only on new state.
- Put the numbers in handoffs, READYs and escalations: "fix now (0.82)", "blocker 0.31 / follow-up 0.69".
- `status` is `complete`, `partial` (read `outcomes[id].failure`), `failed` or `disabled`.

## When it is not available

- Not in your tool list: your client may defer MCP tools, so search for `trw_assess` by name, or call
  `trw_request_tool_access(tool_name="trw_assess", reason=...)`.
- `post_compaction_recovery_required`: call `trw_session_start`, then retry once.
- `invalid_request`: fix the shape (each question is `{type, instructions, criteria}`); do not resend it unchanged.
- `disabled`, a transient failure, or no access at all (restricted sub-agents and reviewer lanes often lack it):
  decide as you normally would and write "trw_assess unavailable" in your report, so the lead can score it. Never
  loop or block on it.
