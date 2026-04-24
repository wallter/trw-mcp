"""Signed MCP registry loading, authorization, and quarantine state."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = structlog.get_logger(__name__)

ALL_PHASES: tuple[str, ...] = (
    "research",
    "plan",
    "implement",
    "validate",
    "review",
    "deliver",
)
ALL_SCOPES: tuple[str, ...] = ("read", "write", "execute")


class MCPSecurityError(RuntimeError):
    """Base error for MCP security failures."""


class MCPSecurityUnavailableError(MCPSecurityError):
    """Raised when the cryptographic verification dependency is unavailable."""


class MCPSecurityConfigError(MCPSecurityError):
    """Raised when the registry or overlay configuration is invalid."""


class AllowedTool(BaseModel):
    """Per-tool authorization entry from the PRD allowlist schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    allowed_phases: tuple[str, ...] = Field(default_factory=lambda: ALL_PHASES)
    allowed_scopes: tuple[str, ...] = Field(default_factory=lambda: ALL_SCOPES)


class RegistrySignatureBlock(BaseModel):
    """Detached signature metadata for a registry file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: Literal["ed25519"] = "ed25519"
    signed_at: str
    signer_fingerprint: str
    signature: str


class MCPServer(BaseModel):
    """Authorized MCP server entry."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str
    url_or_command: str
    public_key_fingerprint: str
    allowed_tools: tuple[AllowedTool, ...] = Field(default_factory=tuple)
    source_tier: Literal["canonical", "overlay"] = "canonical"

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_capabilities(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        upgraded = dict(value)
        if "public_key_fingerprint" not in upgraded:
            upgraded["public_key_fingerprint"] = _descriptor_fingerprint(
                str(upgraded.get("url_or_command", upgraded.get("name", "")))
            )
        if "allowed_tools" in value:
            return upgraded
        capabilities = upgraded.get("capabilities")
        if not isinstance(capabilities, list):
            return upgraded
        upgraded["allowed_tools"] = [
            {
                "name": str(tool_name),
                "allowed_phases": list(ALL_PHASES),
                "allowed_scopes": list(ALL_SCOPES),
            }
            for tool_name in capabilities
        ]
        return upgraded

    def tool_names(self) -> set[str]:
        return {tool.name for tool in self.allowed_tools}

    def tool_by_name(self, tool_name: str) -> AllowedTool | None:
        for tool in self.allowed_tools:
            if tool.name == tool_name:
                return tool
        return None


class MCPAllowlist(BaseModel):
    """Resolved allowlist after canonical + optional overlay merge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = 1
    signing_algorithm: Literal["ed25519"] = "ed25519"
    servers: tuple[MCPServer, ...] = Field(default_factory=tuple)
    signature_block: RegistrySignatureBlock | None = None
    allowlist_hash: str = ""

    def by_name(self, name: str) -> MCPServer | None:
        for server in self.servers:
            if server.name == name:
                return server
        return None

    def by_fingerprint(self, fingerprint: str) -> MCPServer | None:
        for server in self.servers:
            if server.public_key_fingerprint == fingerprint:
                return server
        return None


class RegistryDecision(BaseModel):
    """Authorization result for a server identity check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: str = ""
    match_type: Literal["canonical", "overlay", "unsigned_admission", "quarantined", "missing"]
    entry: MCPServer | None = None
    drift_detected: bool = False
    quarantine_reason: str = ""


def _load_crypto() -> tuple[type[object], type[object]]:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )

        return Ed25519PublicKey, Ed25519PrivateKey
    except ImportError as exc:  # pragma: no cover - exercised via startup tests
        raise MCPSecurityUnavailableError(
            "cryptography>=42 is required for MCP registry signature verification"
        ) from exc


def _descriptor_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonicalize_registry_payload(payload: dict[str, object]) -> bytes:
    to_sign = dict(payload)
    to_sign.pop("signature_block", None)
    return json.dumps(to_sign, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _load_public_key(path: Path) -> tuple[object, str]:
    public_key_type, _ = _load_crypto()
    raw = path.read_bytes()
    if len(raw) != 32:
        stripped = raw.strip()
        try:
            raw = base64.b64decode(stripped, validate=True)
        except ValueError:
            raw = stripped
    try:
        public_key = public_key_type.from_public_bytes(raw)  # type: ignore[attr-defined]
    except ValueError as exc:
        raise MCPSecurityConfigError(f"invalid Ed25519 public key at {path}") from exc
    return public_key, "sha256:" + hashlib.sha256(raw).hexdigest()


def _verify_registry_signature(payload: dict[str, object], *, public_key_path: Path) -> None:
    signature_block_raw = payload.get("signature_block")
    if not isinstance(signature_block_raw, dict):
        raise MCPSecurityConfigError("registry signature_block is required")
    signature_block = RegistrySignatureBlock.model_validate(signature_block_raw)
    public_key, computed_fingerprint = _load_public_key(public_key_path)
    if signature_block.signer_fingerprint != computed_fingerprint:
        raise MCPSecurityConfigError("registry signer fingerprint mismatch")
    try:
        signature = base64.b64decode(signature_block.signature.encode("ascii"), validate=True)
    except ValueError as exc:
        raise MCPSecurityConfigError("registry signature is not valid base64") from exc
    try:
        public_key.verify(signature, canonicalize_registry_payload(payload))  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - exact crypto error type is library-specific
        raise MCPSecurityConfigError("registry signature verification failed") from exc


def _parse_allowlist_file(path: Path, *, public_key_path: Path) -> MCPAllowlist:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except OSError as exc:
        raise MCPSecurityConfigError(f"unable to read allowlist {path}") from exc
    if not isinstance(raw, dict):
        raise MCPSecurityConfigError(f"allowlist {path} must be a YAML mapping")
    _verify_registry_signature(raw, public_key_path=public_key_path)
    servers = tuple(MCPServer.model_validate(item) for item in raw.get("servers", []))
    return MCPAllowlist(
        version=int(raw.get("version", 1)),
        signing_algorithm="ed25519",
        servers=servers,
        signature_block=RegistrySignatureBlock.model_validate(raw["signature_block"]),
        allowlist_hash="sha256:" + hashlib.sha256(canonicalize_registry_payload(raw)).hexdigest(),
    )


def _overlay_is_not_weaker(canonical: MCPServer, overlay: MCPServer) -> bool:
    if overlay.public_key_fingerprint != canonical.public_key_fingerprint:
        return False
    canonical_tools = {tool.name: tool for tool in canonical.allowed_tools}
    for tool in overlay.allowed_tools:
        original = canonical_tools.get(tool.name)
        if original is None:
            return False
        if not set(tool.allowed_phases).issubset(set(original.allowed_phases)):
            return False
        if not set(tool.allowed_scopes).issubset(set(original.allowed_scopes)):
            return False
    return True


def _merge_allowlists(canonical: MCPAllowlist, overlay: MCPAllowlist) -> MCPAllowlist:
    merged: dict[str, MCPServer] = {server.name: server for server in canonical.servers}
    for overlay_entry in overlay.servers:
        existing = merged.get(overlay_entry.name)
        if existing is None:
            merged[overlay_entry.name] = overlay_entry.model_copy(update={"source_tier": "overlay"})
            continue
        if not _overlay_is_not_weaker(existing, overlay_entry):
            raise MCPSecurityConfigError(
                f"overlay entry {overlay_entry.name!r} weakens canonical authorization"
            )
        merged[overlay_entry.name] = overlay_entry.model_copy(update={"source_tier": "overlay"})
    payload = {
        "version": canonical.version,
        "signing_algorithm": canonical.signing_algorithm,
        "servers": [server.model_dump(mode="python") for server in merged.values()],
    }
    return MCPAllowlist(
        version=canonical.version,
        signing_algorithm=canonical.signing_algorithm,
        servers=tuple(merged.values()),
        signature_block=canonical.signature_block,
        allowlist_hash="sha256:" + hashlib.sha256(canonicalize_registry_payload(payload)).hexdigest(),
    )


class MCPRegistry:
    """Runtime registry wrapper with authorization and quarantine state."""

    def __init__(self, *, allowlist: MCPAllowlist, allow_unsigned: bool = False) -> None:
        self.allowlist = allowlist
        self.allow_unsigned = allow_unsigned
        self._observed_fingerprints: dict[str, str] = {}
        self._quarantine_reasons: dict[str, str] = {}

    @classmethod
    def from_allowlist(cls, allowlist: MCPAllowlist, *, allow_unsigned: bool = False) -> MCPRegistry:
        return cls(allowlist=allowlist, allow_unsigned=allow_unsigned)

    @classmethod
    def load(
        cls,
        *,
        canonical_path: Path,
        canonical_public_key_path: Path,
        overlay_path: Path | None = None,
        operator_public_key_path: Path | None = None,
        allow_unsigned: bool = False,
    ) -> MCPRegistry:
        canonical = _parse_allowlist_file(canonical_path, public_key_path=canonical_public_key_path)
        resolved = canonical
        if overlay_path is not None and overlay_path.exists():
            if operator_public_key_path is None:
                raise MCPSecurityConfigError("operator overlay requires operator public key")
            overlay = _parse_allowlist_file(overlay_path, public_key_path=operator_public_key_path)
            resolved = _merge_allowlists(canonical, overlay)
        logger.info(
            "mcp_registry_loaded",
            path=str(canonical_path),
            overlay=str(overlay_path) if overlay_path else "",
            servers=len(resolved.servers),
            outcome="loaded",
        )
        return cls(allowlist=resolved, allow_unsigned=allow_unsigned)

    @property
    def registered_servers(self) -> list[str]:
        return [server.name for server in self.allowlist.servers]

    @property
    def quarantined_servers(self) -> list[str]:
        return sorted(self._quarantine_reasons)

    @property
    def allowlist_hash(self) -> str:
        return self.allowlist.allowlist_hash

    def quarantine(self, server_name: str, *, reason: str) -> None:
        self._quarantine_reasons[server_name] = reason
        logger.warning(
            "mcp_server_quarantined",
            server=server_name,
            reason=reason,
            outcome="quarantined",
        )

    def release_quarantine(self, server_name: str) -> None:
        self._quarantine_reasons.pop(server_name, None)

    def authorize_server(
        self,
        server_name: str,
        *,
        observed_fingerprint: str | None = None,
        auto_release: bool = False,
    ) -> RegistryDecision:
        entry = self.allowlist.by_name(server_name)
        if entry is None and observed_fingerprint:
            entry = self.allowlist.by_fingerprint(observed_fingerprint)
        effective_server_name = entry.name if entry is not None else server_name

        quarantine_reason = self._quarantine_reasons.get(effective_server_name)
        if quarantine_reason:
            if (
                auto_release
                and entry is not None
                and observed_fingerprint
                and observed_fingerprint == entry.public_key_fingerprint
            ):
                self.release_quarantine(entry.name)
                self._observed_fingerprints[entry.name] = observed_fingerprint
                quarantine_reason = None
            else:
                return RegistryDecision(
                    allowed=False,
                    reason=quarantine_reason,
                    match_type="quarantined",
                    entry=entry,
                    quarantine_reason=quarantine_reason,
                )
        if quarantine_reason:
            return RegistryDecision(
                allowed=False,
                reason=quarantine_reason,
                match_type="quarantined",
                entry=entry,
                quarantine_reason=quarantine_reason,
            )

        if entry is None:
            if self.allow_unsigned:
                return RegistryDecision(
                    allowed=True,
                    reason="unsigned_admission",
                    match_type="unsigned_admission",
                )
            return RegistryDecision(
                allowed=False,
                reason="server_not_in_allowlist",
                match_type="missing",
            )

        if observed_fingerprint:
            previous = self._observed_fingerprints.get(entry.name)
            if previous is None:
                self._observed_fingerprints[entry.name] = observed_fingerprint
            elif previous != observed_fingerprint:
                self.quarantine(entry.name, reason="signature_drift")
                return RegistryDecision(
                    allowed=False,
                    reason="signature_drift",
                    match_type="quarantined",
                    entry=entry,
                    drift_detected=True,
                    quarantine_reason="signature_drift",
                )
            if observed_fingerprint != entry.public_key_fingerprint:
                self.quarantine(entry.name, reason="signature_drift")
                return RegistryDecision(
                    allowed=False,
                    reason="signature_drift",
                    match_type="quarantined",
                    entry=entry,
                    drift_detected=True,
                    quarantine_reason="signature_drift",
                )

        return RegistryDecision(
            allowed=True,
            match_type=entry.source_tier,
            entry=entry,
        )


def bundled_allowlist_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "mcp_servers.allowlist.yaml"


def bundled_public_key_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "mcp_registry_ed25519.pub"


def load_allowlist(
    default_path: Path,
    overlay_path: Path | None = None,
    *,
    canonical_public_key_path: Path | None = None,
    operator_public_key_path: Path | None = None,
    allow_unsigned: bool = False,
) -> MCPAllowlist:
    registry = MCPRegistry.load(
        canonical_path=default_path,
        canonical_public_key_path=canonical_public_key_path or bundled_public_key_path(),
        overlay_path=overlay_path,
        operator_public_key_path=operator_public_key_path,
        allow_unsigned=allow_unsigned,
    )
    return registry.allowlist


def verify_signature(server: MCPServer) -> bool:
    """Compatibility helper for structural signature contract checks."""

    return server.public_key_fingerprint.startswith("sha256:")


def is_allowed(server_name: str, allowlist: MCPAllowlist) -> bool:
    """Compatibility helper returning whether a server is allowlisted."""

    return allowlist.by_name(server_name) is not None


__all__ = [
    "ALL_PHASES",
    "ALL_SCOPES",
    "AllowedTool",
    "MCPAllowlist",
    "MCPRegistry",
    "MCPSecurityConfigError",
    "MCPSecurityError",
    "MCPSecurityUnavailableError",
    "MCPServer",
    "RegistryDecision",
    "bundled_allowlist_path",
    "bundled_public_key_path",
    "canonicalize_registry_payload",
    "is_allowed",
    "load_allowlist",
    "verify_signature",
]
