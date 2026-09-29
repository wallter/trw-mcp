"""Header, digest manifest and canonical rendering of a pack (PRD-CORE-323 FR05, FR07, NFR02).

The manifest carries a sha256 per section over the section's canonical bytes and
a ``pack_digest`` over the canonical header plus those section digests. Both use
the receipts' canonical serializer (``models/_evidence_core.canonical_json``:
sorted keys, compact separators, UTF-8), so the same logical pack always renders
to the same bytes. The header carries no wall-clock timestamp.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version

from trw_mcp.evidence_pack._redaction import JsonValue
from trw_mcp.evidence_pack._wording import POSTURE_LINE, REDACTION_LIMIT_LINE, SCHEMA, VERIFY_LIMIT_LINE
from trw_mcp.models._evidence_core import canonical_json

#: NFR02: a pack that would exceed this many bytes is refused and nothing is written.
MAX_PACK_BYTES = 8 * 1024 * 1024

#: Section order is fixed; the manifest lists every one, built or not.
SECTION_NAMES: tuple[str, ...] = ("requirements", "decisions", "evidence", "verdict")


class PackRefusedError(Exception):
    """The export was refused; ``reason`` is the named token printed on stderr (exit 2)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _trw_mcp_version() -> str:
    try:
        return version("trw-mcp")
    except PackageNotFoundError:  # trw-fail-silent-allow: an uninstalled source tree has no version to report
        return "unknown"


def build_header(run_identity: str, head_commit: str | None) -> dict[str, JsonValue]:
    """The pack header: schema, run identity, HEAD or null, trw-mcp version and the three fixed lines.

    Runtime caller: ``_pack.build_pack``.
    """
    return {
        "schema": SCHEMA,
        "run_identity": run_identity,
        "head_commit": head_commit,
        "trw_mcp_version": _trw_mcp_version(),
        "posture": POSTURE_LINE,
        "redaction_limit": REDACTION_LIMIT_LINE,
        "verify_limit": VERIFY_LIMIT_LINE,
    }


def _sha256(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def render_pack(header: Mapping[str, JsonValue], sections: Mapping[str, JsonValue]) -> bytes:
    """Seal the sections into a manifest and render the whole pack as canonical JSON bytes.

    Runtime caller: ``_pack.build_pack``. Raises :class:`PackRefusedError` with
    ``pack_too_large`` when the rendered bytes exceed :data:`MAX_PACK_BYTES`, before
    anything is written. Soundness scope: the digests prove the pack is internally
    consistent; anyone can edit a pack and recompute them (FR07 soundness scope).
    """
    section_digests = {name: _sha256(sections[name]) for name in SECTION_NAMES}
    manifest: dict[str, JsonValue] = {
        "section_digests": dict(section_digests),
        "pack_digest": _sha256({"header": dict(header), "section_digests": section_digests}),
    }
    pack = {"header": dict(header), "sections": dict(sections), "manifest": manifest}
    rendered = canonical_json(pack) + b"\n"
    if len(rendered) > MAX_PACK_BYTES:
        raise PackRefusedError("pack_too_large", f"{len(rendered)} bytes exceeds {MAX_PACK_BYTES}")
    return rendered
