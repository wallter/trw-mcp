---
name: trw-learn
description: "Record, update, or retire durable learnings. Use when the user asks for learning management or a workflow explicitly surfaces a non-obvious reusable discovery; never use for routine status. Invoke: /trw-learn [\"summary\" | resolve L-id | obsolete L-id]\n"
---

> Codex-specific skill: this version is authored for Codex. Follow Codex-native skill and subagent flows, and ignore Claude-only references if any remain.

# Learn Skill

Manage TRW's learning memory. Three modes:

- **Record**: `/trw-learn "summary"` — record a new high-value learning
- **Retire**: `/trw-learn resolve L-id` or `/trw-learn obsolete L-id` — mark a learning as fixed or outdated
- **Reflect**: `/trw-learn` (no args) — review session for discoveries, refine or retire stale entries

**Update invariant:** `trw_learn_update` replaces `summary` and `detail`; it never appends them. Before either field is
updated, re-read the current entry and pass the complete replacement with all still-valid detail and provenance. Never
pass a refinement or reason fragment expecting append semantics.

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
   - If a similar learning exists and this new one refines it, merge the refinement into the full current summary/detail, then call `trw_learn_update(learning_id, detail=..., summary=...)` instead of creating a duplicate.
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
   - If a reason was provided, append it locally to the fetched detail, then pass the full reconstructed detail as the replacement.

4. **Confirm**: Report the change — learning ID, old status, new status, and reason.

If the user doesn't provide a specific ID but describes the learning (e.g., `/trw-learn resolve "the stop hook race condition"`), use `trw_recall` to search for matching entries, confirm the match with the user, then update.

## Workflow — Reflect (no arguments)

1. Review actual session errors, surprises, successful patterns, and resolved gotchas before querying memory.
2. Form one candidate durable discovery at a time. Use targeted `trw_recall(query="<candidate keywords>")` to detect overlap; avoid blanket wildcard recall, which adds noise and updates access telemetry.
3. Choose one action:
   - record a genuinely new reusable discovery;
   - update an existing entry while preserving any still-valid detail (the `detail` field is replaced, not appended automatically);
   - mark a fixed issue `resolved` or superseded knowledge `obsolete`, with evidence;
   - take no action when the session produced no durable knowledge.
4. Report the learning IDs and evidence for changes. Routine session status belongs in checkpoints/reports, not memory.

## Examples

```
/trw-learn "Pydantic v2 use_enum_values changes comparison semantics"
/trw-learn "Completion gate must stay evidence-grounded and adapter-safe" --impact 0.9
/trw-learn resolve L-abc12345 "Fixed in commit df6ec89"
/trw-learn obsolete L-def67890 "Superseded by new update mechanism"
/trw-learn resolve "the stop hook race condition"
```

## Notes

- Active learnings surface through `trw_session_start` and `trw_recall`; they are not promoted into `AGENTS.md`
- Resolved/obsolete learnings are excluded from normal recall results
- The learning memory is shared across all sessions — every entry costs attention
- Prefer retiring stale learnings over letting them accumulate noise
- Use `/trw-memory-audit` to review health and find candidates for retirement
