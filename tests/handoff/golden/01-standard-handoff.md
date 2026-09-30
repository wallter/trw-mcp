# Handoff 01J9ZK5W1STD000000000001 — swap\-dirty\-worktree   (tier: standard; Agent\-to\-agent\; uncommitted lane edits in a shared worktree)

Rendered view of 01J9ZK5W1STD000000000001 sha256\:ddcd8aa819205a8baec80e246d0f47bfd0997de14feeda78f1545eac6fad1d45; the JSON record is normative.  
from worker\-w1 [agent, balanced, s\-7f3a] → integrator [agent]; completer none  
created 2026\-09\-28T21\:10\:00Z · state as of 2026\-09\-28T21\:09\:30Z @ 47c004499 (dirty; changed: file\:changed\-paths\.txt) · run run\:hotreload\-w1 · expires 2026\-09\-29T21\:10\:00Z · digest sha256\:ddcd8aa819205a8baec80e246d0f47bfd0997de14feeda78f1545eac6fad1d45 · signature —  
Supersedes: none  
Status: lifecycle state unknown (rendered from a file, not a store)

## Objective
- Goal: Make the hot\-reload swap refuse when the target worktree has uncommitted changes\.
- Intent: No operator work may be lost\; a refused swap is acceptable\, a silent overwrite is not\. Message wording may change without asking\.
- Done when: A dirty target produces a refusal and git status is unchanged
- Done when: The full package suite is recorded as passing

## Constraints
- Do not touch the launcher config\.
- Edit only the three files owned by lane W1\.

## Risks
- [high] (r1) Refusal may be raised after the source is partly staged — Check ordering in swap\.py lines 120\-160 first\.
- [low] (r2) Refusal message may confuse operators

## First action
- (a1) Run the full package suite on the branch and record the result — owner integrator; done when Recorded pass with non\-zero test count and scope \'full\'; depends on c1

## Read first
1. file\:W1\-hotreload\.md (file) — Lane brief and ownership
2. file\:swap\.py (file) — Ordering risk r1 at lines 120\-160 [sha256\:9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f9f]

## State
| id | label | claim | basis or evidence |
|---|---|---|---|
| c1 | observed | Refusal path for a dirty worktree is implemented in swap\.py | Read the full diff of swap\.py at 21\:05Z\; not executed\. |
| c2 | verified | Unit tests for the refusal path pass | pytest tests\/test\_swap\.py \-q; one test file\, not the package suite; supports; 2026\-09\-28T21\:08\:00Z; worker\-w1; file\:pytest\-w1\.txt |
| c3 | inferred | Behaviour with a locked index file is unchanged | The code path was not touched and was not exercised\. |
| c4 | unknown | Windows path handling after a refusal | — |

## Not done
- Full package suite has not been run
- No live swap rehearsal

## Unknowns
- Whether the MCP proxy caches the old module path after a refused swap

## Contingencies
- If The rehearsal shows a partial stage then Revert only lane\-owned staged paths and message the lead\; never reset the worktree\.

## Decisions
- Refuse rather than stash — Stash is forbidden in the shared tree and can discard operator edits\. (rejected: auto\-stash, force flag)

## Remaining actions
- (a2) Rehearse a swap against a scratch worktree with one dirty file — owner integrator; done when Refusal observed and git status unchanged; depends on c2

## Read-back requested
required yes · reverify c2

## Extensions
- https\:\/\/example\.org\/ahr\-ext\/lane\/1: \{\"lane\"\: \"W1\"\}
