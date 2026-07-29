"""R3: operator-minted, single-use break-glass tokens (never agent-suppliable).

Honest limit (Non-Goals / OQ8 / RISK-005): this is a convention-plus-audit-trail
control — a fixed path, mode 0600, human-created, expiring, single-use file — NOT
a cryptographic capability. An agent with unrestricted ``Bash`` could fabricate
one; FR03 still ledgers the resulting override, so the audit trail survives even
when the gate does not. What this DOES close is codex #7: break-glass can no
longer be self-supplied through the tool-call payload the agent controls.

Minting is a human terminal action (``python3 -m
trw_mcp.security.intent_contract.break_glass mint ...``); no MCP tool wraps it.

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path

from trw_mcp.security.intent_contract.paths import BREAK_GLASS_DIR, read_bytes_nofollow

__all__ = ["break_glass_dir", "consume_token", "mint_token"]

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def break_glass_dir(root: Path) -> Path:
    return root / BREAK_GLASS_DIR


def _slug(value: str) -> str:
    return _SAFE_ID.sub("_", value)[:64]


def mint_token(root: Path, *, claim_id: str, file_path: str, ttl_seconds: int, max_ttl_seconds: int) -> Path:
    """Create one token bound to *claim_id* + *file_path*. Operator-only."""
    if ttl_seconds < 1:
        raise ValueError("ttl_seconds must be positive")
    if ttl_seconds > max_ttl_seconds:
        raise ValueError(f"ttl_seconds exceeds break_glass_token_ttl_max_seconds ({max_ttl_seconds})")
    directory = break_glass_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(8)
    path = directory / f"{_slug(claim_id)}-{nonce}.token"
    payload = {
        "claim_id": claim_id,
        "file_path": file_path,
        "nonce": nonce,
        "expires_at": int(time.time()) + ttl_seconds,
    }
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, json.dumps(payload, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def _valid_token(path: Path, *, claim_id: str, file_path: str, now: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o077:
        # Symlinked or group/other-accessible: not an operator-minted 0600 token.
        return False
    try:
        payload = json.loads(read_bytes_nofollow(path).decode("utf-8", errors="strict"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    if str(payload.get("claim_id", "")) != claim_id or str(payload.get("file_path", "")) != file_path:
        return False
    expires_at = payload.get("expires_at")
    return isinstance(expires_at, int) and expires_at > now


def consume_token(root: Path, *, claim_id: str, file_path: str) -> str | None:
    """Consume ONE matching, unexpired token; return its filename, else ``None``.

    Consumption is destructive (single-use). An expired or mismatched token is
    treated exactly as absent — no override.
    """
    directory = break_glass_dir(root)
    if not directory.is_dir():
        return None
    now = int(time.time())
    for path in sorted(directory.glob(f"{_slug(claim_id)}-*.token")):
        if not _valid_token(path, claim_id=claim_id, file_path=file_path, now=now):
            continue
        try:
            path.unlink()
        except OSError:
            return None
        return path.name
    return None


def main(argv: list[str] | None = None) -> int:
    """``python3 -m trw_mcp.security.intent_contract.break_glass mint --claim-id ... [--root PATH]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 7 or args[0] != "mint":
        print(
            "usage: python3 -m trw_mcp.security.intent_contract.break_glass mint "
            "--claim-id ID --file PATH --ttl-seconds N [--root PATH]",
            file=sys.stderr,
        )
        return 2
    from trw_mcp.models.config._loader import get_config
    from trw_mcp.security.intent_contract.paths import repo_root

    options = dict(zip(args[1::2], args[2::2], strict=False))
    intent = get_config().security.intent
    root = Path(options["--root"]) if "--root" in options else repo_root()
    path = mint_token(
        root,
        claim_id=options.get("--claim-id", ""),
        file_path=options.get("--file", ""),
        ttl_seconds=int(options.get("--ttl-seconds", "0")),
        max_ttl_seconds=intent.break_glass_token_ttl_max_seconds,
    )
    print(f"minted: {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI entry
    raise SystemExit(main())
