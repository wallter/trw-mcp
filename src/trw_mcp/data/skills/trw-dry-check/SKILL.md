---
name: trw-dry-check
description: >
  Scan files for duplicated code blocks and suggest extraction.
  Read-only analysis — reports findings without modifying code.
  Use: /trw-dry-check [file-patterns...]
user-invocable: true
context: fork
agent: Explore
argument-hint: "[file-patterns...]"
---

# /trw-dry-check

Use when: you want a read-only scan for duplicated code blocks and extraction candidates.

Scan files for duplicated code blocks that violate DRY principles.

## Usage

```
/trw-dry-check [file-patterns...]
```

## Workflow

1. **Identify files**: If patterns provided, use Glob to find matching files. Otherwise, use `git diff --name-only HEAD` to find changed files.

2. **Read each file**: Read all matched files and compare blocks of 5+ consecutive lines for similarity.

3. **Detect duplicates**: For each pair of files, identify:
   - Exact duplicate blocks (5+ lines identical after whitespace normalization)
   - Near-duplicate blocks (>80% similar after identifier normalization)
   - Repeated structural patterns (same shape, different names)

4. **Report findings**: For each duplicated block, suggest:
   - Which file should own the shared helper
   - A function signature for the extracted helper
   - Which locations should call the new helper

5. **Summarize**: Report total blocks found, total files affected, and suggested extractions.
