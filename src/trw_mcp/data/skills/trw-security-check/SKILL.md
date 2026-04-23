---
name: trw-security-check
context: fork
agent: Explore
description: >
  OWASP-focused security audit of the trw-mcp codebase. Checks for
  command injection, YAML deserialization, path traversal, secrets,
  and input validation. Read-only.
  Use: /trw-security-check [module or 'all']
user-invocable: true
argument-hint: "[module or 'all']"
---

# Security Audit Skill

Use when: you want an OWASP-focused read-only audit of trw-mcp (command injection, YAML, path traversal, secrets).

Perform an OWASP-focused security audit of the trw-mcp codebase. This skill is read-only — it identifies vulnerabilities but does not modify code.

## Workflow

1. **Determine scope**: Parse `$ARGUMENTS`:
   - If a specific module path, focus on that module
   - If `all` or empty, audit the full codebase (discover source directories via Glob)

2. **Command Injection (OWASP A03)**: Search for unsafe subprocess usage:
   - Grep for `subprocess.run`, `subprocess.Popen`, `os.system`, `os.popen`
   - Check if `shell=True` is used (dangerous with user input)
   - Verify arguments are passed as lists, not interpolated strings
   - Check `tools/build.py` specifically (runs pytest/mypy via subprocess)

3. **Deserialization Safety (OWASP A08)**: Check YAML handling:
   - Grep for `yaml.load` without `Loader=SafeLoader` or `yaml.safe_load`
   - Verify ruamel.yaml usage patterns are safe (ruamel defaults to safe)
   - Check for pickle, marshal, or eval() usage
   - Check JSON parsing for `object_hook` injection vectors

4. **Path Traversal (OWASP A01)**: Check file operations:
   - Grep for `open()`, `Path()`, file read/write operations
   - Check if user-provided paths are validated against a base directory
   - Verify `state/_paths.py` resolve functions prevent directory escape
   - Look for `..` traversal in path construction

5. **Secrets Exposure (OWASP A02)**: Check for hardcoded credentials:
   - Grep for patterns: `password`, `secret`, `api_key`, `token`, `credential`
   - Check `.env` files are gitignored
   - Verify no API keys in source code or test fixtures
   - Check structlog calls don't log sensitive data

6. **Input Validation (OWASP A03)**: Check system boundaries:
   - MCP tool parameter validation (Pydantic models handle this)
   - Check for unvalidated string interpolation in file paths
   - Verify PRD file path resolution validates input
   - Check that `trw_prd_create` input_text is sanitized

7. **Dependency Security (OWASP A06)**: Check dependencies:
   - Read `pyproject.toml` for dependency versions
   - Flag any dependencies without version pins
   - Note if `pip-audit` or `safety` checks are configured

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
- Pydantic v2 models provide built-in input validation for MCP tool parameters
- The codebase runs locally (no network-facing service), so network-based attacks are lower priority
