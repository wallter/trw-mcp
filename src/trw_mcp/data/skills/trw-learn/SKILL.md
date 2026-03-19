---
name: trw-learn
description: >
  Record, update, or retire learnings. With a summary: records a new learning.
  With "resolve/obsolete L-id": changes status. Without arguments: reflects on
  session memory. Use: /trw-learn ["summary" | resolve L-id | obsolete L-id]
user-invocable: true
model: claude-sonnet-4-6
disable-model-invocation: true
argument-hint: "[\"summary\" | resolve L-id [reason] | obsolete L-id [reason]]"
allowed-tools: Read, Bash, mcp__trw__trw_recall, mcp__trw__trw_learn, mcp__trw__trw_learn_update
---

# Learn Skill

Three modes: **Record** (`/trw-learn "summary"`), **Retire** (`/trw-learn resolve L-id`), **Reflect** (`/trw-learn` no args).

## Quality Gate

Record only if it: prevents a repeated mistake, documents a non-obvious gotcha, changes how work is done, or captures critical architecture knowledge. Skip routine observations and obvious facts.

## Record (quoted summary)

1. Call `trw_recall` with keywords from the summary to check for duplicates.
   - Duplicate with no new info → skip. Refinement → `trw_learn_update` the existing entry.
2. Verify quality gate. If borderline, ask the user.
3. Expand summary into detail (what happened, why it matters, what to do differently). Infer 2-3 tags. Set `source_type="human"`, impact=0.8.
4. Call `trw_learn(summary, detail, tags, impact, source_type="human")`.
5. Report: learning ID, summary, impact, tags.

## Retire (resolve/obsolete L-id)

1. Parse learning ID and optional reason. `resolve` = fixed, `obsolete` = outdated.
2. Call `trw_recall` to verify the learning exists.
3. Call `trw_learn_update(learning_id, status="resolved"|"obsolete")`. Append reason to detail if provided.
4. Report: ID, old status, new status, reason.

If user describes the learning instead of providing an ID, search via `trw_recall`, confirm match, then update.

## Reflect (no arguments)

1. Call `trw_recall('*', min_impact=0.5, max_results=10, compact=true)`.
2. Review session: errors encountered, patterns that worked, gotchas discovered.
3. Compare against existing learnings. Take action:
   - **Record new** genuine discoveries
   - **Refine existing** via `trw_learn_update(id, detail=..., summary=...)`
   - **Retire stale** via `trw_learn_update(id, status="resolved")`
   - **No action** if memory is current
4. Summarize what was done.
