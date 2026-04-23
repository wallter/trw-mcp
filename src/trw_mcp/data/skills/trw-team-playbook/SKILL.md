---
name: trw-team-playbook
description: >
  Generate structured teammate playbooks with file ownership and YAML interface
  contracts for Agent Teams. Produces per-teammate playbook files and a validated
  file_ownership.yaml. Use: /trw-team-playbook [sprint-doc-path]
user-invocable: true
argument-hint: "[sprint-doc-path or structured args]"
---

# Team Playbook Generation Skill

Use when: you need per-teammate playbooks and a file_ownership.yaml generated from a sprint document.

Generate structured artifacts for Agent Teams: file ownership maps, interface contracts, and per-teammate playbooks. These artifacts prevent the #1 Agent Teams failure mode (file conflicts) and ensure consistent teammate coordination from sprint context through teammate spawn.

Canonical template shapes live in `trw-mcp/src/trw_mcp/data/playbook-template.yaml`. Sections of the template are referenced by name below (`section: X`); the fields below describe how to customize each one.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRD files. Sprint docs are in the sibling `sprints/active/` directory. Output artifacts go to the active run's `scratch/team-playbooks/` directory.

## Workflow

### Step 1: Parse Arguments

Check `$ARGUMENTS` for a sprint document path:

- If a valid file path is provided, read it as the sprint context document.
- If empty or "active", look for the sprint doc in `sprints/active/` (sibling of `prds_relative_path`). If exactly one file exists, use it. If multiple exist, list them and ask the user to pick one.
- If no sprint doc is found anywhere, ask the user to provide: (a) the sprint doc path, or (b) a list of PRD IDs and teammate names to generate playbooks for directly.

### Step 2: Read Sprint Document

Parse the sprint doc to extract sprint name + goals, the PRD assignments table (PRD ID, title, priority, track, assigned role), and track structure (Track A = implementer-1, Track B = implementer-2, etc.).

Build a teammate list. Each entry has:
- `name`: lowercase hyphenated (e.g., `implementer-1`, `tester-1`, `reviewer`)
- `role`: one of `implementer`, `tester`, `reviewer`, `researcher`
- `assigned_prds`: list of PRD IDs this teammate is responsible for

If roles are not assigned in the sprint doc, apply the defaults from the template at `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: team-composition).

### Step 3: Read Each PRD

For each assigned PRD, read the PRD file and extract:

- **Functional requirements**: FR ID, priority (P0/P1/P2/P3), description (1-sentence summary)
- **Technical Approach section**: Any file paths, module names, or class names mentioned
- **Key Files table** (if present): explicit source-to-test file mappings
- **Acceptance criteria**: per-FR success conditions
- **Dependencies**: other PRDs or external components this PRD depends on

If an execution plan exists at `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`, read it and use its micro-task decomposition, wave plan, and file ownership mapping as the primary source for task and file assignments. This eliminates the need to re-derive file ownership from the PRD's Technical Approach section.

Group the extracted FRs by teammate based on the sprint doc's PRD assignments.

### Step 4: Determine File Ownership

For each teammate, derive their exclusive file set:

- **Implementer**: source files from the PRD's Key Files table or Technical Approach section. If no files are listed explicitly, use Glob to find files matching the module paths mentioned in the PRD, then assign by functional area.
- **Tester**: test files corresponding to the implementer's source files. Convention: `tests/test_{module_name}.py` for each `src/trw_mcp/{module_name}.py`.
- **Reviewer / Researcher**: no owned files -- read-only access to all in-scope files.

Validate zero overlap (source AND test files):

- Build a flat set of ALL ownership claims: `owns` + `test_owns` from every teammate.
- Check for any file appearing in more than one teammate's `owns` OR `test_owns` lists.
- If overlap detected in source files (`owns`): STOP. Report and ask user to resolve.
- If overlap detected in test files (`test_owns`): STOP. Report and ask user to resolve. Common resolution: assign the test file to ONE teammate (typically the one whose PRD scope covers more of the file's test cases), and have the other teammate's tests go in a NEW test file (e.g., `test_module_fr01.py` and `test_module_fr02.py`).
- Test files are NOT shared resources. Sprint 66 showed that two agents editing the same test file causes 4+ merge iterations and cascading failures.

Also check: every source file in scope is assigned to exactly one owner. Report any unassigned files and ask the user whether to assign them or mark them as shared read-only.

### Step 5: Generate file_ownership.yaml

Write `scratch/team-playbooks/file_ownership.yaml` using the shape from `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: file-ownership). Fields to customize:

- `generated`: current ISO 8601 timestamp.
- `sprint`: sprint name from the sprint doc.
- `teammates`: one entry per teammate with `role`, `owns`, `test_owns`, `does_not_own`, and (for reviewers) `read_only` globs.

The `does_not_own` list for each teammate is the union of all other teammates' `owns` + `test_owns` lists.

### Step 6: Generate Interface Contracts

For each shared boundary between teammates (implementer-to-tester, implementer-to-implementer if they share a module), document the contract.

Use Grep to find actual function signatures and Pydantic model definitions in the relevant files. Do not fabricate -- use real patterns from the codebase.

Write `scratch/team-playbooks/interface-contract.yaml` using the shape from `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: interface-contract). Customize `boundary`, `functions`, `schemas`, `shared_paths`, and `negative_constraints` per contract. Omit contract entries for read-only roles (reviewer, researcher).

### Step 7: Generate Per-Teammate Playbooks

For each teammate, write `scratch/team-playbooks/playbooks/tm-{name}.md`. The seven canonical sections live in `trw-mcp/src/trw_mcp/data/playbook-template.yaml` (section: playbook-sections). Copy the section body verbatim; fill in `{placeholders}` with sprint- and teammate-specific values.

Section order:

1. `section-1-identity-and-mission` — role, sprint, model recommendation, working directory, 1-2 sentence mission.
2. `section-2-file-ownership` — render owns / test_owns / does_not_own from Step 5.
3. `section-3-interface-contracts` — paste the relevant blocks from Step 6.
4. `section-4-tasks` — one task per assigned FR, in priority order (P0 first). For testers, prepend `section-4-tester-prefix` (context isolation). For implementers, append `section-4-implementer-coord-addendum` to their coordination section.
5. `section-5-quality-standards` — self-review checklist + test expectations.
6. `section-6-shard-protocol` — include ONLY for implementers with 3+ tasks. Omit for testers, reviewers, researchers.
7. `section-7-coordination` — message directives, file-conflict escalation, completion promise.

Token budget enforcement: After generating each playbook, estimate its token count as `word_count * 1.33`. If the estimate exceeds 3000 tokens:

1. Truncate Section 6 (Shard Protocol) first -- remove entirely if needed.
2. Truncate Section 7 (Coordination) to bullet points only.
3. Truncate Section 5 (Quality Standards) to checklist only.
4. Report the truncation in the summary output.

Never truncate Sections 1-4 -- those are the minimum viable playbook.

### Step 7b: Learning Injection (PRD-CORE-075)

After generating each teammate's playbook, inject task-relevant learnings from prior sessions. This step requires `agent_learning_injection: true` in config (the default).

For each teammate:

1. Extract their task description (from Section 4) and file ownership list (from Section 2).
2. Call `select_learnings_for_task(task_description, file_paths)` from `trw_mcp.state.learning_injection` to query recall for relevant learnings, ranked by domain tag overlap and impact score.
3. Call `format_learning_injection(selected_learnings)` to render a markdown section.
4. Append the formatted section to the teammate's playbook file, immediately after Section 4 (Tasks) and before Section 5 (Quality Standards).

If `agent_learning_injection` is `false` in config, skip this step. If no learnings are found for a teammate, skip injection for that teammate (do not add an empty section).

Configuration controls (from `.trw/config.yaml`):
- `agent_learning_injection`: toggle on/off (default: true)
- `agent_learning_max`: maximum learnings per teammate (default: 5)
- `agent_learning_min_impact`: minimum impact score threshold (default: 0.5)

### Step 8: Report Results

Output a summary table:

```
## /trw-team-playbook complete

Sprint: {sprint name}
Artifacts written to: scratch/team-playbooks/

| Teammate      | Role        | Owned Files | Tasks | Est. Tokens | Status |
|---------------|-------------|-------------|-------|-------------|--------|
| implementer-1 | implementer | 3           | 4     | 1,850       | OK     |
| tester-1      | tester      | 2           | 3     | 1,420       | OK     |
| reviewer      | reviewer    | 0 (r/o)     | 1     | 980         | OK     |

File ownership validation: PASS (zero overlap, all in-scope files assigned)
Interface contracts: 2 generated

Generated artifacts:
- scratch/team-playbooks/file_ownership.yaml
- scratch/team-playbooks/interface-contract.yaml
- scratch/team-playbooks/playbooks/tm-{name}.md  (one per teammate)

Next step: Run /trw-sprint-team to create the team and spawn teammates using these playbooks.
```

If any validation failed (file overlap, unresolved ambiguity), report FAIL and list the issues. Do not report "Next step" if validation failed.

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The file ownership is obvious, I don't need validation" | Ownership violations cause silent overwrites discovered only at REVIEW | One conflict cascades into 3-4 re-implementation tasks |
| "I'll skip the interface contract, the code is self-documenting" | Contracts prevent field-name mismatches between teammates | Missing contracts cause integration failures at VALIDATE |
| "Token budget doesn't matter, I'll include everything" | Oversized playbooks exceed context limits and degrade agents | Past ~150 rules agents start ignoring critical guidance |

## Notes

- Re-running the skill overwrites existing playbooks -- this is intentional (idempotent).
- If the active run directory (`scratch/`) does not exist, call `trw_init(task_name="{sprint-name}")` to create it first.
- Token estimation: `word_count * 1.33 ~= token_count`. Count words in the rendered markdown, not the raw template.
- Playbooks are markdown -- they are designed to be pasted directly into teammate spawn prompts (via the `Task` tool's `prompt` parameter). Keep prose tight and imperative.
- Never fabricate code patterns. Use Grep to verify real function signatures and model field names before writing them into contracts.
- If a PRD has no Key Files table and no explicit file paths in Technical Approach, use Glob to find candidate files by module name, then confirm with the user before assigning ownership.
- Reviewers and researchers never appear in `owns` lists -- listing them there would create a false file lock. Their `read_only` list can be a glob pattern (`src/**`) rather than explicit paths.
