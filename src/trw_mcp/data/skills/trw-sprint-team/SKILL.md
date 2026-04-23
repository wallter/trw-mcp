---
name: trw-sprint-team
description: >
  Automate Agent Teams setup from a sprint plan. Reads sprint doc, analyzes PRDs,
  proposes team structure with file ownership, and spawns teammates with generated
  playbooks. Use: /trw-sprint-team [sprint-doc-path]
user-invocable: true
disable-model-invocation: true
argument-hint: "[sprint-doc-path]"
---

# Sprint Team Automation Skill

Use when: a sprint plan exists and you want Agent Teams bootstrapped end-to-end (playbooks + team creation + teammate spawn).

Automate Agent Teams setup from a sprint plan. This skill reads the sprint document, analyzes PRD scope and complexity, proposes a team structure, gets user approval, generates playbooks via `/trw-team-playbook`, creates the team, spawns teammates, and assigns tasks.

Shared templates (team composition, worktree recipe, integration-review prompt) live in `trw-mcp/src/trw_mcp/data/playbook-template.yaml`. Section names are cross-referenced below.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD directory. Sprint docs are in the `sprints/active/` sibling directory.

## Workflow

### Step 1: Parse Arguments

Check `$ARGUMENTS` for a sprint doc path:
- Valid file path → read that sprint doc directly.
- Empty or "active" → look in `sprints/active/` (sibling of `prds_relative_path`).
- Multiple active sprint docs → list them and ask the user to pick one.
- No sprint doc found → abort: "No active sprint found. Run /trw-sprint-init first."

### Step 2: Read Sprint Document

Extract sprint name, goals (1-3 bullets), PRD assignments per track, existing owner assignments, and implementation order constraints. Derive the team name from the sprint doc filename (e.g., `sprint-25-agent-teams-quality.md` → `sprint-25-agent-teams-quality`).

### Step 3: Analyze PRDs

For each PRD listed in the sprint:
- Read the full PRD file (locate via `prds_relative_path` + PRD ID).
- Extract FR count, Technical Approach (affected modules/files), Dependencies.
- Estimate complexity: Low (1-3 FRs), Medium (4-7), High (8+).
- If an execution plan exists at `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`, use its wave plan and file ownership mapping instead of estimating from the PRD alone.
- List key files likely to be created or modified.
- Note prerequisites.

If a PRD file cannot be found, log a warning and continue.

### Step 4: Propose Team Structure

Use the defaults from `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: team-composition) for implementer/tester/reviewer counts. Model selection: `sonnet` for all roles (cost-effective for focused coding/testing/review).

For each teammate, assign PRDs (distribute evenly across implementers, group by track where possible) and key files (from Technical Approach).

Present the proposal as a formatted table with columns: Teammate | Role | Model | PRDs | Key Files. Also print a PRD Dependencies list and a File Ownership Summary.

### Step 5: User Approval Gate

Ask explicitly:

> "Does this team structure look good? Reply 'yes' to proceed, or describe changes (e.g., 'combine tracks A and B', 'use haiku for the tester')."

- "yes" / "looks good" / "proceed" → Step 6.
- Changes requested → revise + re-present (up to 3 iterations).
- After 3 rejections → abort: "Team setup aborted after 3 revision cycles."

Do NOT proceed to TeamCreate or teammate spawning without explicit approval.

### Step 6: Generate Playbooks

Invoke `/trw-team-playbook` with the approved structure:
- Pass the sprint doc path and teammate assignments as context.
- Output artifacts: `scratch/team-playbooks/file_ownership.yaml`, `interface-contract.yaml`, `playbooks/tm-{name}.md`.

Wait for completion. Abort before TeamCreate if playbook generation fails. Verify that every proposed teammate's `tm-{name}.md` file exists before continuing.

### Step 6a: Pre-Worktree State Validation

Before creating any git worktrees:

1. Run `git status --porcelain`.
   - Empty → proceed to Step 7.
   - Non-empty → report the count, then ask the user: "You have N uncommitted changes. Worktree agents will NOT see these. Options: (1) commit now and proceed, (2) stash and proceed, (3) skip worktrees (shared directory), (4) abort."
2. Log the validation result in events.jsonl.

Sprint 64 lost 2 quality agents and 4+ merge iterations to stale-code worktrees — do not skip this gate.

### Step 7: Create Team and Worktrees

Call `TeamCreate(team_name={team-name}, description={sprint goals summary})`. If the team already exists, abort and ask the user to clean up the prior team.

After TeamCreate succeeds, create worktrees for each non-lead teammate using the recipe at `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: worktree-setup). The recipe covers: `.trees/` in .gitignore, base-branch selection (sprint-{N}-integration or main), `git worktree add`, `.env` + `.trw/config.yaml` copy into the worktree, and failure handling (spawn in main worktree with a `[WARN]` prefix).

Record worktree paths for Step 8 playbook injection.

### Step 8: Spawn Teammates

For each approved teammate (implementers first, then testers, then reviewers):

1. Read the generated playbook from `scratch/team-playbooks/playbooks/tm-{name}.md`.
2. If a worktree was created, prepend to the playbook: `**Working Directory**: {absolute_worktree_path}` followed by a `pwd` verification note.
3. Spawn via `Task` with:
   - `team_name`: the created team name
   - `name`: teammate name (e.g., "implementer-1")
   - `subagent_type`: `trw-implementer` / `trw-tester` / `trw-reviewer` / `trw-researcher`
   - `model`: as proposed
   - `prompt`: full playbook content

Spawn one teammate at a time; confirm each spawn before the next.

Note: playbooks produced by `/trw-team-playbook` already include auto-injected learnings (PRD-CORE-075) when `agent_learning_injection: true`. No extra action needed.

### Step 9: Create Tasks

Per PRD, create three tasks:

- **Implementation**: `Implement {PRD-ID}: {title}` → owner: implementer.
- **Test**: `Test {PRD-ID}: {title}` → owner: tester, `addBlockedBy` the implementation task.
- **Review** (sprint-level, one total): `Review sprint: {team-name}` → owner: reviewer, `addBlockedBy` ALL implementation + test tasks.

Each description includes specific FRs, files, and acceptance criteria. Reference PRD ID explicitly.

### Step 10: Enter Delegate Mode

Stay in coordination mode — do NOT implement code directly. Monitor via `TaskList`, respond to teammate messages, redirect approaches that aren't working, synthesize findings.

### Step 11: Report Results

Output a structured summary: team name, teammate count, task counts (impl/test/review), task assignments table, file-ownership artifact path, playbook paths, and "Next Steps" (panel cycling, `/trw-sprint-finish` hint).

## Shutdown Protocol

When all tasks are complete:

1. Verify all tasks show `completed` via `TaskList`.
2. Run `trw_build_check(scope="full")`.
3. Spawn integration reviewer using the Explore prompt at `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: integration-review-prompt). Wait up to 120s; on timeout, log `[WARN] Integration reviewer timed out.` and proceed.
4. `SendMessage(shutdown_request)` to each teammate.
5. Merge worktree branches — CRITICAL, changes are lost if skipped:
   a. Enumerate via `git worktree list --porcelain`.
   b. For each: commit stray uncommitted changes, then `git merge {branch} --no-edit`; resolve any conflicts manually.
   c. Run `trw_build_check(scope="unit")` post-merge; fix before continuing.
6. Clean up worktrees (only after step 5 merge verified):
   `git worktree remove ...` for each, then `git worktree prune`, then `git branch -d trw-*`. A refused delete means unmerged work — merge first.
7. Call `TeamDelete`.
8. Run `/trw-deliver`.

## Rationalization Watchlist

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The team structure is obvious, I'll skip user approval" | Misaligned teams waste whole sprint budgets | Past sprints without approval required full re-spawning |
| "Playbook generation is slow, I'll spawn with inline prompts" | Inline prompts skip file ownership validation — the #1 Agent Teams failure | Two teammates on one file creates merge conflicts + unreviewed code |
| "I know the ownership, I don't need zero-overlap validation" | Violations cause silent overwrites discovered only at REVIEW | One conflict cascades into 3-4 re-implementation tasks |
| "The worktree is clean, rm -rf is fine" | Branches may have committed but unmerged changes — rm -rf destroys them | Entire sprint of work lost, requires re-implementation from output logs |

## Notes

- Composes `/trw-team-playbook` (Step 6); does not duplicate playbook logic.
- Only the team lead should run this skill — teammates cannot create teams.
- If `/trw-team-playbook` fails, abort before `TeamCreate`.
- Sprint doc must exist — run `/trw-sprint-init` first if needed.
- One team per session — clean up prior team before re-running.
- Maximum 5 teammates — coordination overhead grows quadratically past this.
- Every teammate MUST have a playbook before spawn — never skip Step 6.
- Zero-overlap in file_ownership.yaml must hold before any spawn.
