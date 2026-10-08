## Negative-Existence Claim Evidence Rule

Any **negative existence claim** — "no X found", "no callers", "does not exist",
"nothing references" — must cite (a) the exact search you ran, including its
scope, and (b) proof that the search root exists. Run the search with `Grep` or
`rg` over the named roots and show the command; without a search tool, ask the
lead, or use `trw_code` for a symbol. Confirm the root with a tool you hold: a
`Glob` returning entries beneath it, a directory listing, or `rg --files <root>`.
A raw `grep` over a missing path returns empty silently, so an empty result over
an unverified root is a broken search, not evidence of absence.
