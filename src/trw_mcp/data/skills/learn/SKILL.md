---
name: learn
description: >
  Record or refine critical learnings. With arguments: records a specific learning.
  Without arguments: reflects on the session for discoveries worth preserving.
  Use: /learn ["summary"] [--tags tag1,tag2] [--impact 0.8]
user-invocable: true
argument-hint: "[\"summary\"] [--tags tag1,tag2] [--impact 0.8]"
allowed-tools: Read, Bash
---

# Learn Skill

Record or refine critical learnings in TRW's memory. Two modes:

- **With arguments**: Record a specific learning you've identified
- **Without arguments**: Reflect on the current session for discoveries worth preserving

## Quality Gate — Record Only If

Before recording, verify the learning meets at least one of these criteria:

- **Prevents a repeated mistake**: You hit an error or bug that would waste time if encountered again
- **Documents a non-obvious gotcha**: Something that looks like it should work but doesn't (API quirks, framework footguns, config pitfalls)
- **Changes how work is done**: A pattern, approach, or workflow that's materially better than the default
- **Captures critical architecture knowledge**: Design decisions, interface contracts, or dependencies that aren't obvious from the code

Do NOT record:
- Routine observations ("this file has 200 lines")
- Obvious facts available in documentation
- Temporary notes that won't matter next session
- Per-task status updates (use `trw_checkpoint` instead)

Adding no new learnings is perfectly fine — an empty reflection means existing memory is already good.

## Workflow — With Arguments

1. **Parse arguments**: Extract the learning summary from `$ARGUMENTS`. Look for optional flags:
   - `--tags tag1,tag2` — categorization tags (default: inferred from summary)
   - `--impact 0.N` — impact score 0.0-1.0 (default: 0.8 for user-initiated)
   - `--detail "..."` — extended context (default: generated from summary)

2. **Check existing memory**: Call `trw_recall` with keywords from the summary.
   - If a similar learning already exists and this new one adds nuance or corrects it, **update** the existing entry by recording a refined version with the same tags and a note referencing the original.
   - If a near-duplicate exists with no new information, tell the user and skip recording.
   - If no match, proceed to record.

3. **Evaluate quality**: Check if the learning meets the quality gate above. If borderline, ask the user.

4. **Enrich context** if not provided:
   - If no `--detail`, expand the summary with: what happened, why it matters, what to do differently
   - If no `--tags`, extract 2-3 relevant keywords from the summary
   - Set `source_type` to `"human"` since this was user-initiated
   - Default impact to 0.8 (user-initiated learnings are presumed important)

5. **Record**: Call `trw_learn(summary, detail, tags, impact, source_type="human")`

6. **Confirm**: Report the learning ID, summary, impact score, and tags back to the user.

## Workflow — Without Arguments (Reflection Mode)

When `$ARGUMENTS` is empty, reflect on the current session for learnings:

1. **Recall existing memory**: Call `trw_recall('*', min_impact=0.5)` to load current learnings.

2. **Review session context**: Think about what happened this session:
   - What errors or unexpected behaviors were encountered?
   - What patterns or approaches worked well?
   - What gotchas or non-obvious behaviors were discovered?
   - Were there architecture decisions or interface contracts worth capturing?

3. **Compare against existing learnings**: For each potential discovery:
   - Does it already exist in memory? If so, could the existing entry be refined or made more precise?
   - Is it genuinely high-value (meets the quality gate)?
   - Would a future agent benefit from knowing this?

4. **Take action** — one of:
   - **Record new learnings** if genuine discoveries were found (follow steps 3-6 from "With Arguments" above)
   - **Refine existing learnings** if an existing entry could be improved — record an updated version with better detail, corrected information, or additional context
   - **Report no action needed** if nothing new was discovered — this is a valid outcome, tell the user "Memory is up to date — no new learnings from this session."

5. **Summarize**: Report what was done — new learnings recorded, existing ones refined, or confirmation that memory is current.

## Examples

```
/learn "Pydantic v2 use_enum_values changes comparison semantics — enum members become raw values after model_validate"
/learn "TaskCompleted hook must be soft gate — blocking subagents causes 69% block rate" --impact 0.9
/learn "Always read existing implementation before specifying new behavior in PRDs" --tags prd-workflow,gotcha
/learn
```

## Notes

- Learnings with impact >= 0.7 are promoted to CLAUDE.md during `trw_claude_md_sync`
- The learning memory is shared across all sessions — every entry costs attention for every future agent
- Prefer fewer, higher-quality learnings over many low-value ones
- Refining existing learnings is often more valuable than adding new ones
- Use `/memory-audit` to review health and prune stale entries
