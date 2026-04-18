"""MCP server registry, allowlist loader, and signature verification.

Implements PRD-INFRA-SEC-001 FR-1 (signed registry), FR-5 (signature-drift
scaffolding), and FR-8 (two-tier signing authority: TRW-maintainer canonical
allowlist + optional operator overlay).

Signature verification is an **observe-mode stub** in v1: the function logs
its decision and returns ``True`` unconditionally. A subsequent wave wires in
the cryptographic Ed25519 verify path; shipping observe-mode first lets us
instrument the call site without gating tool exposure before thresholds are
calibrated (PRD §8 Rollout Phase 1).

Overlay semantics (FR-8): the operator overlay MAY add new servers but MUST
NOT downgrade the ``trust_level`` of an entry present in the canonical
allowlist. ``load_allowlist`` enforces this by ignoring (and logging) any
overlay entry that collides with a canonical name at a weaker trust level.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

TrustLevel = Literal["verified", "operator", "unsigned"]

_TRUST_RANK: dict[str, int] = {
    "verified": 2,
    "operator": 1,
    "unsigned": 0,
}


class MCPServer(BaseModel):
    """A single MCP server entry in the allowlist.

    Fields mirror the minimal PRD-INFRA-SEC-001 §13.1 schema needed for
    FR-1 / FR-2 / FR-8. Cryptographic material is opaque to this module in v1
    (observe-mode); ``signature`` is carried through so later waves can
    validate without schema churn.
    """

    name: str = Field(..., description="Unique MCP server identifier.")
    url_or_command: str = Field(
        ...,
        description="Connection string — stdio command or HTTP URL.",
    )
    signer: str = Field(
        ...,
        description="Signing authority identity (e.g. 'trw-maintainer').",
    )
    signature: str = Field(
        default="",
        description="Detached signature over the entry (empty in observe mode).",
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="Tool names this server is authorized to expose.",
    )
    trust_level: TrustLevel = Field(
        default="unsigned",
        description="Trust tier: verified (Tier 1), operator (Tier 2), unsigned.",
    )


class MCPAllowlist(BaseModel):
    """A resolved allowlist — canonical plus any applied operator overlay."""

    servers: list[MCPServer] = Field(default_factory=list)

    def by_name(self, name: str) -> MCPServer | None:
        for server in self.servers:
            if server.name == name:
                return server
        return None


def _parse_allowlist_file(path: Path) -> list[MCPServer]:
    """Parse a YAML allowlist file into ``MCPServer`` entries."""

    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"allowlist {path} must be a YAML mapping at the root")
    servers_raw = raw.get("servers", [])
    if not isinstance(servers_raw, list):
        raise ValueError(f"allowlist {path} 'servers' must be a list")
    return [MCPServer.model_validate(item) for item in servers_raw]


def load_allowlist(
    default_path: Path,
    overlay_path: Path | None = None,
) -> MCPAllowlist:
    """Load the canonical allowlist and optionally merge an operator overlay.

    Overlay rules (FR-8):
      * Overlay MAY add new servers not in the canonical allowlist.
      * Overlay MAY NOT downgrade ``trust_level`` of a canonical entry.
        Collisions at a weaker trust level are dropped with a structured log.
      * Collisions at equal-or-greater trust level replace the canonical entry
        (operators may tighten, not relax).
    """

    canonical = _parse_allowlist_file(default_path)
    logger.info(
        "mcp_allowlist_loaded",
        tier="canonical",
        path=str(default_path),
        count=len(canonical),
    )

    if overlay_path is None or not overlay_path.exists():
        return MCPAllowlist(servers=canonical)

    overlay = _parse_allowlist_file(overlay_path)
    logger.info(
        "mcp_allowlist_loaded",
        tier="operator_overlay",
        path=str(overlay_path),
        count=len(overlay),
    )

    merged: dict[str, MCPServer] = {s.name: s for s in canonical}
    for entry in overlay:
        existing = merged.get(entry.name)
        if existing is None:
            merged[entry.name] = entry
            continue
        incoming_rank = _TRUST_RANK.get(entry.trust_level, 0)
        existing_rank = _TRUST_RANK.get(existing.trust_level, 0)
        if incoming_rank < existing_rank:
            logger.warning(
                "mcp_allowlist_overlay_downgrade_rejected",
                server=entry.name,
                canonical_trust=existing.trust_level,
                overlay_trust=entry.trust_level,
                outcome="rejected",
            )
            continue
        merged[entry.name] = entry

    return MCPAllowlist(servers=list(merged.values()))


def verify_signature(server: MCPServer) -> bool:
    """Observe-mode signature verification stub (FR-1 / FR-5 v1).

    Always returns ``True`` and emits a structured log. Real Ed25519 verify
    lands in a later wave once observe-mode baselines are collected per PRD
    §8 Rollout Phase 1.
    """

    logger.info(
        "mcp_signature_verify",
        server=server.name,
        signer=server.signer,
        trust_level=server.trust_level,
        mode="observe",
        outcome="accepted",
    )
    return True


def is_allowed(server_name: str, allowlist: MCPAllowlist) -> bool:
    """Return ``True`` iff ``server_name`` is present in the resolved allowlist."""

    match = allowlist.by_name(server_name)
    outcome = "allowed" if match is not None else "denied"
    logger.info(
        "mcp_is_allowed_decision",
        server=server_name,
        outcome=outcome,
    )
    return match is not None
