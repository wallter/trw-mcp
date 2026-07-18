---
name: trw-dry-check
description: >
  Scan files for duplicated code blocks and classify consolidation candidates.
  Read-only analysis — reports evidence without modifying code.
  Use: /trw-dry-check [file-patterns...]
user-invocable: true
context: fork
agent: Explore
argument-hint: "[file-patterns...]"
---

# /trw-dry-check

Use when: a change may have introduced duplicated logic, before consolidation work, or when a DRY audit of specific files is requested.

Analyze duplicate candidates without treating scanner matches as DRY violations.

## Usage

```
/trw-dry-check [file-patterns...]
```

## Workflow

1. **Scope files**: Resolve supplied patterns with project-native search. Otherwise inspect files changed from `HEAD`.
   Stay read-only and do not disturb unrelated work in a shared or dirty workspace.

2. **Detect candidates**: Compare relevant blocks and label the method used: exact, near-duplicate, or structural.
   Cite files/ranges and confidence. A similarity score or scanner match is evidence, not a verdict.

3. **Trace context**: Inspect surrounding ownership, callers, contracts, tests, generation/sync paths, and deployment
   boundaries. Required client projections, schema/default mirrors, standalone artifacts, fixtures, and intentionally
   separate safety or lifecycle code are not extraction targets merely because they repeat.

4. **Classify each candidate**:
   - **consolidate** only when maintained first-party copies implement the same behavior, one owner is appropriate, and
     extraction reduces net complexity without weakening boundaries;
   - **retain** when repetition is intentional or required; or
   - **uncertain** when reachability, ownership, or contract evidence is incomplete.

5. **Report**: Give candidate/file counts, classification, and evidence. Suggest an owner, helper signature, and call
   sites only for **consolidate** findings. A justified no-change result is valid.
