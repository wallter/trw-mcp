"""Unit tests for the signed MCP registry."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trw_mcp.security.mcp_registry import MCPRegistry, verify_signature


def _public_key_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _fingerprint(raw_public_key: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw_public_key).hexdigest()


def _write_signed_allowlist(path: Path, public_key_path: Path) -> None:
    private_key = Ed25519PrivateKey.generate()
    raw_public_key = _public_key_bytes(private_key)
    public_key_path.write_bytes(raw_public_key)
    payload = {
        "version": 1,
        "signing_algorithm": "ed25519",
        "servers": [
            {
                "name": "trw",
                "url_or_command": "trw-mcp",
                "public_key_fingerprint": "sha256:trw",
                "allowed_tools": [
                    {
                        "name": "trw_recall",
                        "allowed_phases": ["implement"],
                        "allowed_scopes": ["read"],
                    }
                ],
            }
        ],
    }
    signature = private_key.sign(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    payload["signature_block"] = {
        "algorithm": "ed25519",
        "signed_at": "2026-04-24T00:00:00Z",
        "signer_fingerprint": _fingerprint(raw_public_key),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def test_load_allowlist_from_signed_canonical(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "allowlist.yaml"
    public_key_path = tmp_path / "maintainer.pub"
    _write_signed_allowlist(allowlist_path, public_key_path)

    registry = MCPRegistry.load(
        canonical_path=allowlist_path,
        canonical_public_key_path=public_key_path,
    )

    assert registry.registered_servers == ["trw"]
    assert registry.allowlist.by_name("trw") is not None


def test_verify_signature_helper_requires_fingerprint_shape() -> None:
    from trw_mcp.security.mcp_registry import MCPServer

    assert verify_signature(MCPServer(name="trw", url_or_command="trw-mcp"))


def test_unsigned_server_denied_by_default(tmp_path: Path) -> None:
    allowlist_path = tmp_path / "allowlist.yaml"
    public_key_path = tmp_path / "maintainer.pub"
    _write_signed_allowlist(allowlist_path, public_key_path)
    registry = MCPRegistry.load(
        canonical_path=allowlist_path,
        canonical_public_key_path=public_key_path,
    )

    decision = registry.authorize_server("ghost")

    assert decision.allowed is False
    assert decision.reason == "server_not_in_allowlist"
