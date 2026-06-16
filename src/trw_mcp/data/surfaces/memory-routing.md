<!-- Canonical human-reference source for TRW memory routing.
     Renderer wire-up (auto-propagation into root CLAUDE.md's trw:start/trw:end
     block via trw_instructions_sync) is deferred to PRD-QUAL-076; edits here
     do NOT yet auto-sync into rendered client surfaces. Mirror changes into
     trw-mcp/src/trw_mcp/state/claude_md/_static_sections.py until QUAL-076 lands. -->

# TRW Memory Routing

**NEVER** store technical knowledge in native auto-memory. Use `trw_learn()` exclusively for engineering insights.

| | `trw_learn()` (Use for Engineering) | Native auto-memory (Use for Personal) |
|---|---|---|
| Search | `trw_recall(query)` — semantic + keyword | Filename scan only |
| Visibility | All agents, subagents, teammates | Primary session only |
| Lifecycle | Impact-scored, recalled at session start | Static until manually edited |

Gotcha or error pattern → `trw_learn()`. User’s preferred commit style → native memory. Build trick that saves time → `trw_learn()`. Communication preference → native memory.

## Project vs user tier

`trw_learn()` routes into one of two tiers. The **project** tier (default, under `.trw/`) holds repo-specific knowledge that travels with the codebase. The opt-in **user** tier (machine-local, at `~/.trw`) holds portable knowledge — operator preferences, cross-cutting patterns, workflow rules — shared by every repo on the box.

- `scope="auto"` (default) classifies portability: repo-local paths/symbols stay project; cross-cutting findings route to the user tier when one is present.
- `scope="project"` / `scope="user"` force the tier.
- `trw_recall()` federates both tiers into one ranked result; `include_tiers=["project"]` restricts it to project-only.

The user tier is off by default and non-destructive: a project that never opts in keeps single-store behavior, and enabling it never moves existing project learnings.

Use `trw_learn_update(memory_id, ...)` to correct or amend an existing entry — avoid storing a duplicate when the intent is to fix stale or inaccurate knowledge.
