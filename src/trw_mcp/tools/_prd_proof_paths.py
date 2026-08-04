# Parent facade: tools/_prd_transition_gate.py
"""FR05 default-path-proof validation, including proof-file existence.

Belongs to the ``_prd_transition_gate.py`` facade and is re-exported there so
callers and tests keep a single import point.

Why this is its own module: the shape checks (non-empty ``receipt`` /
``removal_assertion``, ``sha256:`` digest) were the WHOLE gate, so a PRD could
keep claiming ``functionality_level: live`` on a receipt naming a test file that
had since been deleted. The proof evaporated and the gate stayed green — a
Potemkin check inside the acceptance-integrity machinery itself. Resolving the
paths a proof names is what makes the receipt content-bound in practice rather
than only in prose.

Conservative by construction, because a false block here stops real deliveries:
only repo-relative source/test paths are hard-checked, and a token resolving
under ANY package prefix passes. Verified against every shipped PRD carrying a
``default_path_proof``: zero flagged (``tests/test_prd_proof_paths.py`` keeps
that guard non-vacuous).

Conservative must not mean *silent*, which is the second defect this module
carried. The extractor only recognised ``py|ts|tsx|sh|md|ya?ml|json``, so a
``.go``, ``.rs``, ``.java``, ``.rb``, ``.cs`` or ``.php`` proof path was never
extracted, never resolved, and never reported — the gate returned green having
verified nothing at all. That is worse than a missing check: it is a check that
claims to have run. Two things follow, and they are the shape of this module:

1. The recognised set covers the mainstream languages, so those paths are now
   genuinely resolved and hard-block when absent (:data:`_PROOF_EXTENSIONS`).
   An allowlist is still the right shape for the BLOCKING tier — a permissive
   "anything with a slash and a dot" rule would hard-block on prose and URLs,
   and a false block stops a real delivery.
2. Nothing that looks like a path is dropped in silence. Anything the blocking
   tier cannot adjudicate — a path-shaped token with an unrecognised extension,
   an absolute ``/tmp/...`` path, a ``.trw/`` run artifact, or a resolver fault
   — is reported as ADVISORY (:data:`DEFAULT_PATH_PROOF_UNVERIFIED`,
   :data:`DEFAULT_PATH_PROOF_CHECK_FAILED`) rather than skipped.

Advisory, not blocking, for tier 2, because absence genuinely proves nothing
there: a ``/tmp`` log and an ephemeral ``.trw/runs/...`` receipt are expected to
be gone from a fresh clone, and an unrecognised extension may not be a path at
all. The point is that the caller can no longer read "green" as "everything you
cited was checked". Both cited ``.trw/`` receipts in the shipped PRD corpus
(PRD-CORE-219, PRD-QUAL-119) are in fact already deleted — precisely the
evaporated-proof incident this module exists to catch, invisible until now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# PRD-QUAL-119-FR05: a live claim needs a content-bound default-path receipt
# plus a superseded-path removal assertion; unit/substrate tests alone fail.
MISSING_DEFAULT_PATH_PROOF = "default_path_proof_missing"
#: The proof is well-formed but names a repo file that no longer exists. Distinct
#: from MISSING_DEFAULT_PATH_PROOF so the deliver message can say "your proof
#: points at something that is gone" rather than "your proof is malformed".
DEFAULT_PATH_PROOF_FILE_MISSING = "default_path_proof_file_missing"
#: ADVISORY: the proof cites something path-shaped that the blocking tier cannot
#: adjudicate — an unrecognised extension, an absolute path, or a non-durable
#: ``.trw/`` run artifact — and it did not resolve. Never blocks; exists so a
#: caller cannot read a green gate as "every path you cited was verified".
DEFAULT_PATH_PROOF_UNVERIFIED = "default_path_proof_paths_unverified"
#: ADVISORY: the path resolver itself faulted, so NO path in this proof was
#: checked. Fail-open on blocking (infrastructure must not stop a delivery) but
#: never silent — an unrun check previously looked identical to a passing one.
DEFAULT_PATH_PROOF_CHECK_FAILED = "default_path_proof_check_failed"

#: Extensions the BLOCKING tier recognises: a token ending in one of these is
#: resolved against the repo and hard-blocks when absent. Mainstream languages
#: only — the gate's job is to resolve source and test files, and every entry
#: added here is a new way to false-block, so exotic and highly prose-collidable
#: extensions (``.m``, ``.r``) stay out and land in the advisory tier instead.
_PROOF_EXTENSIONS: tuple[str, ...] = (
    # Python / web
    "py",
    "pyi",
    "ts",
    "tsx",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "vue",
    "svelte",
    # JVM
    "java",
    "kt",
    "kts",
    "scala",
    "groovy",
    # Systems
    "go",
    "rs",
    "c",
    "h",
    "cc",
    "cpp",
    "cxx",
    "hpp",
    "hh",
    "zig",
    "swift",
    # .NET
    "cs",
    "fs",
    "vb",
    # Scripting
    "rb",
    "php",
    "pl",
    "lua",
    "ex",
    "exs",
    "erl",
    "dart",
    "sh",
    "bash",
    "ps1",
    # Docs / data / build definitions
    "md",
    "yaml",
    "yml",
    "json",
    "toml",
    "sql",
    "proto",
    "tf",
    "gradle",
)
#: Longest-first: the alternation is unanchored, so a shorter prefix listed
#: first would win and truncate the token. ``foo.tsx`` matched ``foo.ts`` under
#: the previous ordering, silently resolving a DIFFERENT file than the one the
#: proof named. The trailing ``(?!\w)`` closes that off independently.
_EXT_ALTERNATION = "|".join(sorted(_PROOF_EXTENSIONS, key=lambda ext: (-len(ext), ext)))

#: A repo-relative source/test path, optionally followed by a pytest ``::node``
#: selector. The leading guard stops mid-path and mid-word matches so
#: ``a/b/c.py`` yields one token rather than several suffixes of itself.
_PROOF_PATH_TOKEN = re.compile(rf"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:{_EXT_ALTERNATION})(?!\w))(?:::[\w\[\]-]+)?")

#: The ADVISORY tier's detector: anything shaped like a path, whatever its
#: extension, including absolute paths. It requires at least one ``/`` — that is
#: what keeps ordinary prose out — and the leading guard means a URL's
#: ``https://host/a/b.html`` is not matched, since the host follows a ``/``.
_PATH_SHAPED_TOKEN = re.compile(r"(?<![\w/.-])(/?(?:[\w.-]+/)+[\w.-]+\.[A-Za-z][\w]{0,7})(?:::[\w\[\]-]+)?")

#: Monorepo packages a proof path may be written relative to.
_PACKAGES: tuple[str, ...] = (
    "trw-mcp",
    "trw-memory",
    "trw-eval",
    "trw-distill",
    "trw-loop",
    "trw-swarm",
    "trw-autoresearch",
    "trw-metaharness",
    "backend",
    "platform",
)

#: Roots a token is tried against, in order: the project root, each package, and
#: each package's import root (receipts routinely write ``server/_tools.py``
#: meaning ``trw-mcp/src/trw_mcp/server/_tools.py``).
_PREFIXES: tuple[str, ...] = (
    "",
    *(f"{pkg}/" for pkg in _PACKAGES),
    *(f"{pkg}/src/{pkg.replace('-', '_')}/" for pkg in _PACKAGES),
)

#: Prefixes that are never durable repo files, so their ABSENCE proves nothing —
#: they are demoted to the advisory tier rather than hard-blocking. They are
#: still resolved first: a receipt naming a ``.trw/`` artifact that is still on
#: disk is verified, and only a vanished one is reported.
_NON_DURABLE_PREFIXES: tuple[str, ...] = (".trw/",)


def _has_letter_stem(token: str) -> bool:
    """Reject enumerations that merely look like filenames.

    ``§1.c`` and ``step 3.h`` are prose, not paths — a numeric-only stem can
    never be a source file, and hard-blocking on one would be a false block on
    text this repo's own PRDs contain.
    """
    stem = token.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return any(char.isalpha() for char in stem)


def _resolves(token: str, project_root: Path) -> bool:
    """True when *token* names a file that exists, under any accepted root."""
    if token.startswith("/"):
        return Path(token).exists()
    return any((project_root / f"{prefix}{token}").exists() for prefix in _PREFIXES)


def classify_proof_paths(blob: str, project_root: Path | None = None) -> tuple[list[str], list[str]]:
    """Split the paths *blob* names into ``(missing, unverified)``.

    ``blob`` is free-form receipt prose. ``missing`` is the hard-blocking tier:
    a repo-relative path in a recognised language that does not exist. Everything
    path-shaped that the blocking tier cannot adjudicate and that did not resolve
    lands in ``unverified`` — never dropped. Both lists are sorted for a
    deterministic message.
    """
    if project_root is None:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root()

    recognised = {match.group(1) for match in _PROOF_PATH_TOKEN.finditer(blob)}
    path_shaped = {match.group(1) for match in _PATH_SHAPED_TOKEN.finditer(blob)}

    missing: set[str] = set()
    unverified: set[str] = set()
    for token in recognised | path_shaped:
        if not _has_letter_stem(token) or _resolves(token, project_root):
            continue
        durable = token in recognised and not token.startswith("/") and not token.startswith(_NON_DURABLE_PREFIXES)
        (missing if durable else unverified).add(token)
    return sorted(missing), sorted(unverified)


def missing_proof_paths(blob: str, project_root: Path | None = None) -> list[str]:
    """Return only the hard-blocking tier of :func:`classify_proof_paths`."""
    return classify_proof_paths(blob, project_root)[0]


@dataclass(frozen=True)
class ProofPathFindings:
    """The FR05 proof verdict, split by severity.

    ``blocking`` stops the transition; ``advisory`` records what the gate could
    NOT verify so a caller never mistakes an unrun check for a passing one.
    """

    blocking: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)


def default_path_proof_findings(
    frontmatter: dict[str, object],
    level: str,
    project_root: Path | None = None,
) -> ProofPathFindings:
    """FR05 with both severities — see :func:`default_path_proof_blocking`.

    A shape failure is blocking and short-circuits (there is nothing to resolve
    yet). Otherwise every path both fields name is classified, and a resolver
    fault degrades to advisory rather than to silence.
    """
    if level != "live":
        return ProofPathFindings()
    proof = frontmatter.get("default_path_proof")
    if not isinstance(proof, dict):
        return ProofPathFindings(blocking=[MISSING_DEFAULT_PATH_PROOF])
    receipt = str(proof.get("receipt", "")).strip()
    digest = str(proof.get("source_digest", "")).strip()
    removal = str(proof.get("removal_assertion", "")).strip()
    if not (receipt and removal and digest.startswith("sha256:")):
        return ProofPathFindings(blocking=[MISSING_DEFAULT_PATH_PROOF])

    try:
        missing, unverified = classify_proof_paths(f"{receipt} {removal}", project_root)
    except Exception:  # justified: a resolver fault must not block on infrastructure
        # Reported, not swallowed: fail-open on BLOCKING only. Returning a bare
        # [] here made "the check could not run" indistinguishable from "the
        # check passed", which is the truthfulness failure, not the fault itself.
        logger.info("default_path_proof_path_check_failed", exc_info=True)
        return ProofPathFindings(advisory=[DEFAULT_PATH_PROOF_CHECK_FAILED])

    blocking: list[str] = []
    advisory: list[str] = []
    if missing:
        # logger.info, not debug: a default install filters debug out entirely,
        # and this is the record a maintainer needs after a block.
        logger.info("default_path_proof_names_missing_files", missing=missing)
        blocking.append(DEFAULT_PATH_PROOF_FILE_MISSING)
    if unverified:
        logger.info("default_path_proof_paths_unverified", unverified=unverified)
        advisory.append(DEFAULT_PATH_PROOF_UNVERIFIED)
    return ProofPathFindings(blocking=blocking, advisory=advisory)


def default_path_proof_blocking(
    frontmatter: dict[str, object],
    level: str,
    project_root: Path | None = None,
) -> list[str]:
    """FR05: a ``live`` claim requires a content-bound default-path receipt.

    The ``default_path_proof`` frontmatter block must carry a non-empty
    ``receipt``, a ``source_digest`` binding the proof to current content
    (sha256:…), and a ``removal_assertion`` naming the superseded-path absence
    proof. Unit or substrate tests alone never satisfy this.

    Beyond shape, every repo file the receipt or the removal assertion names must
    still exist. Both fields are scanned: the incident that motivated this had
    the deleted test named in the removal assertion.

    Blocking tier only. Callers that can surface warnings should use
    :func:`default_path_proof_findings` instead — dropping its ``advisory`` is
    dropping the record of what the gate could not check.
    """
    return default_path_proof_findings(frontmatter, level, project_root).blocking
