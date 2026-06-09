---
name: trw-feedback
description: >
  File a TRW bug, install issue, feature request, feedback note, or question
  through the authenticated `POST /v1/submissions` channel. Prompts for the
  category and a one-line summary, optionally attaches recent learnings + the
  last error trace, and returns a submission_id the operator can quote on
  follow-up. Use: /trw-feedback
user-invocable: true
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

1. **Ask for the category**. Valid values (canonical PRD-CORE-182 enum):
   - `bugfix` — something is broken in TRW
   - `installation` — install or upgrade failed
   - `feedback` — general feedback, no action required
   - `feature_request` — a new capability the operator wants
   - `question` — a clarification request
   - `other` — does not fit the above

   If the operator provided a category as the first argument, use it directly.
   Otherwise prompt: "Which category? bugfix | installation | feedback |
   feature_request | question | other".

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
       subject=<one-line summary, max 200 chars>,
       message=<longer detail, min 10 chars; redacted client-side before send>,
       contact_email=<optional reply-to>,
       metadata=<optional dict[str,str]; auto-merged with trw_mcp_version etc.>,
   )
   ```

   Both surfaces share the same canonical tool (PRD-CORE-182); PRD-INFRA-132
   adds the PII redactor to `message` and surfaces this guided skill.

5. **Return the submission_id** to the operator so they can quote it on a
   follow-up email. If the tool returned a structured error (e.g. backend
   unreachable, missing API key), surface the remediation hint
   conversationally — do not retry silently.

## Autonomous / agent-initiated use

An agent or sub-agent may invoke this skill without a human present. In that
case, derive `category`, `subject`, and `message` from the current context
(the bug/rough edge you just hit, the failing command, the install issue) and
call `trw_submit_feedback` directly — do not block waiting for interactive
prompts. When an operator IS present, still confirm with them first.

## Notes

- The skill never auto-submits. Confirm the assembled body with the operator
  before calling the tool when the operator did not pre-supply all arguments.
- The redactor runs inside the tool — do not pre-redact in this skill.
- See `https://trwframework.com/llms.txt#reporting-issues-to-trw` for the
  canonical operator-facing description of the channel.
