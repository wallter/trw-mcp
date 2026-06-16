# /trw-reflect

Run the end-of-session reflection: turn this session's friction into routed
improvements (quick-fixes, PRDs, learnings, backlog) with a follow-through
ledger.

## When to use

- A work session is winding down and you want its process lessons captured.
- Before `/trw-deliver`, so accepted learnings ride the delivery ceremony.
- After a session with notable friction: rejected approaches, repeated
  corrections, missing tools/commands, dead-end searches.

## What it does

Invokes the TRW `trw-reflect` skill (see `.cursor/skills/trw-reflect/`):

1. Checks `.trw/reflections/*.md` for prior ledger entries and escalates
   previously routed items that recurred unimplemented.
2. Collects external signals (run events, git diff/log, build records, user
   corrections, tool friction) — findings without a signal citation are
   demoted to hypotheses.
3. Synthesizes improvement opportunities (category, evidence, impact, effort,
   route), deduped against existing learnings and the improvement backlog.
4. Gates persistent writes behind per-row approval, then routes each accepted
   item: inline quick-fix, PRD pipeline, `trw_learn`, or backlog.
5. Appends a follow-through ledger entry under `.trw/reflections/`.

Canon documents (FRAMEWORK / AARE-F / VISION / CONSTITUTION) are never
modified by a reflection run.

## Usage

Type `/trw-reflect` optionally followed by a depth, e.g.:

```
/trw-reflect
/trw-reflect quick
/trw-reflect deep
/trw-reflect action
```

Depth defaults to `standard` (caps: quick 5 opportunities / standard 10 /
deep 15). `action` runs no new reflection — it loads open
recorded-only/deferred items from prior ledgers and routes them through
approval + implementation, paying down ledger debt.
