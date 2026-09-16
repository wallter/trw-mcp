---
name: trw-plan-review
description: Propose a work plan to a formation peer, review a peer's plan, and tell a current review from a stale one. Use before starting work that touches paths another member may declare, or when a peer sends you a plan body.
---

# Propose and review a peer's work plan

Use when: you are a member of a formation and are about to change paths another
member may declare, or a peer has sent you a plan body to review.

Two agents can already send each other text. This gives that text a shape: a
tamper-evident statement of what you intend to change, a peer's independent read
of it, and a way to tell whether the reply you got is about the plan you
currently hold.

**None of it is authority.** A plan is a statement of intent, a review is
advisory feedback, and an ACK is durable receipt. Nothing here grants
permission, records completion, or blocks a write. If you want a peer to stop,
say so in words and wait for an answer — do not read one into a review.

## The shape

`trw-mcp plan` reads local files and writes a body to stdout. It never sends
anything. YOU send the body with `trw_send`, through the MCP session you already
have, which is why this adds no MCP tool and works from any harness with a
shell.

## Workflow

### Before you start work

```
trw-mcp plan precheck <path> [<path> ...]
```

Prints who DECLARES each path. Read the advisory line it writes to stderr: a
declared path may be idle, and an undeclared path is an absent declaration, not
permission. If a path is declared by someone else, that is a reason to ask, not
a prohibition.

### Propose

```
trw-mcp plan propose --plan-id <32-hex> --revision 1 \
  --path <a repo-relative source path> \
  --test-path <a repo-relative test path> \
  --summary "what you intend to do"
```

Paths are whatever your project actually uses — this protocol has no opinion
about language or layout, only that each path is repo-relative.

The last line of stdout is the body. Send it:

```
trw_send(recipient_member_id="<peer>", request_key="<plan_id>:1",
         body="<body>", kind="request", delivery_class="on_demand")
```

`request_key` is `plan_id:revision` and is an EXACT RETRY key only. Changing the
body under the same key is a conflict, not an update — bump the revision.

### Review a peer's plan

Fetch with `trw_inbox(action="fetch")`, then:

```
trw-mcp plan review --body - <<< "<the body you received>"
```

It verifies the digest, refuses anything malformed, and reruns the ownership
check ITSELF rather than trusting the sender's reading — which is the only
reason your review carries information the sender did not already have. Send the
result back with `kind="reply"`, then ACK the original with
`trw_inbox(action="ack", message_ids=[...])`.

### Check whether a review is still current

```
trw-mcp plan verify --review - --proposal your-current-proposal.json
```

`CURRENT` means the findings describe the plan you hold. `STALE` means they
describe an earlier revision — the reviewer did nothing wrong, it simply has not
seen your newer plan. Exit status is 3 for anything that is not current, so a
script can branch on it.

## Bounds

One exchange is one proposal and one review, plus two ACKs. This is a
CONVENTION, not an enforced limit: nothing in this slice can bound how many
messages you send, and the only real ceiling is the transport's own per-group
and per-sender budget. Keep to it because the point of the protocol is fixed
overhead, not because something will stop you.

A body must fit the receiving group's admitted size limit. The CLI warns when a
body exceeds THIS process's configured bound, but that bound is a local
candidate: admission policy is snapshotted per group when the group is created,
so `trw_send` is the authority on whether a body is deliverable.

## Refusals

Every refusal is a closed reason code with a non-zero exit. Common ones:
`absolute_path` (send repo-relative paths so a peer can recompute the digest
without knowing your project root), `digest_mismatch` (the body was altered
after it was built), `authority_field_present` (a body tried to carry an
approval or completion field), `unknown_key` (the schema is closed).
