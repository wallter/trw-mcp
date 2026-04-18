"""Unit tests for trw_mcp.security.mcp_registry (PRD-INFRA-SEC-001 FR-1 / FR-8)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.security.mcp_registry import (
    MCPAllowlist,
    MCPServer,
    is_allowed,
    load_allowlist,
    verify_signature,
)


def _write_allowlist(path: Path, servers: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump({"version": 1, "servers": servers}))


@pytest.fixture
def canonical_path(tmp_path: Path) -> Path:
    p = tmp_path / "canonical.yaml"
    _write_allowlist(
        p,
        [
            {
                "name": "trw-mcp",
                "url_or_command": "stdio://trw-mcp",
                "signer": "trw-maintainer",
                "signature": "sha256:aaa",
                "trust_level": "verified",
                "capabilities": ["trw_session_start", "trw_learn"],
            },
            {
                "name": "context7",
                "url_or_command": "stdio://context7",
                "signer": "trw-maintainer",
                "signature": "sha256:bbb",
                "trust_level": "verified",
                "capabilities": ["resolve-library-id"],
            },
        ],
    )
    return p


def test_load_allowlist_from_default_only(canonical_path: Path) -> None:
    allowlist = load_allowlist(canonical_path)
    assert isinstance(allowlist, MCPAllowlist)
    assert {s.name for s in allowlist.servers} == {"trw-mcp", "context7"}
    assert all(s.trust_level == "verified" for s in allowlist.servers)


def test_load_allowlist_merges_operator_overlay_additions(
    canonical_path: Path, tmp_path: Path
) -> None:
    overlay = tmp_path / "overlay.yaml"
    _write_allowlist(
        overlay,
        [
            {
                "name": "internal-tool",
                "url_or_command": "stdio://internal",
                "signer": "operator",
                "signature": "sha256:opsig",
                "trust_level": "operator",
                "capabilities": ["do_thing"],
            }
        ],
    )
    allowlist = load_allowlist(canonical_path, overlay)
    names = {s.name for s in allowlist.servers}
    assert names == {"trw-mcp", "context7", "internal-tool"}
    added = allowlist.by_name("internal-tool")
    assert added is not None
    assert added.trust_level == "operator"


def test_load_allowlist_rejects_overlay_attempts_to_relax_trust_level(
    canonical_path: Path,
    tmp_path: Path,
    captured_structlog: list[dict[str, object]],
) -> None:
    overlay = tmp_path / "overlay.yaml"
    _write_allowlist(
        overlay,
        [
            {
                "name": "trw-mcp",
                "url_or_command": "stdio://evil",
                "signer": "operator",
                "signature": "sha256:weaker",
                "trust_level": "operator",
                "capabilities": ["exec_shell"],
            }
        ],
    )
    allowlist = load_allowlist(canonical_path, overlay)

    entry = allowlist.by_name("trw-mcp")
    assert entry is not None
    # Canonical entry preserved: trust_level still "verified", not downgraded.
    assert entry.trust_level == "verified"
    assert entry.url_or_command == "stdio://trw-mcp"
    assert any(
        e.get("event") == "mcp_allowlist_overlay_downgrade_rejected"
        for e in captured_structlog
    )


def test_verify_signature_observe_mode_always_true_with_log(
    captured_structlog: list[dict[str, object]],
) -> None:
    server = MCPServer(
        name="trw-mcp",
        url_or_command="stdio://trw-mcp",
        signer="trw-maintainer",
        signature="",
        trust_level="verified",
    )
    assert verify_signature(server) is True
    events = [e for e in captured_structlog if e.get("event") == "mcp_signature_verify"]
    assert events, "expected mcp_signature_verify structlog event"
    assert events[0].get("mode") == "observe"


def test_is_allowed_returns_false_for_unknown_server(canonical_path: Path) -> None:
    allowlist = load_allowlist(canonical_path)
    assert is_allowed("nonexistent-server", allowlist) is False


def test_is_allowed_returns_true_for_default_allowlist_entry(
    canonical_path: Path,
) -> None:
    allowlist = load_allowlist(canonical_path)
    assert is_allowed("trw-mcp", allowlist) is True
    assert is_allowed("context7", allowlist) is True
