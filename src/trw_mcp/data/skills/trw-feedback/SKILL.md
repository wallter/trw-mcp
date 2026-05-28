---
name: trw-feedback
description: >
  File a TRW bug, install issue, feature request, feedback note, or question
  through the authenticated `POST /v1/submissions` channel. Prompts for the
  category and a one-line summary, optionally attaches recent learnings + the
  last error trace, and returns a submission_id the operator can quote on
  follow-up. Use: /trw-feedback
category: feedback
user-invocable: true
disable-model-invocation: true
argument-hint: "[category] [\"summary\"]"
---

# Feedback Skill

Use when: the operator wants to report a TRW bug, an install issue, a feature
request, general feedback, or ask a question — and you want a single guided
flow that captures context and submits it through the official channel.

This skill wraps the `trw_submit_feedback` MCP tool. The tool POSTs to
`<backend_url>/v1/submissions` (PRD-CORE-182) using the operator's
`platform_api_key` from `.trw/config.yaml`. PII redaction (license keys, API
key prefixes, `$HOME` paths, sensitive env vars) runs before the network call.

## Workflow

1. **Ask for the category**. Valid values:
   - `bug` — something is broken in TRW
   - `install_issue` — install or upgrade failed
   - `feedback` — general feedback, no action required
   - `feature_request` — a new capability the operator wants
   - `question` — a clarification request

   If the operator provided a category as the first argument, use it directly.
   Otherwise prompt: "Which category? bug | install_issue | feedback |
   feature_request | question".

2. **Ask for a one-line summary**. Keep it under ~120 characters. If the
   operator already provided a quoted summary as the second argument, use
   that. Otherwise prompt: "One-line summary?".

3. **Confirm attachments**. Ask whether to include the recent learnings + the
   last unhandled tool exception trace. Defaults to **yes** for both — the
   maintainer needs the context to triage. The operator can say "no" to
   either.

4. **Call the tool**:

   ```python
   trw_submit_feedback(
       category=<category>,
       summary=<summary>,
       detail=<optional longer detail or empty string>,
       include_recent_learnings=<bool, default True>,
       include_last_error=<bool, default True>,
   )
   ```

5. **Return the submission_id** to the operator so they can quote it on a
   follow-up email. If the tool returned a structured error (e.g. backend
   unreachable, missing API key), surface the remediation hint
   conversationally — do not retry silently.

## Notes

- The skill never auto-submits. Confirm the assembled body with the operator
  before calling the tool when the operator did not pre-supply all arguments.
- The redactor runs inside the tool — do not pre-redact in this skill.
- See `https://trwframework.com/llms.txt#reporting-issues-to-trw` for the
  canonical operator-facing description of the channel.
