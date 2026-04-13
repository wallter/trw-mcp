# /trw-deliver

Run the TRW deliver ceremony — reflect, checkpoint, sync learnings, close the run.

## When to use

- At the end of a work session before closing the IDE.
- After completing a task or milestone that you want to preserve.
- When the context window is getting full and you want to checkpoint.
- After a significant implementation to persist discoveries.

## What it does

Invokes the TRW `trw-deliver` skill via the MCP server:

1. Reflects on what was accomplished this session.
2. Calls `trw_checkpoint()` to save resumption state.
3. Persists new learnings via `trw_learn()` for future agents.
4. Closes the active run and updates ceremony metrics.

## Usage

Type `/trw-deliver` in the Cursor chat — no additional arguments needed.
The agent will prompt you if reflection input would improve the checkpoint.
