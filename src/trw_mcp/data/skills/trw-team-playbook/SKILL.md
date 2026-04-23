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

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRD files. Sprint docs are in the sibling `sprints/active/` directory. Output artifacts go to the active run's `scratch/team-playbooks/` directory.

## Workflow

### Step 1: Parse Arguments

Check `$ARGUMENTS` for a sprint document path:

- If a valid file path is provided, read it as the sprint context document.
- If empty or "active", look for the sprint doc in `sprints/active/` (sibling of `prds_relative_path`). If exactly one file exists, use it. If multiple exist, list them and ask the user to pick one.
- If no sprint doc is found anywhere, ask the user to provide: (a) the sprint doc path, or (b) a list of PRD IDs and teammate names to generate playbooks for directly.

### Step 2: Read Sprint Document

Parse the sprint doc to extract:

- Sprint name and goals
- PRD assignments table (PRD ID, title, priority, track, assigned role)
- Track structure (Track A = implementer-1, Track B = implementer-2, etc.)

Build a teammate list. Each entry has:
- `name`: lowercase hyphenated (e.g., `implementer-1`, `tester-1`, `reviewer`)
- `role`: one of `implementer`, `tester`, `reviewer`, `researcher`
- `assigned_prds`: list of PRD IDs this teammate is responsible for

If roles are not assigned in the sprint doc, apply these defaults based on team composition rules:

| PRD Count | Implementers | Testers | Reviewers | Total |
|-----------|-------------|---------|-----------|-------|
| 1-2       | 1           | 1       | 0         | 2     |
| 3-4       | 2           | 1       | 0         | 3     |
| 5-6       | 2           | 1       | 1         | 4     |
| 7+        | 3           | 1       | 1         | 5     |

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
- **Reviewer**: no owned files -- read-only access to all in-scope files.
- **Researcher**: no owned files -- read-only access to all in-scope files.

Validate zero overlap (source AND test files):

- Build a flat set of ALL ownership claims: `owns` + `test_owns` from every teammate.
- Check for any file appearing in more than one teammate's `owns` OR `test_owns` lists.
- If overlap detected in source files (`owns`): STOP. Report and ask user to resolve.
- If overlap detected in test files (`test_owns`): STOP. Report and ask user to resolve. Common resolution: assign the test file to ONE teammate (typically the one whose PRD scope covers more of the file's test cases), and have the other teammate's tests go in a NEW test file (e.g., `test_module_fr01.py` and `test_module_fr02.py`).
- Test files are NOT shared resources. Sprint 66 showed that two agents editing the same test file causes 4+ merge iterations and cascading failures.
- If no overlap: continue.

Also check: every source file in scope is assigned to exactly one owner. Report any unassigned files and ask the user whether to assign them or mark them as shared read-only.

### Step 5: Generate file_ownership.yaml

Write `scratch/team-playbooks/file_ownership.yaml`:

```yaml
# file_ownership.yaml -- generated by /trw-team-playbook
version: '1.0'
generated: '{ISO 8601 timestamp}'
sprint: '{sprint name}'
teammates:
  implementer-1:
    role: implementer
    owns:
      - src/trw_mcp/tools/example_tool.py
      - src/trw_mcp/state/example_state.py
    test_owns:
      - tests/test_example_tool.py
    does_not_own:
      - src/trw_mcp/tools/other_tool.py
      - tests/test_other_tool.py
  tester-1:
    role: tester
    owns:
      - tests/test_integration_example.py
    does_not_own:
      - src/trw_mcp/tools/example_tool.py
  reviewer:
    role: reviewer
    owns: []
    read_only:
      - src/**
      - tests/**
validation:
  no_overlap: true
  all_in_scope_files_assigned: true
```

The `does_not_own` list for each teammate is the union of all other teammates' `owns` + `test_owns` lists.

### Step 6: Generate Interface Contracts

For each shared boundary between teammates (implementer-to-tester, implementer-to-implementer if they share a module), document the contract.

Use Grep to find actual function signatures and Pydantic model definitions in the relevant files. Do not fabricate -- use real patterns from the codebase.

Write `scratch/team-playbooks/interface-contract.yaml`:

```yaml
# interface-contract.yaml -- generated by /trw-team-playbook
version: '1.0'
generated: '{ISO 8601 timestamp}'
contracts:
  - boundary: "implementer-1 -> tester-1"
    description: "Functions and models that tester-1 will call or validate"
    functions:
      - name: example_function
        module: src/trw_mcp/tools/example_tool.py
        signature: "def example_function(arg: str, config: TRWConfig) -> ExampleResult"
        notes: "Returns ExampleResult -- do not call with raw dict"
    schemas:
      - name: ExampleResult
        module: src/trw_mcp/models/run.py
        fields:
          - name: status
            type: str
            enum: [success, failure, skipped]
          - name: artifacts
            type: list[Path]
            required: true
    shared_paths:
      - .trw/config.yaml
    negative_constraints:
      - "DO NOT return raw dict -- use typed Pydantic model"
      - "DO NOT change ExampleResult field names without notifying tester-1"
```

If a boundary has no shared functions or schemas (e.g., reviewer is read-only), omit the contract entry for that boundary.

### Step 7: Generate Per-Teammate Playbooks

For each teammate, write `scratch/team-playbooks/playbooks/tm-{name}.md`.

Each playbook has exactly these sections, in order:

---

**Section 1: Identity and Mission**

```markdown
# Teammate Playbook: {name}

**Role**: {role} | **Sprint**: {sprint name}
**Model recommendation**: {Opus for reviewer, Sonnet for implementer/tester}
**Working Directory**: {absolute_worktree_path_or_"main worktree (repo root)"}

Verify `pwd` matches your Working Directory before beginning any file edits. If your working directory does not match, run `cd {working_directory}` first.

## Mission

{1-2 sentence purpose statement tied to sprint goals and assigned PRDs.
Example: "Implement the observability hooks in PRD-CORE-031. Your work enables
the tester to validate that all 14 hook events fire in the correct sequence."}
```

**Section 2: File Ownership**

```markdown
## File Ownership

You own these files exclusively -- only you may modify them:

### Source files
- `src/trw_mcp/tools/example_tool.py`

### Test files
- `tests/test_example_tool.py`

### DO NOT modify (owned by other teammates)
- `src/trw_mcp/tools/other_tool.py` -- owned by implementer-2
- `tests/test_other_tool.py` -- owned by tester-1
```

**Section 3: Interface Contracts**

```markdown
## Interface Contracts

### What you provide to tester-1

{Paste the relevant contract block from interface-contract.yaml in YAML code fence.}

### What you consume from implementer-2 (if applicable)

{Paste the relevant contract block.}
```

If the teammate is a reviewer or researcher with no contracts, write: "No interface contracts -- read-only role."

**Section 4: Tasks**

```markdown
## Tasks

### Task 1: Implement {FR-ID} -- {short description}

**PRD**: PRD-{ID} | **Priority**: P{n}

**What to build**: {2-3 sentence description of the concrete deliverable}

**Acceptance criteria**:
- [ ] {criterion 1}
- [ ] {criterion 2}

**Files to modify**: `src/trw_mcp/tools/example_tool.py`

---

### Task 2: ...
```

List tasks in priority order (P0 first). Each task maps to exactly one FR ID.

**For tester teammates, prepend this to Section 4:**

```markdown
## CONTEXT ISOLATION

Do NOT read implementation files (*.py source files in src/ or app/) before writing your tests. Read ONLY:
1. PRD FR acceptance criteria from the execution plan
2. Test skeleton functions from the test skeleton files

Your tests must verify the specification, not the implementation. Write tests that would pass for ANY correct implementation, not just the one your teammate wrote.
```

**For implementer teammates, add to Section 7 (Coordination):**

```markdown
### Test Integration
- Implement against the failing tests in the test skeleton files
- Do NOT modify test files — make the tests pass by implementing correct behavior
- If a test seems wrong, message the tester before modifying it
```

**Section 5: Quality Standards**

```markdown
## Quality Standards

### Self-review checklist (complete before marking any task done)

1. Re-read assigned FRs -- verify every requirement is implemented, not just the easy ones
2. Check integration -- new functions are imported and called from existing code
3. Review your diff for DRY (no duplicated logic), KISS (minimum viable), SOLID (single responsibility)
4. Run `trw_build_check(scope="full")` -- pytest + mypy must pass across the full codebase
5. Write a completion checkpoint: which FRs implemented, test count, integration points touched

### Test expectations

- Coverage target: >=90% for new code
- All edge cases covered: empty inputs, missing files, invalid config
- mypy --strict must pass with no new errors
- Test file: `tests/test_{module}.py`
```

**Section 6: Shard Protocol**

Include only if the teammate is an implementer with 3+ tasks:

```markdown
## Shard Protocol (optional)

If your task scope is large, you may decompose into internal shards:

- Maximum 4 shards, launched in parallel in ONE message
- Each shard gets a non-overlapping subset of your exclusive files
- Shards write results to `scratch/tm-{name}/shards/shard-{id}/result.yaml`
- You aggregate shard outputs after all complete
- Shards MUST NOT spawn sub-shards (depth 1 max)
```

If the teammate is a tester, reviewer, or researcher, omit this section entirely.

**Section 7: Coordination**

```markdown
## Coordination

### Message these teammates when:

- **tester-1**: when you change a function signature, model field, or file path they test
- **reviewer** (if present): when a task is complete and ready for review

### Receive messages from:

- **tester-1**: if tests reveal bugs in your implementation, fix them in your owned files
- **team lead**: for task reassignment or priority changes

### If you discover a file conflict:

STOP. Message the team lead immediately with: which file, which teammate you think owns it, what you needed to change.

### Reporting completion:

When all your tasks are done, call `TaskUpdate` to mark each task `completed`, then message the team lead: "All tasks complete. Build check passed. Ready for shutdown."

### Completion Promise

As your FINAL action before completing, output this exact text:

```
SHARD_COMPLETE: {your-teammate-name} — all acceptance criteria verified
```

This is a machine-readable completion signal that the TaskCompleted hook verifies. Missing this signal triggers a warning (or block, if `completion_hooks_blocking=true`).
```

---

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

If `agent_learning_injection` is `false` in config, skip this step entirely. If no learnings are found for a teammate, skip injection for that teammate (do not add an empty section).

The injected section looks like:

```markdown
## Task-Relevant Learnings (auto-injected)

The following learnings from prior sessions are relevant to your current task. Treat them as high-priority constraints.

- **[L-042]** require_org_admin must accept both admin and owner roles (impact: 0.9, tags: auth, admin)
- **[L-089]** Pydantic v2: use_enum_values=True breaks comparison (impact: 0.8, tags: pydantic)
```

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
- scratch/team-playbooks/playbooks/tm-implementer-1.md
- scratch/team-playbooks/playbooks/tm-tester-1.md
- scratch/team-playbooks/playbooks/tm-reviewer.md

Next step: Run /trw-sprint-team to create the team and spawn teammates using these playbooks.
```

If any validation failed (file overlap, unresolved ambiguity), report FAIL and list the issues. Do not report "Next step" if validation failed.


## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The file ownership is obvious, I don't need validation" | File ownership violations cause silent overwrites discovered only at REVIEW | One file conflict cascades into 3-4 re-implementation tasks |
| "I'll skip the interface contract, the code is self-documenting" | Interface contracts prevent field name mismatches between teammates | Missing contracts cause integration failures that only surface at VALIDATE |
| "Token budget doesn't matter, I'll include everything" | Oversized playbooks exceed context limits and degrade agent performance | Agents past the instruction density cliff (~150 rules) start ignoring critical guidance |

## Notes

- Re-running the skill overwrites existing playbooks -- this is intentional (idempotent).
- If the active run directory (`scratch/`) does not exist, call `trw_init(task_name="{sprint-name}")` to create it first, then proceed.
- Token estimation: `word_count * 1.33 ~= token_count`. Count words in the rendered markdown, not the raw template.
- Playbooks are markdown -- they are designed to be pasted directly into teammate spawn prompts (via the `Task` tool's `prompt` parameter). Keep prose tight and imperative.
- Never fabricate code patterns. Use Grep to verify real function signatures and model field names before writing them into contracts.
- If a PRD has no Key Files table and no explicit file paths in Technical Approach, use Glob to find candidate files by module name, then confirm with the user before assigning ownership.
- Reviewers and researchers never appear in `owns` lists -- listing them there would create a false file lock. Their `read_only` list can be a glob pattern (`src/**`) rather than explicit paths.
