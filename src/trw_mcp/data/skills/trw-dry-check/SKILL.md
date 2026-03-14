---
name: trw-dry-check
model: claude-sonnet-4-6
description: >
  Scan files for duplicated code blocks and suggest extraction.
  Read-only analysis — reports findings without modifying code.
  Use: /trw-dry-check [file-patterns...]
user-invocable: true
context: fork
agent: Explore
argument-hint: "[file-patterns...]"
allowed-tools: Read, Glob, Grep
---

# /trw-dry-check

Scan files for duplicated code blocks that violate DRY principles.

## Usage

```
/trw-dry-check [file-patterns...]
```

## Workflow

1. **Identify files**: If patterns provided, use Glob to find matching files. Otherwise, use `git diff --name-only HEAD` to find changed files.

2. **Read the dry_check module**: Read `trw-mcp/src/trw_mcp/state/dry_check.py` to understand the detection logic.

3. **Run detection**: Execute the following in bash:
   ```bash
   cd /path/to/project && .venv/bin/python -c "
   from trw_mcp.state.dry_check import find_duplicated_blocks, format_dry_report
   import sys, glob

   files = glob.glob('PATTERN_HERE')  # Replace with actual patterns
   blocks = find_duplicated_blocks(files, min_block_size=5)
   print(format_dry_report(blocks))
   "
   ```

4. **Report findings**: For each duplicated block, suggest:
   - Which file should own the shared helper
   - A function signature for the extracted helper
   - Which locations should call the new helper

5. **Summarize**: Report total blocks found, total files affected, and suggested extractions.
