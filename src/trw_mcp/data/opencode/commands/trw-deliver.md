---
name: trw-deliver
description: Run TRW delivery with build verification and persistence
---

Run the TRW delivery workflow for the current project.

Workflow:
1. Check the current run state and confirm there is work to deliver.
2. Run `trw_build_check(scope="full")`.
3. If the build fails, stop and report the failures clearly.
4. If the build passes, run `trw_deliver()`.
5. Summarize build status, delivery status, and any remaining risks.

Fallbacks:
- If there is no active run, explain that and offer to run `trw_session_start()` first.
- If team-specific state is unavailable, skip team synthesis instead of failing.

Constraints:
- Do not assume Claude Code hooks or Codex-only tools.
- Keep the workflow concise and OpenCode-safe.
