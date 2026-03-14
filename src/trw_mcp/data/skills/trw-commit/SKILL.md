---
name: trw-commit
description: >
  Convention-enforced git commit. Analyzes changes, generates
  type(scope): msg format with WHY rationale and PRD-ID linking.
  Use: /trw-commit or /trw-commit "optional message hint"
user-invocable: true
model: claude-sonnet-4-6
disable-model-invocation: true
argument-hint: "[optional message hint]"
allowed-tools: Read, Grep, Glob, Bash
---

# Convention-Enforced Commit Skill

Create a git commit following TRW project conventions: `type(scope): message` format with `WHY:` rationale, PRD-ID linking, and Co-Authored-By trailer.

## Workflow

1. **Check git status and branch**: Run `git status -sb` to see all changed files. If no changes exist, report "Nothing to commit" and exit.

   **Branch naming check**: Run `git branch --show-current`. If the branch does NOT match pattern `trw-[a-z0-9-]+-[a-z]+`:
   ```
   [WARN] Current branch '{branch}' does not follow TRW naming convention (trw-{prd-id}-{role}).
   Commits on this branch will not be automatically included in sprint integration.
   ```
   Ask for confirmation before proceeding. If the branch matches, continue silently.

2. **Analyze changes**: Run `git diff --stat` and `git diff` (staged + unstaged) to understand what changed:
   - Identify affected modules (tools/, state/, models/, tests/, skills/, docs/)
   - Determine change type: `feat` (new feature), `fix` (bug fix), `refactor` (restructure), `docs` (documentation), `chore` (maintenance), `test` (tests only)
   - Determine scope from the primary module affected

3. **Check recent commits**: Run `git log --oneline -5` to match the repository's commit style.

4. **Check for new dependencies**: Run `git diff --cached` and scan for `+` lines in `requirements.txt`, `pyproject.toml`, and `package.json`. If any new package names are found, output:
   ```
   New dependencies detected: [{package_names}]. Confirm these have been scanned by `trw_build_check(scope='deps')` before committing.
   ```
   This is advisory only — do not block the commit on this warning. If no new dependencies found, skip silently.

5. **Identify FR context**: Check for an active run (`.trw/context/run.yaml` or find the active run directory). If a run is active:
   - Extract the PRD IDs from the run's `prd_scope` field
   - Read the PRD(s) to enumerate FRs
   - If multiple FRs exist: prompt "Which FR does this commit address? (FR01 / FR02 / ... / custom)"
   - If exactly 1 FR exists: auto-populate the FR trailer without prompting
   - If no run is active or no PRD context found: skip FR trailer (warn if `commit_fr_trailer_enabled` is true in config)

6. **Find PRD context**: Search changed files for PRD references (grep for `PRD-` in modified files and in any active sprint docs). If a PRD is being implemented, include it in the commit.

7. **Generate commit message**: Following the format:
   ```
   type(scope): concise description of WHAT changed

   WHY: rationale for the change
   FR: PRD-{ID}-FR{NN}
   PRD: PRD-{ID}
   AI-Provenance: model={model_id}, agent={agent_role}, shard={run_id}
   PRD-Scope: PRD-{ID}
   Security-Scan: pip-audit={PASS|FAIL|SKIP}, trw-review={PASS|FAIL|SKIP}(confidence={n})

   Co-Authored-By: Claude <noreply@anthropic.com>
   ```

   - The `FR:` and `PRD:` trailers are added when `commit_fr_trailer_enabled` is true (default) in `.trw/config.yaml`
   - The `AI-Provenance:`, `PRD-Scope:`, and `Security-Scan:` trailers are added when `provenance_enabled` is true (default) in `.trw/config.yaml`
   - `model_id`: read from active run's `run.yaml` field `model`, fallback to `unknown`
   - `agent_role`: read from `run.yaml` field `agent_type`, fallback to `unknown`
   - `run_id`: read from `run.yaml` field `run_id`, fallback to `unknown`
   - `pip-audit`: read from `.trw/context/build-status.yaml` field `pip_audit_passed` (true→PASS, false→FAIL, absent→SKIP)
   - `trw-review`: read from active run's `review.yaml` field `verdict` (pass/warn→PASS, block→FAIL, absent→SKIP)
   - `confidence`: minimum confidence of surfaced findings from `review.yaml`, or omit if no review
   - If `$ARGUMENTS` contains a message hint, use it to inform the description
   - Keep the first line under 72 characters
   - The WHY line explains the motivation, not the mechanics

8. **Stage files**: Run `git add` for the relevant files. Exclude:
   - `.env`, credentials, secrets
   - Large binary files
   - `.trw/logs/` debug logs
   - Run artifacts (`docs/*/runs/`)

9. **Confirm with user**: Show the proposed commit message and staged files. Ask for confirmation before committing.

10. **Execute commit**: Run `git commit` with the message via HEREDOC format.

11. **Optional PR creation**: After the commit succeeds, ask: "Create a PR for this branch? (yes/no)"

    If yes:
    - Determine PR base: read `sprint_integration_branch_pattern` from `.trw/config.yaml` (default: `sprint-{N}-integration`). Check if that branch exists with `git branch --list`. If not, use `main`.
    - Read `PR-TEMPLATE.md` from the skill directory and populate placeholders from:
      - `run.yaml` for Agent Metadata fields
      - `build-status.yaml` for Automated Checks fields
      - `git diff --stat {base}..HEAD` for Change Summary fields
      - Commit messages for WHY section
    - Run `gh pr create --base {base_branch} --title "{first_line}" --body "{populated_template}"`
    - If `gh` CLI is not available or fails: output the populated PR body as markdown for manual paste
    - Display the PR URL on success

12. **Report**: Show commit hash, files committed, branch name, and PR URL (if created).

## Commit Type Reference

| Type | When |
|------|------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructure without behavior change |
| `docs` | Documentation only |
| `chore` | Maintenance, cleanup, dependency updates |
| `test` | Test additions or fixes only |
| `perf` | Performance improvement |

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "I'll use git add -A, it's faster" | git add -A stages everything including secrets, large binaries, and run artifacts | One accidentally committed .env file means rotating all credentials |
| "The commit message doesn't need a WHY line" | The WHY line is how future agents understand the rationale behind changes | Future agents who read git log can't distinguish intentional changes from accidental ones |
| "I'll skip the user confirmation, the changes are obvious" | User confirmation catches unintended staged files and wrong commit types | One wrong commit type (feat vs fix) breaks changelog generation and release notes |

## AI Authorship Query Reference

```bash
# Count AI-generated commits
git log --grep="AI-Provenance:" --oneline | wc -l

# List unique models used
git log --format=%B | grep "^AI-Provenance:" | sed 's/.*model=\([^,]*\).*/\1/' | sort | uniq -c

# Find commits for a specific FR
git log --grep="FR: PRD-CORE-055-FR03" --oneline
```

## Constraints

- NEVER commit files that contain secrets (.env, credentials, API keys)
- NEVER use `git add -A` or `git add .` — always stage specific files
- NEVER amend previous commits unless explicitly asked
- NEVER skip pre-commit hooks (no --no-verify)
- ALWAYS confirm the commit message with the user before executing
- ALWAYS use HEREDOC format for multi-line commit messages
