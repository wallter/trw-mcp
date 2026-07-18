"""Out-of-band AcceptanceManifest derivation, store, and read adapter.

PRD-QUAL-120 FR02/FR03. This module is the SOLE AcceptanceManifest writer per
the requirements authority map (``prd_utils.REQUIREMENTS_AUTHORITY_MAP``): it
derives acceptance truth from the raw authored PRD bytes plus typed receipts,
persists it atomically under ``.trw/requirements/acceptance-manifests/``, and
exposes READ-ONLY views for the registry and delivery. It has no API that
writes PRD source bytes, INDEX, or ROADMAP, and the manifest's own digest is
never an input to its derivation (no feedback loop).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models.requirements import (
    AcceptanceManifest,
    AcceptedRequirement,
    AcceptedRequirementState,
)
from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger(__name__)

MANIFEST_RELATIVE_DIR = Path(".trw") / "requirements" / "acceptance-manifests"

# Blocker vocabulary (typed, bounded).
BLOCKER_NO_RECEIPT = "no_receipt_recorded"
BLOCKER_EXISTENCE_ONLY = "existence_only_evidence"


@dataclass(frozen=True, slots=True)
class ReceiptEvidence:
    """One typed proof for a requirement: identity + content binding."""

    receipt_id: str
    evidence_digest: str = ""  # sha256:… of the proof artifact bytes


def derive_manifest(
    prd_path: Path,
    receipts: dict[str, ReceiptEvidence],
    *,
    completion_outcome: str = "unknown",
) -> AcceptanceManifest:
    """Derive the manifest from RAW authored PRD bytes plus typed receipts.

    Per requirement (from the PRD's verification mappings): a receipt with a
    content-bound evidence digest is ACCEPTED; a receipt without content
    binding is BLOCKED (existence-only evidence cannot pass, FR02); a missing
    receipt is UNKNOWN with a typed blocker. Fail-closed: no mapping data at
    all yields zero accepted requirements, never an implicit pass.
    """
    raw = prd_path.read_bytes()
    frontmatter = parse_frontmatter(raw.decode("utf-8", errors="replace"))
    prd_id = str(frontmatter.get("id", prd_path.stem))

    requirement_ids: list[str] = []
    verification = frontmatter.get("verification")
    if isinstance(verification, dict) and isinstance(verification.get("mappings"), list):
        requirement_ids.extend(
            str(mapping["requirement_id"]).strip()
            for mapping in verification["mappings"]
            if isinstance(mapping, dict) and str(mapping.get("requirement_id", "")).strip()
        )

    accepted: list[AcceptedRequirement] = []
    for requirement_id in requirement_ids:
        receipt = receipts.get(requirement_id)
        if receipt is None or not receipt.receipt_id.strip():
            accepted.append(
                AcceptedRequirement(
                    requirement_id=requirement_id,
                    state=AcceptedRequirementState.UNKNOWN,
                    blocker=BLOCKER_NO_RECEIPT,
                )
            )
        elif not receipt.evidence_digest.strip().startswith("sha256:"):
            accepted.append(
                AcceptedRequirement(
                    requirement_id=requirement_id,
                    state=AcceptedRequirementState.BLOCKED,
                    receipt_id=receipt.receipt_id,
                    blocker=BLOCKER_EXISTENCE_ONLY,
                )
            )
        else:
            accepted.append(
                AcceptedRequirement(
                    requirement_id=requirement_id,
                    state=AcceptedRequirementState.ACCEPTED,
                    receipt_id=receipt.receipt_id,
                    evidence_digest=receipt.evidence_digest,
                )
            )

    return AcceptanceManifest(
        prd_id=prd_id,
        source_digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        requirements=accepted,
        completion_outcome=completion_outcome,
    )


def manifest_path(trw_root: Path, prd_id: str) -> Path:
    return trw_root / MANIFEST_RELATIVE_DIR.relative_to(".trw") / f"{prd_id}.json"


def persist_manifest(manifest: AcceptanceManifest, trw_root: Path) -> Path:
    """Atomically persist the canonical manifest (schema-versioned payload)."""
    target = manifest_path(trw_root, manifest.prd_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"manifest": manifest.model_dump(mode="json"), "manifest_digest": manifest.canonical_digest()},
        sort_keys=True,
        indent=2,
    )
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), suffix=".json.tmp")
    tmp = Path(tmp_name)
    try:
        os.close(fd)
        tmp.write_text(payload + "\n", encoding="utf-8")
        tmp.replace(target)
    except Exception:  # justified: cleanup must not mask the original error
        tmp.unlink(missing_ok=True)
        raise
    logger.info("acceptance_manifest_persisted", prd_id=manifest.prd_id)
    return target


def load_manifest(trw_root: Path, prd_id: str) -> AcceptanceManifest | None:
    """Load and digest-verify a persisted manifest; tampered payloads are None."""
    target = manifest_path(trw_root, prd_id)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        # strict=False: use_enum_values stores wire strings; the canonical
        # digest check below is the integrity gate, not parse strictness.
        manifest = AcceptanceManifest.model_validate(data["manifest"], strict=False)
        # PRD-QUAL-120-FR05: ONE manifest schema — a foreign version is a typed
        # absence requiring migration, never a best-effort read.
        from trw_mcp.models.requirements import ACCEPTANCE_MANIFEST_SCHEMA_VERSION

        if manifest.schema_version != ACCEPTANCE_MANIFEST_SCHEMA_VERSION:
            logger.warning(
                "acceptance_manifest_schema_mismatch",
                prd_id=prd_id,
                found=manifest.schema_version,
            )
            return None
        if manifest.canonical_digest() != str(data.get("manifest_digest", "")):
            logger.warning("acceptance_manifest_digest_mismatch", prd_id=prd_id)
            return None
        return manifest
    except Exception:  # justified: unreadable manifest is a typed absence, never a pass
        logger.warning("acceptance_manifest_unreadable", prd_id=prd_id, exc_info=True)
        return None


@dataclass(frozen=True, slots=True)
class RegistryAcceptanceView:
    """READ-ONLY adapter the executable registry consumes (FR03).

    Deliberately a frozen value object with no persistence methods — the
    registry and projections read acceptance state; they never write it.
    """

    prd_id: str
    accepted_count: int
    blocked_count: int
    unknown_count: int
    completion_outcome: str
    manifest_digest: str


def registry_view(manifest: AcceptanceManifest) -> RegistryAcceptanceView:
    """Project the manifest into the registry's read-only input."""
    states = [str(requirement.state) for requirement in manifest.requirements]
    return RegistryAcceptanceView(
        prd_id=manifest.prd_id,
        accepted_count=states.count("accepted"),
        blocked_count=states.count("blocked"),
        unknown_count=states.count("unknown"),
        completion_outcome=manifest.completion_outcome,
        manifest_digest=manifest.canonical_digest(),
    )
