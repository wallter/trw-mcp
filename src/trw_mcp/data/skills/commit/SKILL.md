---
name: commit
description: >
  Convention-enforced git commit. Analyzes changes, generates
  type(scope): msg format with WHY rationale and PRD-ID linking.
  Use: /commit or /commit "optional message hint"
user-invocable: true
argument-hint: "[optional message hint]"
allowed-tools: Read, Grep, Glob, Bash
---

# Convention-Enforced Commit Skill

Create a git commit following TRW project conventions: `type(scope): message` format with `WHY:` rationale, PRD-ID linking, and Co-Authored-By trailer.

## Workflow

1. **Check git status**: Run `git status -sb` to see all changed files. If no changes exist, report "Nothing to commit" and exit.

2. **Analyze changes**: Run `git diff --stat` and `git diff` (staged + unstaged) to understand what changed:
   - Identify affected modules (tools/, state/, models/, tests/, skills/, docs/)
   - Determine change type: `feat` (new feature), `fix` (bug fix), `refactor` (restructure), `docs` (documentation), `chore` (maintenance), `test` (tests only)
   - Determine scope from the primary module affected

3. **Check recent commits**: Run `git log --oneline -5` to match the repository's commit style.

4. **Find PRD context**: Search changed files for PRD references (grep for `PRD-` in modified files and in any active sprint docs). If a PRD is being implemented, include it in the commit.

5. **Generate commit message**: Following the format:
   ```
   type(scope): concise description of WHAT changed

   WHY: rationale for the change
   PRD: PRD-XXX-NNN (if applicable)

   Co-Authored-By: Claude <noreply@anthropic.com>
   ```

   - If `$ARGUMENTS` contains a message hint, use it to inform the description
   - Keep the first line under 72 characters
   - The WHY line explains the motivation, not the mechanics

6. **Stage files**: Run `git add` for the relevant files. Exclude:
   - `.env`, credentials, secrets
   - Large binary files
   - `.trw/logs/` debug logs
   - Run artifacts (`docs/*/runs/`)

7. **Confirm with user**: Show the proposed commit message and staged files. Ask for confirmation before committing.

8. **Execute commit**: Run `git commit` with the message via HEREDOC format.

9. **Report**: Show commit hash, files committed, and branch name.

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

## Constraints

- NEVER commit files that contain secrets (.env, credentials, API keys)
- NEVER use `git add -A` or `git add .` — always stage specific files
- NEVER amend previous commits unless explicitly asked
- NEVER skip pre-commit hooks (no --no-verify)
- ALWAYS confirm the commit message with the user before executing
- ALWAYS use HEREDOC format for multi-line commit messages
