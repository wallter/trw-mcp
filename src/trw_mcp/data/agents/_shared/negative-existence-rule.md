## Negative-Existence Claim Evidence Rule

Any **negative existence claim** — "no X found", "no callers", "does not exist",
"nothing references" — must cite (a) the exact search you ran, including its
scope, and (b) proof that the search root exists. Run the absence search with
`Grep`, or with `rg` over the named roots (or `trw-distill query` when it is
installed), and show the command and root. Confirm the root with a tool you
actually hold: a `Glob` returning entries beneath it, a directory listing, or
`rg --files <root>`. A raw `grep` over a path that does not exist returns empty
silently, so an empty result over an unverified root is a broken search, not
evidence of absence.
