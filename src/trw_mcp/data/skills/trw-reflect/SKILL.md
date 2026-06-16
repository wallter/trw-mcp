---
name: trw-reflect
description: >
  End-of-session reflection: examine the session's actual signals (events, diffs,
  build results, corrections), identify process/organization/tooling/agent
  improvement opportunities, get approval, then route each one to implementation
  (inline quick-fix, PRD pipeline, learning, or backlog) with a follow-through
  ledger. Run BEFORE /trw-deliver. Use: /trw-reflect [quick|standard|deep]
user-invocable: true
argument-hint: "[quick|standard|deep|action]"
---

# Session Reflection Skill (PRD-CORE-187)

Use when: a work session is winding down and you want its process lessons —
not just its code — to compound into the next session. This skill finds what
slowed the session down or what's missing (commands, skills, agents, indexes,
docs, conventions) and drives accepted fixes to implementation instead of
leaving a report behind.

Run this BEFORE `trw_deliver` so accepted learnings ride the delivery ceremony.

## Depth

`$ARGUMENTS` selects depth (default `standard`):

| Depth | Signals | Opportunity cap | Sub-agents |
|---|---|---|---|
| quick | in-context review + git diff + prior ledger | 5 | 0 |
| standard | + events.jsonl, build records | 10 | ≤ 2 (Explore) |
| deep | + tool-usage friction sweep, docs/index audit | 15 | ≤ 5 |

**Action mode** (`/trw-reflect action`): no new reflection. Load the open
`recorded-only` / `deferred` rows from prior ledgers (Step 0 discovery), run
them through Steps 3-4 (approval + routing), and append a ledger entry whose
Opportunities table records the disposition of each drained item. Use this to
pay down accumulated ledger-only debt.

## Step 0: Recurrence check (mandatory first)

List `.trw/reflections/*.md` ledger entries — ignore `*-L-*.yaml` files there
(those are delivery-ceremony reflect shards owned by `_do_reflect`, not
ledgers). Order by file MODIFICATION time, not filename date — filenames carry
the session's date, and concurrent instances write same-day entries. Name the
ledger files you actually found in your header; claim "first reflection" only
when zero `.md` ledgers exist.
Read the 1-3 most recently modified ledger entries and check each previously
routed item (treat `recorded-only` rows as unimplemented):

- **Completed** (PRD implemented, quick-fix shipped, learning present via `trw_recall`) → note as closed.
- **Routed but not implemented, AND the same friction appears again this session** → flag
  "recurred — escalated": raise its impact one level (L→M→H) in this session's table, citing the prior ledger file by path.
- **Routed, not implemented, no recurrence** → carry as "deferred, no new signal".

Never claim recurrence without citing the prior ledger entry (NFR04).

Tally the open `recorded-only` + `deferred` items across prior ledgers and
include the count in your Step 3 presentation (offer them for routing
alongside new opportunities, or suggest `/trw-reflect action`). A ledger that
only ever accumulates recorded-only rows has not closed the follow-through
loop — say so plainly when the debt grows.

## Step 1: Collect signals (external evidence only)

Gather what exists; skip gracefully what doesn't (e.g., no active run → skip
events; rely on git + in-context evidence):

1. **Run events** (standard/deep): read the active run's `events.jsonl` (path from `trw_status`).
   Look for: error events, retries, phase regressions, long gaps between
   checkpoints, repeated near-identical operations.
2. **Code reality**: `git diff` + `git log` for the session — what actually
   changed vs. what was planned or promised.
3. **Validation outcomes** (standard/deep): build_check results (pass/fail transitions; a
   fail→pass transition is a verified lesson; a never-run check is a process gap).
4. **Learnings recorded**: what this session already saved via `trw_learn`.
5. **User corrections**: in-context redirections, rejected approaches, repeated
   clarifications — each is a high-value friction signal.
6. **Tool friction** (standard/deep): denied permission calls, dead-end
   searches, tools/skills/agents that were needed but missing, oversized
   reads that went unused.

At `standard`+ depth, you MAY delegate (2) and (6) to an Explore sub-agent;
keep the conclusions, not the dumps.

**Grounding rule (anti-drift)**: every finding MUST cite at least one concrete
signal from the list above (file, event, diff hunk, quoted correction). A
finding you cannot ground is at most a "hypothesis" in the report appendix —
never an opportunity row. Do not re-litigate the task itself; reflect on the
*process* that executed it.

## Step 2: Synthesize opportunities

Convert grounded findings into improvement opportunities. Each row MUST have:

| Field | Contract |
|---|---|
| Category | exactly one of: `process/ceremony`, `organization/indexing`, `tooling/commands`, `agents/delegation`, `memory/learnings`, `docs` |
| Evidence | the signal citation(s) from Step 1 |
| Impact | H / M / L (recurrence-escalated per Step 0) |
| Effort | H / M / L |
| Proposed route | quick-fix / PRD / learning / backlog (see Step 4) |

Then **dedup** before presenting:

- `trw_recall(query=<topic>)` — if an existing learning already covers it, the
  opportunity becomes recurrence evidence on that learning, not a new row.
- Grep `docs/documentation/improvement-backlog.md` (create it with a one-line
  header if missing) — matches move to a "recurring" section with a pointer to
  the existing entry.

Respect the depth cap by dropping the rows with the lowest impact-to-effort
ratio (score H=3/M=2/L=1; rank by impact ÷ effort). State that rows were
dropped; never silently truncate.

## Step 3: Approval gate

Present the opportunity table (new + recurring + hypotheses appendix) and get
explicit approval per row before ANY persistent write other than `trw_learn`:

- Interactive session: use AskUserQuestion (multi-select) or an equivalent
  in-chat approval listing each row with its route.
- Autonomous session (no user available): auto-approve ONLY reversible routes —
  `learning` and `backlog`. Quick-fixes are auto-approvable only when covered by
  an existing standing authorization; PRD creation for structural changes is
  auto-approvable, but its *implementation* still walks the full phase gates.
  Record "auto-approved (autonomous)" in the ledger for each such row.

Rejected rows are recorded in the ledger with the rejection reason — they are
signal for future reflections, not noise.

**Ledger-only mode**: when the operator directs "document, don't implement"
(or the invocation arguments contain `ledger-only`), skip ALL routing
execution — the `trw_learn` pre-approval exemption is suspended too. Record
every row with Status `recorded-only`, citing the directive once in the
header. The ledger IS the deliverable; the next reflection treats
`recorded-only` rows as unimplemented when checking recurrence.

## Step 4: Route and implement

Route each approved opportunity to exactly one channel:

1. **quick-fix** — small, reversible, ≤ ~15 min (typo-class doc fix, missing
   index entry, config default). First check the target isn't actively owned
   by a concurrent instance (recent foreign commits/edits on the same
   surface); if contested, route to backlog with a hand-off note instead.
   Otherwise implement NOW, inline, and validate with the narrowest
   project-native check.
2. **PRD** — anything structural (new tool/skill/agent, schema change,
   cross-package behavior). Invoke the PRD pipeline (`/trw-prd-new "<title>"`,
   or draft per `docs/requirements-aare-f/CLAUDE.md` including the mandatory
   search-scope greps). The change then follows
   RESEARCH→PLAN→IMPLEMENT→VALIDATE→REVIEW→DELIVER — never implement
   structural changes ad hoc from a reflection.
3. **learning** — durable gotcha/pattern: `trw_learn(summary, detail, tags,
   impact)` with the evidence citation in the detail.
4. **backlog** — valuable but not now: append to
   `docs/documentation/improvement-backlog.md` (create with a one-line header
   if missing) with date + evidence pointer.

## Step 5: Write the ledger

Append (never overwrite) `.trw/reflections/YYYY-MM-DD-<session-slug>.md`,
where `<session-slug>` is the active run's task name, or a 2-4 word
kebab-case session summary when there is no run:

```markdown
# Reflection — <date> — <one-line session description>
Reflected-at: <ISO timestamp> | Depth: <quick|standard|deep>
Prior ledgers checked: <filenames found, or "first reflection">
<if ledger-only mode: one line citing the operator directive>

## Signals examined
<one line per signal source actually read, with paths>

## Opportunities
| # | Category | Evidence | Impact | Effort | Route | Approval | Status |
<Status enum: shipped | recorded-only | deferred | rejected — plus a pointer
(commit, learning ID, backlog line, PRD ID) when shipped>

## Recurring (deduped)
<pointers to existing learnings/backlog entries that matched; recurred-escalated
items appear BOTH here (with the prior-ledger pointer) and in the Opportunities
table above (with the escalated impact)>

## Rejected / Deferred
<row + reason>

## Hypotheses (ungrounded — not actioned)

## Next reflection — verify
<one line per routed item: what closes it, and what recurrence escalates>
```

Then report to the user: opportunities found/approved/routed, quick-fixes
shipped (with validation evidence), PRDs created, and a pointer to the ledger's
"Next reflection — verify" section.

## Guardrails

- **Canon documents are off-limits**: a reflection run MUST NOT modify the core
  framework or its canon — `FRAMEWORK.md`, `AARE-F-FRAMEWORK.md`, `VISION.md`,
  `CONSTITUTION.md`, or framework docs. If a reflection surfaces a canon-level
  improvement, document it as a *proposal* (backlog or PRD with
  `status: draft`) and route it for explicit operator approval; never edit
  those artifacts from an individual run.
- **Complement, don't duplicate `trw_deliver`**: mechanical error/repeated-op
  learning extraction happens at delivery (`_do_reflect`). This skill owns
  *process and capability* improvements; leave routine error-learning capture
  to the delivery ceremony.
- **No instruction-file bloat**: never edit CLAUDE.md/AGENTS.md inline from a
  reflection without explicit approval; durable guidance routes to learnings
  or skills (on-demand context), not always-loaded files.
- **No fabricated metrics**: report counts only for things you actually
  measured this session; distinguish verified signals from hypotheses.
- **Append-only ledger**: writes go under `.trw/reflections/` or explicitly
  approved targets; nothing destructive.
- **Truthful routing**: a routed item is not a completed item — never report
  PRD-routed work as "done"; Step 0 of the next reflection holds the score.
