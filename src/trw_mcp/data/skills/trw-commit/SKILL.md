---
name: trw-commit
description: >
  Concurrency-safe conventional commit with scoped staging, cached-diff review,
  repository-native branch policy, and optional PRD/run provenance. Use only
  when the user asks to commit current work.
user-invocable: true
disable-model-invocation: true
argument-hint: "[optional message hint]"
---

# TRW Commit

Use when: the user explicitly asks to commit the current owned change set.

Create one intentional commit without taking ownership of unrelated workspace
changes. Never amend, reset, stash, switch branches, bypass hooks, or broadly
stage the repository unless the user explicitly authorizes that separate action.

## Required pre-edit ownership preparation

The candidate workflow is fail-closed: the owning run must claim exact paths **before**
editing. At task start, after `trw_session_start` resolves the pinned run, execute:

```bash
trw-mcp prepare-candidate --path <owned-path> [--path ...] \
  --run-dir <active-run-dir> [--transaction-id <id>]
```

Keep the returned transaction ID. If work already changed a path without this prepared
claim, `trw-commit` must not manufacture ownership from the current bytes; coordinate a
quiesced native commit or restart the edit from a verified baseline.

## Workflow

1. **Snapshot the workspace.** Run `git status --short`, `git status -sb`, and
   `git diff --cached --name-only`. Record files already staged before this
   skill began. If ownership is unclear, stop rather than absorbing another
   worker's changes.
2. **Honor repository policy.** Read project instructions and recent commit
   history. Check the current branch against an actual configured/repository
   policy when one exists. Do not invent or enforce a TRW-global branch regex.
3. **Resolve run context safely.** Use only the active run returned for this MCP session by
   `trw_session_start`/`trw_status`, then read
   `{RUN_ROOT}/meta/run.yaml` if needed. Never scan for an arbitrary active run
   or infer this commit's provenance from global context files. If the pinned
   run does not cover these paths, report run/PRD metadata as `UNKNOWN` or omit
   optional trailers.
4. **Understand the owned diff.** Inspect unstaged and staged diffs for the
   exact owned paths. Identify the dominant conventional type/scope and the
   motivation. Check dependency manifests and lock/resolution changes; advise
   the project-native security/dependency check when applicable.
5. **Verify evidence binding.** Include build, security, review, FR, or PRD
   claims only when their artifact is bound to this pinned run/change set and
   postdates the edits it covers. Unbound or stale evidence is `UNKNOWN`/`SKIP`,
   never inherited from `.trw/context/build-status.yaml` or another run.
6. **Claim exact paths only.** Enumerate the files the user or current task
   owns. Never claim a directory/glob that could capture concurrent changes.
   Exclude secrets, credentials, large unintended binaries, caches, logs, and
   unrelated run artifacts. Every current byte of a named tracked file must be
   owned; a mixed-ownership file requires coordination or an isolated patch
   because a path-scoped candidate cannot isolate hunks. Do NOT `git add` —
   the candidate-first commit below never touches the shared index.
7. **Review the exact owned diff.** Inspect `git diff HEAD -- <owned-paths>`
   for exactly the claimed paths. Abort on any unexpected content; do not
   touch another worker's staged or unstaged files.
8. **Propose the commit.** Show the owned paths and a message using the
   repository's style, normally:

   ```text
   type(scope): concise imperative summary

   WHY: motivation or user-visible reason
   PRD: PRD-...        # only when evidenced/configured
   FR: PRD-...-FR..    # only when evidenced/configured
   ```

   Optional provenance/security trailers follow repository configuration and
   must use verified values; omit or mark unknown rather than fabricating them.
   Keep the subject concise and let the user's message hint influence wording.
9. **Confirm, publish the candidate, report.** Obtain confirmation when the
   invoking client or user policy requires it. Commit through the
   candidate-first entrypoint — NOT `git add` + `git commit`:

   ```bash
   trw-mcp commit-candidate --transaction-id <prepared-transaction-id> \
     --message-file <msg.txt> --run-dir <active-run-dir>
   ```

   It validates ownership against every concurrent claim, runs blocking hooks
   in the isolated candidate context, publishes the reviewed commit to a
   session-namespaced candidate ref, and returns the OID + typed handoff with
   shared state untouched. Verify the reported candidate diff equals the
   intended path list. A refusal or failure is not permission to amend, reset,
   restore, or bypass hooks. Report the candidate ref, OID, and owned paths.
   Direct `git commit --only ... -- <exact-owned-paths>` is ONLY the later
   native-integration step under verified repository quiescence — never the
   concurrent-session default.

## Concurrency contract (PRD-QUAL-119-FR07)

Another worker changing files never changes what done means. Isolation alters
mechanics only: acceptance criteria, test scope, review depth, and evidence
requirements stay exactly as configured. When concurrent changes touch your
paths, isolate the owned change set or fail with an ownership conflict —
never shrink verification, drop assertions, or downgrade evidence to make the
commit possible.

## Candidate-first workflow (PRD-CORE-219-FR07)

trw-commit is a two-step, candidate-first protocol. The pre-edit
`trw-mcp prepare-candidate` command persists repository, branch, parent, run, path,
and pre-edit bindings. After the run journals the edits and records a successful
post-claim build receipt, publish only that prepared transaction:

```bash
trw-mcp commit-candidate --transaction-id <prepared-transaction-id> \
  --message-file <msg.txt> --run-dir <active-run-dir>
```

The commit command accepts neither paths nor a caller-supplied run ID, so it cannot
widen scope or absorb current bytes under arbitrary provenance. It publishes the
reviewed commit to a session-namespaced candidate ref
(refs/trw/commit-candidates/<run>/<transaction>), returns the commit OID,
candidate ref, integrated=false, and a typed native-integration handoff, binds the terminal
evidence into the required run's checkpoint journal — and
never integrates the checked-out branch or shared index automatically
(any automatic-integration request is refused as
automatic_integration_unsupported). Integration is a later native-Git operation under repository
quiescence with a fresh review if the parent drifted. Frequent commits mean
durable candidates; shared-branch advancement is a separate, quiesced concern.

## Optional PR handoff

Create or propose a PR only when asked. Resolve the base from repository/user
policy, not a default sprint branch pattern. Build the PR description from the
committed diff and verified checks. If publication tooling is unavailable,
return the prepared title/body and exact remaining operator step.

## Message guidance

Use the repository's convention. When it follows Conventional Commits:

| Type | Use |
|---|---|
| `feat` | New behavior or capability |
| `fix` | Correctness defect |
| `refactor` | Behavior-preserving structure change |
| `test` | Test-only change |
| `docs` | Documentation-only change |
| `perf` | Measured performance improvement |
| `chore` | Maintenance not better described above |

Explain why the change exists, not a line-by-line summary. Never claim tests,
review, security, requirements, authorship, or model provenance that was not
actually observed and bound to this commit.
