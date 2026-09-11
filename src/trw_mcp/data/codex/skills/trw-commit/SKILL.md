---
name: trw-commit
description: Commit requested work using repository-native conventions, scoped changes, and verified evidence. Use only when the user asks for commits, including an ongoing request to commit frequently.
---

# Commit requested work

Use when: the user asks for a commit, or has asked for frequent commits during a
task. Not for unrequested commits — a commit is an outward-facing act and the
request is what authorizes it.

Make small, coherent commits of the user's requested work. Follow the repository's
Git policy; this skill does not add a mandatory run, pre-edit claim, branch naming
scheme, or extra approval round. A request to commit frequently applies throughout
the task; do not ask again for each ordinary scoped commit.

## Workflow

1. **Inspect ownership.** Check `git status --short`, `git diff`, and
   `git diff --cached --name-only`. Identify exactly which changes belong to this
   task, including any files already staged by another worker.
2. **Review and validate.** Inspect the intended diff and run relevant project-native
   checks. Explain limitations accurately; a commit is not proof of release readiness.
3. **Commit a coherent milestone.** Prefer the repository's scoped commit helper when
   provided; otherwise use native Git with explicit owned paths. Never stage the
   entire shared tree merely because the user requested all changes from this task.
   Review the exact content that will be committed before invoking the commit.
4. **Verify the result.** Inspect the resulting commit's OID, paths and diff. Confirm
   unrelated staging and working-tree changes remain intact. Report what was
   committed and any task changes still pending. Do not push unless requested.

## Shared-tree pitfalls

- A path-scoped commit includes the whole selected working-tree file. It does **not**
  isolate your hunks in a file another worker also edited. Use a reviewed patch with
  a repository-supported isolated index/worktree, or coordinate that file first.
- Do not amend, reset, stash, clean, rebase, discard changes, or bypass hooks to make
  a commit succeed without separate authorization for that action.
- Preserve existing ownership checks. If the repository explicitly requires a
  prepared candidate transaction, honor its actual prerequisites. Do not fabricate
  a retrospective claim; resolve the conflict using an authorized native workflow.
- No active TRW run is not itself a reason to block an ordinary native commit.
  Include run/PRD/build trailers only when genuinely bound to this change set;
  omit unknown provenance and never invent authorship or validation results.

## Message and handoff

Use the repository's message convention, usually `type(scope): concise purpose`
with a short WHY. Prefer several verified milestones over one accumulated diff.
For a requested PR, use the repository template; [PR-TEMPLATE.md](PR-TEMPLATE.md)
is a minimal fallback, not an additional required ceremony.
