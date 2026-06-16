# Security Policy

We take the security of trw-mcp seriously. This document explains which
versions receive security fixes and how to report a vulnerability privately.

## Supported Versions

Security fixes are provided for the current minor release line. Older minor
versions are not maintained — please upgrade to the latest release before
reporting.

| Version | Supported          |
| ------- | ------------------ |
| 0.55.x  | :white_check_mark: |
| < 0.55  | :x:                |

## Reporting a Vulnerability

Please do **not** open a public GitHub issue for security vulnerabilities.

Instead, report privately by email to **security@trwframework.com**. Include
as much detail as you can:

- A description of the vulnerability and its potential impact.
- Steps to reproduce, or a proof-of-concept, if available.
- The affected version(s) and your environment.
- Any suggested remediation.

We will acknowledge your report within **3 business days** and keep you
informed as we investigate and work toward a fix. We may ask for additional
information to reproduce or validate the issue.

## Supply chain & install integrity

We aim to be precise — not reassuring — about the integrity guarantees of the
install path. The honest trust model today is:

**1. Bootstrap (`curl -fsSL https://trwframework.com/install.sh | bash`).**
The integrity of the bootstrap script itself rests on **TLS to
`trwframework.com`**. There is no out-of-band signature on the bootstrap; an
attacker who can break TLS or compromise the web origin can serve a different
bootstrap. Inspect the script before piping it to a shell if your threat model
requires it.

**2. The downloaded installer is SHA-256 verified.**
The bootstrap fetches the full installer (`install-trw.py`) from the releases
API and verifies its bytes against an `installer_checksum` served by that API
(recorded in the platform database). This is the SHA-256 of the installer
script itself — **distinct** from the release ZIP's checksum
(`artifact_checksum`). A mismatch is a hard abort; the installer is never run.

**3. What the checksum does and does not defend against.**
The checksum and the installer artifact share a **trust origin** — both are
served by the API host. So the checksum defends against:

- tampering with the installer object in object storage (S3), which the API's
  database-recorded checksum will not match;
- transit corruption between the CDN/storage and the client.

It does **not** provide cryptographic provenance against a **full compromise of
the API host**: an attacker who controls that host can serve a malicious
installer *and* a matching checksum. The checksum is an integrity check, not a
signature. This residual risk is acknowledged.

**4. Out-of-band hardening levers (for higher-assurance / enterprise installs).**

- `TRW_INSTALLER_SHA256=<sha>` — pin the expected installer SHA-256 obtained
  from a **trusted channel** (e.g. a value you record from a known-good
  install, or one published by us out of band). This overrides the API-served
  checksum and forces a hard abort on mismatch, so a compromised API host
  cannot substitute its own checksum.
- `TRW_REQUIRE_INSTALLER_CHECKSUM=1` (or `--require-checksum`) — **fail-closed**
  when no checksum is published. By default, if a release exposes no
  `installer_checksum`, the bootstrap warns and proceeds on TLS alone
  (fail-open, for backward compatibility). Setting this lever turns that
  absent-checksum path into a hard abort, so an integrity check is mandatory.

**5. Offline / air-gapped installs.**
The `--offline` path installs from embedded wheels only. Note that **only
`trw-mcp` and `trw-memory` are embedded** — their transitive dependencies
(pydantic, pydantic-settings, ruamel.yaml, structlog, …) must already be present
in the target environment or pre-staged in a wheelhouse exposed via
`PIP_FIND_LINKS`.

**6. Roadmap.**
Cryptographic release signing (e.g. Sigstore/cosign or minisign) and SLSA
provenance attestation are **roadmap items** and are not yet in place. Until
they ship, the strongest available defense against API-host compromise is the
out-of-band `TRW_INSTALLER_SHA256` pin above.

## Known dependency advisories (optional ML extras)

The **core** `trw-mcp` install carries no known-vulnerable dependencies. The
advisory below applies **only to the optional, transitive ML embedding path** and
is documented here for transparency.

**`torch` — CVE-2025-3000 (no fix available).** When embeddings are enabled,
`trw-mcp` uses `trw-memory`'s local embedding provider, which pulls
`sentence-transformers` (via the optional `trw-memory[embeddings]` extra) and,
transitively, `torch`. The advisory affects `torch.jit.script` (a local memory-corruption
issue when JIT-scripting). TRW's embedding path runs a forward pass over the
`all-MiniLM-L6-v2` model and **never calls `torch.jit.script`**, so the vulnerable
code path is not reached. No stable upstream fix exists as of this revision.

Mitigations:

- Embeddings are an **optional extra**, not a default/core dependency — installs that
  do not enable the embedding path never pull `torch`.
- The embedding model runs **locally/offline**; no untrusted model is JIT-scripted.

We will raise the pin as soon as a fixed `torch` release is published. (The companion
`transformers` advisories PYSEC-2025-217 / CVE-2026-1839 are resolved by
`transformers >= 5.0`, which the current ML stack satisfies.)

## Scope

This policy covers the `trw-mcp` package — its source code, bundled data, and
the MCP server it ships. Vulnerabilities in third-party dependencies should be
reported to their respective maintainers, though we appreciate a heads-up so we
can update our dependency pins.

## Safe Harbor

We support responsible, good-faith security research. If you make a good-faith
effort to comply with this policy during your research, we will consider your
research authorized, we will work with you to understand and resolve the issue
quickly, and we will not pursue or support legal action against you. Please
avoid privacy violations, data destruction, and service disruption, and only
interact with accounts you own or have explicit permission to access.

Thank you for helping keep trw-mcp and its users safe.
