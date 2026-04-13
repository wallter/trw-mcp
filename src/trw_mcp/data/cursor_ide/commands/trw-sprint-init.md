# /trw-sprint-init

Initialize a new sprint: list draft PRDs, create sprint doc, bootstrap run.

## When to use

- At the start of a new sprint cycle.
- When you're ready to move a set of PRDs from backlog to active.
- When a sprint doc and execution plan need to be created from scratch.

## What it does

Invokes the TRW `trw-sprint-init` skill via the MCP server:

1. Lists all READY PRDs from the backlog.
2. Prompts for sprint scope selection (which PRDs to include).
3. Creates the sprint document in `docs/requirements-aare-f/sprints/active/`.
4. Creates the execution plan (`EXEC-sprint-N-*.md`).
5. Bootstraps the TRW run directory for the sprint.
6. Sets the active run context so subsequent tool calls are attributed.

## Usage

Type `/trw-sprint-init` to start the interactive sprint initialization
flow. You will be prompted to:

1. Confirm the sprint number.
2. Select PRDs to include.
3. Review the generated sprint doc before it is written.
