---
name: trw-sprint-team
description: >
  Automate Agent Teams setup from a sprint plan. Reads sprint doc, analyzes PRDs,
  proposes team structure with file ownership, and spawns teammates with generated
  playbooks. Use: /trw-sprint-team [sprint-doc-path]
user-invocable: true
model: claude-opus-4-6
disable-model-invocation: true
argument-hint: "[sprint-doc-path]"
allowed-tools: Read, Write, Glob, Grep, Bash, Edit, mcp__trw__trw_recall, mcp__trw__trw_status, mcp__trw__trw_init, mcp__trw__trw_checkpoint
---

# Sprint Team Automation Skill

Automate Agent Teams setup from a sprint plan. This skill reads the sprint document, analyzes PRD scope and complexity, proposes a team structure with file ownership, gets user approval, generates playbooks via `/trw-team-playbook`, creates the team, spawns teammates, and assigns tasks. This is the highest-leverage sprint automation — turning 30 minutes of manual team setup into a single command.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD directory. Sprint docs are in the `sprints/active/` sibling directory.

## Workflow

### Step 1: Parse Arguments

Check `$ARGUMENTS` for a sprint doc path:
- If `$ARGUMENTS` is a valid file path, read that sprint doc directly.
- If `$ARGUMENTS` is empty or "active", look for sprint docs in `sprints/active/` (sibling of `prds_relative_path`).
- If multiple active sprint docs are found, list them and ask the user to pick one.
- If no sprint doc is found, abort with: "No active sprint found. Run /trw-sprint-init first to create a sprint."

### Step 2: Read Sprint Document

From the sprint doc, extract:
- Sprint name (used to derive the team name in kebab-case)
- Sprint goals (1-3 bullet points)
- PRD assignments per track (Track A, B, C, D...)
- Existing owner assignments if any
- Implementation order constraints (e.g., "Track B blocked by Track A completion")

Derive the team name from the sprint doc filename by stripping path and extension and converting to kebab-case (e.g., `sprint-25-agent-teams-quality.md` -> `sprint-25-agent-teams-quality`).

### Step 3: Analyze PRDs

For each PRD listed in the sprint:
- Read the full PRD file (locate via `prds_relative_path` + PRD ID)
- Extract:
  - Functional Requirements count (number of FRs)
  - Technical Approach section (to understand affected modules and files)
  - Dependencies / Traceability section (cross-PRD dependencies)
- Estimate complexity: Low (1-3 FRs), Medium (4-7 FRs), High (8+ FRs)
- If an execution plan exists for a PRD (check `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`), use its wave plan and file ownership mapping instead of estimating from the PRD alone. Execution plans provide pre-computed micro-task decompositions that reduce self-decomposition variance.
- List key files likely to be created or modified (from Technical Approach)
- Note which PRDs are prerequisites for others

If a PRD file cannot be found, log a warning but continue with the information available from the sprint doc.

### Step 4: Propose Team Structure

Based on PRD count and complexity, propose a team composition using these rules:

| PRD Count | Implementers | Testers | Reviewers | Total |
|-----------|-------------|---------|-----------|-------|
| 1-2       | 1           | 1       | 0         | 2     |
| 3-4       | 2           | 1       | 0         | 3     |
| 5-6       | 2           | 1       | 1         | 4     |
| 7+        | 3           | 1       | 1         | 5     |

Model selection:
- **Implementers**: `claude-sonnet-4-6` (cost-effective for focused coding)
- **Testers**: `claude-sonnet-4-6` (cost-effective for focused testing)
- **Reviewers**: `claude-sonnet-4-6` (rubric-scored review with structured output)

For each teammate, assign:
- PRDs from the sprint (distribute evenly across implementers, group by track where possible)
- Key files based on the Technical Approach sections of assigned PRDs

Present the proposal as a formatted table:

```
## Proposed Team Structure

Team name: {team-name}

| Teammate      | Role        | Model  | PRDs            | Key Files                  |
|---------------|-------------|--------|-----------------|----------------------------|
| implementer-1 | implementer | claude-sonnet-4-6 | PRD-CORE-035    | src/trw_mcp/tools/learn.py |
| tester-1      | tester      | claude-sonnet-4-6 | PRD-CORE-035    | tests/test_tools_learn*.py |
| reviewer      | reviewer    | claude-sonnet-4-6 | All (read-only) | (read-only)                |

## PRD Dependencies

- PRD-CORE-036 depends on PRD-CORE-035 (implementer-2 blocked by implementer-1)

## File Ownership Summary

- implementer-1: src/trw_mcp/tools/learn.py, src/trw_mcp/state/recall_search.py
- tester-1: tests/test_tools_learning.py, tests/test_recall_search.py
- reviewer: read-only access to src/**
```

### Step 5: User Approval Gate

After presenting the proposal, ask explicitly:

> "Does this team structure look good? Reply 'yes' to proceed, or describe changes (e.g., 'combine tracks A and B into one implementer', 'add a second tester', 'use haiku for the tester')."

- If the user approves ("yes", "looks good", "proceed", "go ahead"): continue to step 6.
- If the user requests changes: revise the proposal and re-present. Repeat up to 3 iterations.
- After 3 rejected iterations without approval: abort with "Team setup aborted after 3 revision cycles. Please run /trw-sprint-team again when the team structure is clearer."
- Do NOT proceed to TeamCreate or teammate spawning without explicit user approval.

### Step 6: Generate Playbooks

Invoke the `/trw-team-playbook` skill with the approved structure:
- Pass the sprint doc path and approved teammate assignments as context.
- The `/trw-team-playbook` skill generates:
  - `scratch/team-playbooks/file_ownership.yaml`
  - `scratch/team-playbooks/interface-contract.yaml`
  - `scratch/team-playbooks/playbooks/tm-{name}.md` (one per teammate)

Wait for `/trw-team-playbook` to complete before proceeding. If it fails or the output artifacts are not found, abort with:
"Playbook generation failed. Cannot spawn teammates without playbooks. Fix the /trw-team-playbook error and retry."

Verify playbook artifacts exist before continuing:
- Confirm `scratch/team-playbooks/file_ownership.yaml` exists
- Confirm each `scratch/team-playbooks/playbooks/tm-{name}.md` exists for every proposed teammate

### Step 7: Create Team and Worktrees

Call `TeamCreate` with:
- `team_name`: derived team name (kebab-case from sprint doc filename)
- `description`: sprint goals summary (1-2 sentences)

If a team with the same name already exists (TeamCreate fails), abort with:
"A team named '{team-name}' already exists. Clean up the previous team first (send shutdown_request to each teammate, then TeamDelete), then re-run /sprint-team."

After TeamCreate succeeds, create worktrees for each non-lead teammate:

1. **Read worktree config**: Check `.trw/config.yaml` for `worktree_dir` (default: `.trees`).

2. **Add `.trees/` to .gitignore**: Run `grep -q "^\.trees/$" .gitignore 2>/dev/null || echo ".trees/" >> .gitignore`

3. **Determine base branch**: Check if `sprint-{N}-integration` branch exists (`git branch --list sprint-*-integration`). If yes, use it as base. If no, use current HEAD of `main` and log: `[INFO] No sprint integration branch found. Creating worktree branches from main.`

4. **For each non-lead teammate** (lead stays in main worktree):
   a. Derive branch name: `trw-{prd-id-lowercase}-{role}` (e.g., `trw-prd-infra-025-impl`). For multi-PRD assignments, use the primary (first-listed) PRD.
   b. Run: `git worktree add {worktree_dir}/{branch-name} -b {branch-name} {base_branch}`
   c. On success: copy config files to worktree:
      - `cp .env {worktree_dir}/{branch-name}/.env` (if .env exists)
      - `mkdir -p {worktree_dir}/{branch-name}/.trw && cp .trw/config.yaml {worktree_dir}/{branch-name}/.trw/config.yaml` (if config exists)
   d. On failure (`git worktree add` exits non-zero): log `[WARN] Worktree creation failed for {teammate}: {error}`. Spawn teammate in main worktree instead. Add warning to playbook: `[WARN] Worktree creation failed — operating in shared directory. File ownership enforcement applies.`

5. **Record worktree paths** for use in Step 8 playbook generation.

### Step 8: Spawn Teammates

For each approved teammate (in this order: implementers first, then testers, then reviewers):

1. Read the generated playbook from `scratch/team-playbooks/playbooks/tm-{name}.md`
2. If a worktree was created for this teammate, prepend to the playbook content:
   ```
   **Working Directory**: {absolute_worktree_path}
   Verify `pwd` matches this path before beginning any file edits.
   ```
3. Spawn via the `Task` tool with:
   - `team_name`: the created team name
   - `name`: the teammate name (e.g., "implementer-1")
   - `subagent_type`: matching agent type:
     - implementer -> `trw-implementer`
     - tester -> `trw-tester`
     - reviewer -> `trw-reviewer`
     - researcher -> `trw-researcher`
   - `model`: as proposed (claude-sonnet-4-6/claude-opus-4-6/claude-haiku-4-5-20251001)
   - `prompt`: full playbook content from `tm-{name}.md`

Spawn one teammate at a time and confirm each spawn before proceeding to the next.

Note: Playbooks generated by `/trw-team-playbook` include auto-injected learnings from prior sessions (PRD-CORE-075) when `agent_learning_injection: true` in config. These learnings are domain-relevant to each teammate's file ownership and task scope. No additional action is needed here -- the injection happens during playbook generation in Step 6.

### Step 9: Create Tasks

For each PRD in the sprint, create a structured task set:

**Implementation task** (one per PRD or per track):
- Subject: `Implement {PRD-ID}: {PRD title}`
- Description: List the specific FRs to address, files to modify, and acceptance criteria. Reference PRD ID explicitly.
- Assign to: the implementing teammate via `TaskUpdate(owner="{teammate-name}")`

**Test task** (one per implementation task):
- Subject: `Test {PRD-ID}: {PRD title}`
- Description: Test scenarios to cover (happy path, edge cases, error conditions), coverage target (>=90% for new code)
- Blocked by: the corresponding implementation task via `addBlockedBy`
- Assign to: the tester teammate

**Review task** (one for the whole sprint):
- Subject: `Review sprint: {team-name}`
- Description: Quality review across all implemented PRDs. Check DRY/KISS/SOLID, integration gaps, coverage, and mypy.
- Blocked by: ALL implementation and test tasks via `addBlockedBy`
- Assign to: the reviewer teammate (if one exists), otherwise omit

Set `addBlockedBy` in `TaskCreate` or via `TaskUpdate` after creation to enforce dependency ordering.

### Step 10: Enter Delegate Mode

After all teammates are spawned and tasks assigned:
- The lead (you) stays in coordination mode — do NOT implement code directly.
- Monitor progress via `TaskList` when teammates report completion.
- Respond to teammate messages and redirect approaches that are not working.
- Synthesize findings as they come in.

### Step 11: Report Results

Output a structured summary:

```
## Team Setup Complete

**Team**: {team-name}
**Teammates**: {count} ({names})
**Tasks created**: {total} ({impl} implementation, {test} test, {review} review)

### Task Assignments

| Task | Owner | Blocked By |
|------|-------|------------|
| Implement PRD-CORE-035: ... | implementer-1 | -- |
| Test PRD-CORE-035: ...      | tester-1      | Implement PRD-CORE-035 |
| Review sprint: ...          | reviewer      | All above |

### File Ownership

See: scratch/team-playbooks/file_ownership.yaml

### Playbooks

- scratch/team-playbooks/playbooks/tm-implementer-1.md
- scratch/team-playbooks/playbooks/tm-tester-1.md
- scratch/team-playbooks/playbooks/tm-reviewer.md (if applicable)

### Next Steps

- Use Shift+Down to cycle through teammate panels
- Use Ctrl+T for task list
- Teammates will message you when tasks complete or when they need input
- Run /trw-sprint-finish when all tasks show 'completed' status
```

## Shutdown Protocol

When all tasks are complete:

1. **Verify all tasks** show `completed` status via `TaskList`

2. **Run build check**: `trw_build_check(scope="full")` to validate integration

3. **Spawn integration reviewer** (INFRA-027-FR01): Before shutting down teammates, spawn an Explore agent to perform integration review:
   - Get the full merged diff: `git diff main...HEAD` (or `git diff sprint-{N}-integration...HEAD`)
   - Read `scratch/team-playbooks/file_ownership.yaml` for per-shard file assignments
   - Spawn via Agent tool with subagent_type "Explore":
     ```
     prompt: "Review the following merged diff for cross-shard integration issues:
     1. Duplicate functions defined in 2+ shard diffs (non-test functions only)
     2. Inconsistent shared types (same class/TypedDict defined differently)
     3. Unresolved imports (import names not exported by their module)
     4. API contract mismatches (caller args don't match callee signature)
     5. Cross-shard DRY violations: similar helper functions (>70% logic overlap)
        implemented independently by different shards — flag for extraction into
        a shared utility module
     6. Spec-based test gaps: for each FR in the linked PRD(s), verify that at least
        one test asserts the acceptance criterion (not just that the code runs).
        Flag tests that only check status codes or `is not None` without verifying
        response content against the spec.

     Produce findings as YAML with: check_name, severity (critical/warning/info), description, file_path, line_number.
     Write results to integration-review.yaml in the run directory."
     ```
   - Wait up to 120 seconds for the integration reviewer to complete
   - If timeout: log `[WARN] Integration reviewer timed out. Proceeding without integration review.`

4. **Send shutdown_request** to each teammate via `SendMessage`

5. **After all teammates confirm shutdown**, clean up worktrees:
   a. For each `.trees/` subdirectory: `rm -rf {worktree_dir}/{branch-name}/`
   b. Run: `git worktree prune`
   c. Run: `git branch -d trw-*` (safe delete — refuses unmerged branches)
   d. Log any unmerged branches as warnings

6. **Call TeamDelete** to remove team infrastructure

7. **Run `/trw-deliver`** for full ceremony

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The team structure is obvious, I'll skip user approval" | User approval prevents misaligned team structures that waste entire sprint budgets | Past sprints without approval had teammate assignments that required full re-spawning |
| "Playbook generation is slow, I'll spawn teammates with inline prompts" | Inline prompts skip file ownership validation — the #1 Agent Teams failure mode | Two teammates editing the same file creates merge conflicts and unreviewed code |
| "I know the file ownership, I don't need to validate zero overlap" | File ownership violations cause silent overwrites that are discovered only at REVIEW | One file conflict can cascade into 3-4 re-implementation tasks |

## Notes

- This skill composites `/trw-team-playbook` (step 6) — it does not duplicate playbook generation logic.
- Only the team lead should run this skill — teammates cannot create teams.
- If `/trw-team-playbook` fails, abort before `TeamCreate` is called.
- Sprint doc must exist before running — use `/trw-sprint-init` first if needed.
- The team name is derived from the sprint doc filename (e.g., `sprint-25-agent-teams-quality.md` -> `sprint-25-agent-teams-quality`).
- One team per session — clean up the previous team first if one exists.
- Maximum 5 teammates per team — coordination overhead grows quadratically beyond this.
- Each teammate MUST have a playbook before spawning — never skip step 6.
- File ownership validated before any teammate is spawned — the file_ownership.yaml zero-overlap guarantee must hold.
