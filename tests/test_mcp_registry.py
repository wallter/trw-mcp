"""Tests for signed MCP registry loading and authorization."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trw_mcp.security.mcp_registry import (
    AllowedTool,
    MCPRegistry,
    MCPSecurityConfigError,
)


def _canonical_bytes(payload: dict[str, object]) -> bytes:
    to_sign = dict(payload)
    to_sign.pop("signature_block", None)
    return json.dumps(to_sign, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign_payload(
    path: Path,
    *,
    private_key: Ed25519PrivateKey,
    signer_fingerprint: str,
    servers: list[dict[str, object]],
) -> None:
    payload: dict[str, object] = {
        "version": 1,
        "signing_algorithm": "ed25519",
        "servers": servers,
    }
    signature = private_key.sign(_canonical_bytes(payload))
    payload["signature_block"] = {
        "algorithm": "ed25519",
        "signed_at": "2026-04-24T00:00:00Z",
        "signer_fingerprint": signer_fingerprint,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _fingerprint(raw_public_key: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw_public_key).hexdigest()


def test_signed_allowlist_rejects_tampered_payload(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_public_key = _public_key_bytes(private_key)
    public_key_path = tmp_path / "maintainer.pub"
    public_key_path.write_bytes(raw_public_key)
    allowlist_path = tmp_path / "allowlist.yaml"
    _sign_payload(
        allowlist_path,
        private_key=private_key,
        signer_fingerprint=_fingerprint(raw_public_key),
        servers=[
            {
                "name": "trw",
                "url_or_command": "trw-mcp",
                "public_key_fingerprint": "sha256:server-trw",
                "allowed_tools": [
                    {
                        "name": "trw_recall",
                        "allowed_phases": ["implement"],
                        "allowed_scopes": ["read"],
                    }
                ],
            }
        ],
    )
    parsed = yaml.safe_load(allowlist_path.read_text())
    parsed["servers"][0]["allowed_tools"][0]["name"] = "exec_shell"
    allowlist_path.write_text(yaml.safe_dump(parsed, sort_keys=False))

    with pytest.raises(MCPSecurityConfigError, match="signature"):
        MCPRegistry.load(
            canonical_path=allowlist_path,
            canonical_public_key_path=public_key_path,
        )


def test_authorize_server_respects_allow_unsigned_flag(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_public_key = _public_key_bytes(private_key)
    public_key_path = tmp_path / "maintainer.pub"
    public_key_path.write_bytes(raw_public_key)
    allowlist_path = tmp_path / "allowlist.yaml"
    _sign_payload(
        allowlist_path,
        private_key=private_key,
        signer_fingerprint=_fingerprint(raw_public_key),
        servers=[
            {
                "name": "trw",
                "url_or_command": "trw-mcp",
                "public_key_fingerprint": "sha256:server-trw",
                "allowed_tools": [
                    {
                        "name": "trw_recall",
                        "allowed_phases": ["implement"],
                        "allowed_scopes": ["read"],
                    }
                ],
            }
        ],
    )
    registry = MCPRegistry.load(
        canonical_path=allowlist_path,
        canonical_public_key_path=public_key_path,
        allow_unsigned=True,
    )

    decision = registry.authorize_server("ghost")

    assert decision.allowed is True
    assert decision.match_type == "unsigned_admission"
    assert decision.entry is None


def test_signature_drift_quarantines_and_blocks_subsequent_calls(tmp_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_public_key = _public_key_bytes(private_key)
    public_key_path = tmp_path / "maintainer.pub"
    public_key_path.write_bytes(raw_public_key)
    allowlist_path = tmp_path / "allowlist.yaml"
    _sign_payload(
        allowlist_path,
        private_key=private_key,
        signer_fingerprint=_fingerprint(raw_public_key),
        servers=[
            {
                "name": "filesystem",
                "url_or_command": "npx -y @modelcontextprotocol/server-filesystem",
                "public_key_fingerprint": "sha256:expected-filesystem",
                "allowed_tools": [
                    {
                        "name": "read_file",
                        "allowed_phases": ["implement"],
                        "allowed_scopes": ["read"],
                    }
                ],
            }
        ],
    )
    registry = MCPRegistry.load(
        canonical_path=allowlist_path,
        canonical_public_key_path=public_key_path,
    )

    first = registry.authorize_server(
        "filesystem",
        observed_fingerprint="sha256:expected-filesystem",
    )
    second = registry.authorize_server(
        "filesystem",
        observed_fingerprint="sha256:drifted-filesystem",
    )
    blocked = registry.authorize_server(
        "filesystem",
        observed_fingerprint="sha256:expected-filesystem",
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.drift_detected is True
    assert "filesystem" in registry.quarantined_servers
    assert blocked.allowed is False
    assert blocked.quarantine_reason == "signature_drift"


def test_allowed_tool_model_parses_prd_shape() -> None:
    tool = AllowedTool(
        name="read_file",
        allowed_phases=["research", "implement"],
        allowed_scopes=["read"],
    )

    assert tool.name == "read_file"
    assert tool.allowed_phases == ("research", "implement")
    assert tool.allowed_scopes == ("read",)
