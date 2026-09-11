---
name: trw-prd-ready
description: Turn a feature description or existing PRD into reviewed requirements and an execution plan using the installed shared readiness contract.
user-invocable: true
argument-hint: "[feature description or PRD-ID]"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, mcp__trw__trw_recall, mcp__trw__trw_prd_create, mcp__trw__trw_prd_validate, mcp__trw__trw_learn
---

# OpenCode PRD Ready

Read [the readiness owner](trw-prd-ready-contract.md) beside this skill and
follow it with the original `$ARGUMENTS`, including any explicit `--embedded-plan`
option. Do not create first, substitute a PRD ID, or run a second pipeline.

Internal phases named by that owner resolve to these installed contracts:
- `trw-prd-groom`: [grooming](trw-prd-groom-contract.md).
- `trw-prd-review`: [review](trw-prd-review-contract.md).
- `trw-exec-plan`: [planning](trw-exec-plan-contract.md).

Read the selected phase contract when needed and apply it inline if the host
cannot invoke internal skills directly. These are supporting files, not extra
public commands. Use available host tools; do not assume subagents exist.
Preserve the selected owner's independence requirements: inline execution does
not authorize author self-review where independent review is required.
If a required contract is missing, stop and report the missing installed path;
do not invent a fallback pipeline. Higher-priority project/operator artifact and
storage rules remain authoritative.
