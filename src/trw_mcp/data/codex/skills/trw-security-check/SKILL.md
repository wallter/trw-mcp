---
name: trw-security-check
context: fork
agent: Explore
description: >
  OWASP-focused, language-aware security audit of the TRW codebase. Checks
  command injection, unsafe deserialization, path traversal, secrets,
  dependency risk, and input validation. Read-only.
  Use: /trw-security-check [module or 'all']
user-invocable: true
argument-hint: "[module or 'all']"
---

> Codex adaptation: `AGENTS.md` is the primary instruction file. If a step mentions legacy Claude-specific workflow, follow the equivalent Codex skill/subagent flow instead.

# Security Audit Skill

Use when: running a read-only OWASP-style security review for a TRW package or module.

Perform an OWASP-focused security audit of the selected TRW package or module. This skill is read-only — it identifies vulnerabilities but does not modify code. Infer language and framework from the target path before choosing checks.

## Workflow

1. **Determine scope**: Parse `$ARGUMENTS`:
   - If a specific module path, focus on that module
   - If `all` or empty, audit the full codebase (discover source directories via Glob)

2. **Command Injection (OWASP A03)**: Search for unsafe subprocess usage:
   - Grep for process execution APIs in the target language (`subprocess`, `os.system`, Node `child_process`, Go `os/exec`, Rust `Command`, shell eval/backticks, etc.)
   - Check if shell invocation is used with user input (for example `shell=True`, `exec`, `sh -c`)
   - Verify arguments are passed as structured arrays/lists where supported, not interpolated strings
   - Check build/test wrappers specifically because they often run user-adjacent commands

3. **Deserialization Safety (OWASP A08)**: Check YAML handling:
   - Grep for unsafe YAML/XML/JSON/object deserialization APIs in the target language
   - Verify YAML loaders use safe modes (e.g., `yaml.safe_load`, `SafeLoader`, safe ruamel usage)
   - Check for `pickle`, `marshal`, `eval()`, dynamic import/require, template execution, or equivalent unsafe eval paths
   - Check JSON parsing hooks/revivers/object hooks for injection vectors

4. **Path Traversal (OWASP A01)**: Check file operations:
   - Grep for `open()`, `Path()`, file read/write operations
   - Check if user-provided paths are validated against a base directory
   - Verify the target package's path-resolution helpers prevent directory escape (for trw-mcp this includes `state/_paths.py`)
   - Look for `..` traversal in path construction

5. **Secrets Exposure (OWASP A02)**: Check for hardcoded credentials:
   - Grep for patterns: `password`, `secret`, `api_key`, `token`, `credential`
   - Check `.env` files are gitignored
   - Verify no API keys in source code or test fixtures
   - Check structured logging/telemetry calls don't log sensitive data

6. **Input Validation (OWASP A03)**: Check system boundaries:
   - MCP/API/CLI parameter validation (Pydantic, Zod, JSON Schema, typed DTOs, Clap/Click validators, etc.)
   - Check for unvalidated string interpolation in file paths
   - Verify PRD file path resolution validates input
   - Check that `trw_prd_create` input_text is sanitized

7. **Dependency Security (OWASP A06)**: Check dependencies:
   - Read dependency manifests for the target package (`pyproject.toml`, `package.json`, lockfiles, `Cargo.toml`, `go.mod`, etc.)
   - Flag any dependencies without version pins
   - Note if a language-appropriate dependency audit is configured (`pip-audit`, `safety`, `npm audit`, `pnpm audit`, `cargo audit`, `govulncheck`, etc.)

8. **Report**: Structured security report:
   ```
   ## Security Audit Report

   ### Summary
   - Scope: {files audited}
   - Critical: {count}
   - Warning: {count}
   - Info: {count}

   ### Findings

   #### [CRITICAL/WARNING/INFO] {Finding Title}
   - **Category**: {OWASP category}
   - **File**: {file:line}
   - **Description**: {what was found}
   - **Recommendation**: {how to fix}

   ### Positive Patterns
   - {Good security practices observed}

   ### Recommendations
   1. {highest priority fix}
   2. {next priority}
   ```

## Severity Classification

| Severity | Criteria |
|----------|----------|
| **Critical** | Exploitable vulnerability with user-controlled input |
| **Warning** | Unsafe pattern that could become exploitable |
| **Info** | Best practice deviation, no immediate risk |

## Notes

- This skill is read-only — it audits but does not fix issues
- Focus on source code, not documentation or configs
- Treat framework validators and schemas as helpful but still verify boundary coverage and coercion behavior
- Adjust network/auth priority to the target package: local CLI tools, MCP servers, web apps, and public services have different threat models
