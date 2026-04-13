# /trw-audit

Run adversarial spec-vs-code audit on a PRD or implementation.

## When to use

- After completing implementation to verify all FRs are satisfied.
- When a PRD has been implemented but tests feel thin.
- Before merging a feature branch to catch gaps early.
- When a reviewer has flagged concerns that need systematic investigation.

## What it does

Invokes the TRW `trw-audit` skill via the MCP server:

1. Reads the target PRD and its FR list.
2. For each FR, searches the implementation for evidence of fulfillment.
3. Flags unimplemented FRs, stub code, and missing tests.
4. Produces a finding report with P0/P1/P2 severity levels.
5. Suggests specific remediation steps.

## Usage

Type `/trw-audit` followed by the PRD ID or implementation path, e.g.:

```
/trw-audit PRD-CORE-136
/trw-audit src/trw_mcp/bootstrap/_cursor_ide.py
```

If no argument is given, audits the most recently modified implementation
relative to the active run.
