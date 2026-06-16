---
name: trw-reflect
description: End-of-session reflection — find process/tooling improvements and route them to implementation
---

Run the TRW end-of-session reflection workflow (arguments:
quick | standard | deep | action; default standard). Run BEFORE /trw-deliver.
`action` skips new reflection and routes open recorded-only/deferred items
from prior ledgers instead.

Workflow:
1. Follow the `trw-reflect` skill in `.opencode/skills/trw-reflect/` end to end.
2. Check `.trw/reflections/*.md` for prior ledger entries; verify previously
   routed items and escalate recurrences.
3. Collect external signals only (run events, git diff/log, build records,
   user corrections, tool friction) — every finding must cite a signal.
4. Synthesize improvement opportunities (category, evidence, impact, effort,
   route), dedup against learnings and docs/documentation/improvement-backlog.md.
5. Present the table and get per-row approval before any persistent write.
6. Route approved items: quick-fix inline, structural changes via the PRD
   pipeline, gotchas via trw_learn, deferred items to the backlog.
7. Append the ledger entry under .trw/reflections/ and report what the next
   reflection should verify.

Constraints:
- Never modify canon documents (FRAMEWORK.md, AARE-F-FRAMEWORK.md, VISION.md,
  CONSTITUTION.md) — document proposals for operator approval instead.
- Keep the workflow OpenCode-safe: no client-specific tools or sub-agent
  assumptions; collect signals inline if helpers are unavailable.
