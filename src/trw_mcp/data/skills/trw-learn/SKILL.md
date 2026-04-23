---
name: trw-learn
description: >
  Record, update, or retire learnings. With a summary: records a new learning.
  With "resolve/obsolete L-id": changes status. Without arguments: reflects on
  session memory. Use: /trw-learn ["summary" | resolve L-id | obsolete L-id]
user-invocable: true
disable-model-invocation: true
argument-hint: "[\"summary\" | resolve L-id [reason] | obsolete L-id [reason]]"
---

# Learn Skill

Use when: you want to record, retire, or reflect on session learnings via a single slash command.

Manage TRW's learning memory. Three modes:

- **Record**: `/trw-learn "summary"` — record a new high-value learning
- **Retire**: `/trw-learn resolve L-id` or `/trw-learn obsolete L-id` — mark a learning as fixed or outdated
- **Reflect**: `/trw-learn` (no args) — review session for discoveries, refine or retire stale entries

## Quality Gate — Record Only If

Before recording, verify the learning meets at least one of these criteria:

- **Prevents a repeated mistake**: An error or bug that would waste time if encountered again
- **Documents a non-obvious gotcha**: Something that looks like it should work but doesn't
- **Changes how work is done**: A pattern or workflow materially better than the default
- **Captures critical architecture knowledge**: Design decisions or dependencies not obvious from code

Do NOT record: routine observations, obvious documented facts, temporary notes, per-task status updates.

Adding no new learnings is perfectly fine — an empty reflection means existing memory is already good.

## Workflow — Record (arguments start with a quoted summary)

1. **Parse arguments**: Extract the learning summary from `$ARGUMENTS`. Look for optional flags:
   - `--tags tag1,tag2` — categorization tags (default: inferred from summary)
   - `--impact 0.N` — impact score 0.0-1.0 (default: 0.8 for user-initiated)
   - `--detail "..."` — extended context (default: generated from summary)

2. **Check existing memory**: Call `trw_recall` with keywords from the summary.
   - If a similar learning exists and this new one refines it, call `trw_learn_update(learning_id, detail=..., summary=...)` to improve the existing entry instead of creating a duplicate.
   - If a near-duplicate exists with no new information, tell the user and skip.
   - If no match, proceed to record.

3. **Evaluate quality**: Check if the learning meets the quality gate. If borderline, ask the user.

4. **Enrich context** if not provided:
   - If no `--detail`, expand the summary with: what happened, why it matters, what to do differently
   - If no `--tags`, extract 2-3 relevant keywords
   - Set `source_type` to `"human"`, default impact to 0.8

5. **Record**: Call `trw_learn(summary, detail, tags, impact, source_type="human")`

6. **Confirm**: Report the learning ID, summary, impact score, and tags.

## Workflow — Retire (arguments start with "resolve" or "obsolete")

When `$ARGUMENTS` starts with `resolve` or `obsolete`:

1. **Parse**: Extract the learning ID (e.g., `L-abc12345`) and optional reason from the arguments.
   - `resolve` = the issue was fixed, the gotcha no longer applies
   - `obsolete` = the learning is outdated, superseded, or no longer relevant

2. **Verify**: Call `trw_recall` to find the learning and confirm it exists and is currently active.

3. **Update**: Call `trw_learn_update(learning_id, status="resolved")` or `trw_learn_update(learning_id, status="obsolete")`.
   - If a reason was provided, also update the detail to append the reason.

4. **Confirm**: Report the change — learning ID, old status, new status, and reason.

If the user doesn't provide a specific ID but describes the learning (e.g., `/trw-learn resolve "the stop hook race condition"`), use `trw_recall` to search for matching entries, confirm the match with the user, then update.

## Workflow — Reflect (no arguments)

When `$ARGUMENTS` is empty, reflect on the current session:

1. **Recall existing memory**: Call `trw_recall('*', min_impact=0.5)` to load current learnings.

2. **Review session context**: Think about what happened this session:
   - What errors or unexpected behaviors were encountered?
   - What patterns or approaches worked well?
   - What gotchas were discovered? Were any existing gotchas resolved?

3. **Compare against existing learnings**: For each potential discovery:
   - Does it already exist? Could the existing entry be refined with `trw_learn_update`?
   - Is any existing learning now stale, resolved, or obsolete?
   - Is it genuinely high-value (meets the quality gate)?

4. **Take action** — any combination of:
   - **Record new** if genuine discoveries were found
   - **Refine existing** with `trw_learn_update(id, detail=..., summary=...)` if an entry could be improved
   - **Retire stale** with `trw_learn_update(id, status="resolved")` if an issue has been fixed
   - **Report no action** if memory is current — "Memory is up to date."

5. **Summarize**: Report what was done — new recorded, existing refined, stale retired, or no changes.

## Examples

```
/trw-learn "Pydantic v2 use_enum_values changes comparison semantics"
/trw-learn "TaskCompleted hook must be soft gate" --impact 0.9
/trw-learn resolve L-abc12345 "Fixed in commit df6ec89"
/trw-learn obsolete L-def67890 "Superseded by new update mechanism"
/trw-learn resolve "the stop hook race condition"
/learn
```

## Notes

- `trw_instructions_sync` refreshes the client instruction file (CLAUDE.md / AGENTS.md / etc.); learnings surface via `trw_session_start()` recall, not by promotion into the instruction file
- Resolved/obsolete learnings are excluded from recall results
- The learning memory is shared across all sessions — every entry costs attention
- Prefer retiring stale learnings over letting them accumulate noise
- Use `/trw-memory-audit` to review health and find candidates for retirement
