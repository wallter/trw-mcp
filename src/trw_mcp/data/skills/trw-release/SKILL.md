---
name: trw-release
model: claude-opus-4-6
description: >
  Release a trw-* package: version bump, changelog, commit, tag, push,
  subtree sync to public repo, PyPI publish. Handles the full 7-step
  release workflow in one command.
  Use: /trw-release trw-mcp 0.32.0 "Executable assertions"
user-invocable: true
argument-hint: "<package> <version> [summary]"
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---
<!-- ultrathink -->

# Package Release Skill

Orchestrate the full release lifecycle for a trw-* package in one command.

## Arguments

Parse `$ARGUMENTS` as: `<package> <version> [summary]`

- **package**: `trw-mcp` or `trw-memory` (required)
- **version**: semver like `0.32.0` or `1.0.0` (required)
- **summary**: one-line release summary (optional, derived from git log if omitted)

Examples:
```
/trw-release trw-mcp 0.32.0 "Executable assertions integration"
/trw-release trw-memory 0.4.0
```

## Package Registry

| Package | pyproject.toml | CHANGELOG.md | Public Remote | Subtree Prefix |
|---------|---------------|--------------|---------------|----------------|
| `trw-mcp` | `trw-mcp/pyproject.toml` | `trw-mcp/CHANGELOG.md` | `trw-mcp-public` | `trw-mcp` |
| `trw-memory` | `trw-memory/pyproject.toml` | `trw-memory/CHANGELOG.md` | `trw-memory-public` | `trw-memory` |

## Pre-flight Checks

Before any changes, verify:

1. **Clean working tree for target package**: `git diff --name-only <package>/` should be empty (uncommitted changes block release)
2. **On main branch**: `git branch --show-current` must be `main`
3. **Version is newer**: Compare `$version` against current version in pyproject.toml. Reject if not a semver bump.
4. **Tests pass**: Run `cd <package> && .venv/bin/python -m pytest tests/ -x -q --tb=line 2>&1 | tail -5` and confirm no failures (pre-existing known failures are OK — list them explicitly)
5. **No other active releases**: Check `git tag --list "v${version}-*"` — reject if tag already exists

If any check fails, report the issue and STOP. Do not proceed with a broken release.

## Release Steps

### Step 1: Generate Changelog Entry

Read `<package>/CHANGELOG.md` to find the current top version. Then:

1. Get commits since last tag: `git log --oneline $(git describe --tags --abbrev=0 --match "v*-<suffix>")..HEAD -- <package>/`
2. Group commits by type (feat, fix, docs, refactor, etc.)
3. Write a new `## [<version>] — <date>` section at the top of the changelog

Format:
```markdown
## [<version>] — YYYY-MM-DD

### Added
- **Feature name** — description (PRD-ID if applicable)

### Fixed
- **Bug name** — description

### Changed
- **Change name** — description
```

If the user provided a `summary`, use it as the primary changelog entry. Otherwise derive from git log.

### Step 2: Bump Version

Edit `<package>/pyproject.toml`: change the `version = "..."` line to the new version.

Check for other version references that may need updating:
- `.trw/frameworks/VERSION.yaml` — only if it references this package's version
- `CLAUDE.md` version table — mentions versions for reference but says "Do NOT hardcode" so leave it

### Step 3: Commit

```bash
git add <package>/pyproject.toml <package>/CHANGELOG.md
git commit -m "release(<package>): v<version> — <summary>"
```

Use the conventional commit format. Include `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>`.

### Step 4: Push to Main

```bash
git push origin main
```

### Step 5: Tag

```bash
git tag v<version>-<suffix> -m "<package> v<version> — <summary>"
git push origin v<version>-<suffix>
```

Tag suffix mapping:
- `trw-mcp` → suffix `mcp` → tag `v0.32.0-mcp`
- `trw-memory` → suffix `memory` → tag `v0.4.0-memory`

### Step 6: Subtree Sync to Public Repo

```bash
git subtree split --prefix=<package> -b <split-branch>
git push <public-remote> <split-branch>:main --force
git branch -D <split-branch>
```

Split branch naming:
- `trw-mcp` → `mcp-split`
- `trw-memory` → `mem-split`

This step is slow (~30s for 585 commits). That's normal.

### Step 7: PyPI Publish (trw-mcp only)

For `trw-mcp`, the publish also uploads the installer bundle:

```bash
TRW_API_KEY=<key> make publish
```

The API key comes from `.trw/config.yaml` field `platform_api_key`, or the user can provide it. If no key is available, skip this step and tell the user to run `TRW_API_KEY=<key> make publish` manually.

For `trw-memory`, publish via:
```bash
cd trw-memory && python -m build && twine upload dist/*
```

If twine/build aren't available, skip and tell the user.

### Step 8: Post-Release Verification

```bash
make amplify-status
```

Report the Amplify build status. If the build is failing, warn the user immediately.

Also verify the package is installable:
```bash
pip install --dry-run --no-deps <package>==<version>
```

## Error Handling

- If `git push` fails (e.g., behind remote): `git pull --rebase origin main` and retry once
- If subtree split fails: warn but continue — the monorepo push already succeeded
- If PyPI publish returns 409: the version already exists on PyPI — warn but don't fail (S3 artifacts still update)
- If Amplify status shows failure: warn prominently but don't block — the release is already published

## Multi-Package Release

If releasing both packages (e.g., after a cross-cutting feature), release the **dependency first**:

1. Release `trw-memory` first (it has no in-repo dependencies)
2. Then release `trw-mcp` (it depends on `trw-memory`)

This ensures PyPI has the correct dependency available.

## Output

Report a summary table:

```
## Release: <package> v<version>

| Step | Status |
|------|--------|
| Changelog | Updated |
| Version bump | 0.31.1 → 0.32.0 |
| Commit | abc1234 |
| Push | origin/main |
| Tag | v0.32.0-mcp |
| Public repo | github.com/wallter/<package> |
| PyPI | Published / Skipped |
| Amplify | Building / Passed / Failed |
```
