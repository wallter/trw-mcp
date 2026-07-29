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
only repo-relative source/test paths are resolved, a token resolving under ANY
package prefix passes, and absolute paths (``/tmp/...``) plus ``.trw/`` run
artifacts are skipped entirely — neither is a durable repo file. Verified against
every shipped PRD carrying a ``default_path_proof``: zero flagged
(``tests/test_prd_proof_paths.py`` keeps that guard non-vacuous).
"""

from __future__ import annotations

import re
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

#: A repo-relative source/test path, optionally followed by a pytest ``::node``
#: selector. The leading guard stops mid-path and mid-word matches so
#: ``a/b/c.py`` yields one token rather than several suffixes of itself.
_PROOF_PATH_TOKEN = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|sh|md|ya?ml|json))(?:::[\w\[\]-]+)?")

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

#: Prefixes that are never durable repo files, so their absence proves nothing.
_SKIPPED_PREFIXES: tuple[str, ...] = (".trw/",)


def missing_proof_paths(blob: str, project_root: Path | None = None) -> list[str]:
    """Return the repo-relative paths *blob* names that do not exist.

    ``blob`` is free-form receipt prose. Tokens that are absolute, or that live
    under a non-durable prefix, are ignored. Sorted for a deterministic message.
    """
    if project_root is None:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root()

    missing: set[str] = set()
    for match in _PROOF_PATH_TOKEN.finditer(blob):
        token = match.group(1)
        if token.startswith("/") or token.startswith(_SKIPPED_PREFIXES):
            continue
        if any((project_root / f"{prefix}{token}").exists() for prefix in _PREFIXES):
            continue
        missing.add(token)
    return sorted(missing)


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
    """
    if level != "live":
        return []
    proof = frontmatter.get("default_path_proof")
    if not isinstance(proof, dict):
        return [MISSING_DEFAULT_PATH_PROOF]
    receipt = str(proof.get("receipt", "")).strip()
    digest = str(proof.get("source_digest", "")).strip()
    removal = str(proof.get("removal_assertion", "")).strip()
    if not (receipt and removal and digest.startswith("sha256:")):
        return [MISSING_DEFAULT_PATH_PROOF]

    try:
        missing = missing_proof_paths(f"{receipt} {removal}", project_root)
    except Exception:  # justified: a resolver fault must not block on infrastructure
        logger.info("default_path_proof_path_check_skipped", exc_info=True)
        return []
    if missing:
        # logger.info, not debug: a default install filters debug out entirely,
        # and this is the record a maintainer needs after a block.
        logger.info("default_path_proof_names_missing_files", missing=missing)
        return [DEFAULT_PATH_PROOF_FILE_MISSING]
    return []
