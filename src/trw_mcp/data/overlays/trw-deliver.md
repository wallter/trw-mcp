## DELIVER PHASE OVERLAY (v18.1_TRW)

This overlay augments the shared core with delivery-specific content.

---

### GIT (Delivery Flow)

<git_conventions>
```bash
git status -sb
git add <specific-paths>           # Use `git add <specific-paths>` (interactive modes unavailable)
git commit -m "feat(scope): msg" -m "WHY: rationale" -m "RUN_ID: {RUN_ID}"
git push -u origin "{BRANCH}"
gh pr create --fill --head "{BRANCH}"
```

All file paths in commands, logs, and shard cards MUST be absolute paths derived from TASK_DIR or REPO_ROOT. WHY: Relative paths break when shards execute from different working directories; absolute paths ensure reproducibility across agent sessions.
Update CHANGELOG.md `[Unreleased]` for user-visible changes at DELIVER.
When `MCP_MODE: tool`, ORC SHOULD call `trw_event("git_commit", data={branch, message, run_id})` after commits.
</git_conventions>

---

### REQUIREMENTS (Post-Development)

<post_development>
Before DELIVER:
```yaml
requirements_traceability:
  - req_id: REQ-001
    implemented_in: [src/auth/login.py]
    verified_by: [tests/test_auth.py::test_login]
    status: PASS
```
</post_development>

### AARE-F Tools

When `MCP_MODE: tool`: `trw_traceability_check` at DELIVER.

---

### DELIVER Checklist

1. PR created OR archived
2. `reports/final.md` complete
3. CLAUDE.md synced (`trw_claude_md_sync`) — MUST
4. CHANGELOG.md `[Unreleased]` updated
5. Run state marked `complete`
6. `trw_event("claude_md_synced", data={scope, entries_promoted})` logged

---

### PSR at DELIVER

| Phase | Inputs | Outputs |
|-------|--------|---------|
| DELIVER | High-impact learnings | Promote findings → `trw_claude_md_sync` (MUST) |

---

### Delivery Testing

| Phase | Testing Activity |
|-------|-----------------|
| DELIVER | E2E smoke tests; coverage gate (global ≥85%, diff ≥90%) |

---

<!-- cache_boundary: content below changes per session -->

## MODEL

- Primary: **Opus 4.6**; child shards (depth ≥2) or trivial subtasks MAY use Haiku 4.5 / Sonnet 4.5
- Agents act, chat remains minimal, artifacts are auditable. WHY: Token budget is finite; every chat token displaces a reasoning or code token.

---

<variables>
TASK       := task_short_desc
TASK_DIR   := ./docs/{TASK}
RUN_ID     := {utc_ts}-{short_id}
RUN_ROOT   := {TASK_DIR}/runs/{RUN_ID}
REPO_ROOT  := $(git rev-parse --show-toplevel)
BRANCH     := feat/{TASK}-{short_id}
ORC        := Orchestrator
</variables>

---

Version date: 2026-02-10
