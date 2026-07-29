## Negative-Existence Claim Evidence Rule

Any **negative existence claim** — "no X found", "no callers", "does not exist",
"nothing references" — must cite (a) the exact search you ran, including its
scope, and (b) proof that the search root exists. Confirm the root with a tool
you actually hold: `{tool:trw_code_search}` (which errors on a missing root), a
`Glob` returning entries beneath it, or a directory listing. A raw `grep` over a
path that does not exist returns empty silently, so an empty result over an
unverified root is a broken search, not evidence of absence.
