"""Did the child's TRW MCP server actually start? (CODEX-P0-B-ZERO-TOOL, runner side)

Belongs to the ``_runner.py`` facade (split out for the 350-eLOC gate). A ``codex exec --json`` child whose
``mcp_servers.trw.command`` does not exist exits 0 with the same four events as a healthy run and nothing on stderr
(measured on codex-cli 0.159.0, 2026-09-30), so the argv-level ``posture_enforced`` / ``trw_access_enforced`` say
"connected" for a review that had no tools at all. Only the SERVER can prove it started: dispatch hands the child's server
a private receipt path and a per-run nonce through the same ``-c mcp_servers.trw.env.*`` channel that already carries
``TRW_SURFACE_ROLE``; the server writes ``{nonce, tools, ...}`` when it lists its tools (``middleware/handshake_receipt.py``, outermost
in the chain so it records what the client was shown); this module demands that receipt after the child exits.

Off by default (``TRW_DISPATCH_REQUIRE_HANDSHAKE=1`` turns it on) until both halves are on trunk and released: demanding a
receipt from an installed server that never writes one would fail every review. Codex without ``--pty`` only; any other launch shape
claims nothing (no layer is added and no reason is raised), never a guess.

The receipt lives in a private 0700 directory and is trusted only when it is a regular file, parses to an object and carries
this run's nonce. The required tools come from ``REVIEWER_TOOLS``, the one definition of the reviewer surface.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from trw_memory._tree_removal import remove_tree

from trw_mcp.dispatch._enforcement_layers import EnforcementFields
from trw_mcp.dispatch._handshake_names import NONCE_ENV, PATH_ENV, REQUIRE_ENV
from trw_mcp.dispatch._types import DispatchRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

NOT_STARTED = "trw_server_not_started"
_RECEIPT_CAP = 64 * 1024


@dataclass(frozen=True)
class Handshake:
    directory: Path
    nonce: str

    @property
    def path(self) -> Path:
        return self.directory / "receipt.json"


_ACTIVE_SERVER = re.compile(r"^mcp_servers\.(trw_dispatch_[0-9a-f]{32})\.command=")


def active_server(run_argv: Sequence[str]) -> str | None:
    """The generated table name of the ACTIVE ``-c mcp_servers.<name>.command=`` server, or ``None``.

    The renderer names the child's real server ``trw_dispatch_<uuid>`` and leaves ``mcp_servers.trw`` present but
    ``enabled=false`` (so a repo's own ``trw`` entry cannot shadow it). A receipt channel spliced under ``trw`` reaches the
    DISABLED table, the healthy server never sees it, and every proven review would read as a dead one.
    """
    return next((m.group(1) for arg in run_argv if (m := _ACTIVE_SERVER.match(arg))), None)


def wanted(req: DispatchRequest, run_argv: Sequence[str]) -> bool:
    """Whether this launch must prove its TRW server started (and can be given the receipt channel)."""
    from trw_mcp.dispatch._client_specs import client_spec_for

    # ``fresh_mcp_server_table``: the client takes the child's MCP server as ``-c mcp_servers.<name>.*`` overrides, the only
    # channel this module knows how to hand a receipt path through.
    if os.environ.get(REQUIRE_ENV) != "1" or req.use_pty or not client_spec_for(req.client).fresh_mcp_server_table:
        return False
    placed = bool(run_argv) and run_argv[-1] == req.prompt and active_server(run_argv) is not None
    return (req.posture == "reviewer" or req.with_trw) and placed


def begin(req: DispatchRequest, run_argv: list[str]) -> tuple[Handshake | None, list[str]]:
    """``(handshake, argv)``: *run_argv* with the receipt channel spliced in before the prompt, or unchanged."""
    if not wanted(req, run_argv):
        return None, run_argv
    server = active_server(run_argv)
    handshake = Handshake(Path(tempfile.mkdtemp(prefix="trw-handshake-")), secrets.token_hex(16))
    # json.dumps: a TOML basic string is a JSON string for the characters a temp path or a hex nonce can hold.
    extra = [
        "-c",
        f"mcp_servers.{server}.env.{PATH_ENV}={json.dumps(str(handshake.path))}",
        "-c",
        f"mcp_servers.{server}.env.{NONCE_ENV}={json.dumps(handshake.nonce)}",
    ]
    return handshake, [*run_argv[:-1], *extra, run_argv[-1]]


def discard(handshake: Handshake | None) -> None:
    if handshake is not None:
        remove_tree(handshake.directory, purpose="dispatch handshake receipt")


def _required(req: DispatchRequest) -> frozenset[str]:
    from trw_mcp.models.surface_packs import REVIEWER_TOOLS

    return REVIEWER_TOOLS if req.posture == "reviewer" else frozenset()


def proof(handshake: Handshake, required: frozenset[str]) -> tuple[bool, str]:
    """``(proved, why)``: the server's receipt for THIS run, listing the required tools."""
    path = handshake.path
    try:
        if path.is_symlink() or not path.is_file():
            return False, "no receipt: the child's TRW MCP server never reported listing its tools"
        with path.open("rb") as handle:
            raw = handle.read(_RECEIPT_CAP + 1)
        receipt = json.loads(raw) if len(raw) <= _RECEIPT_CAP else None
    except (
        OSError,
        ValueError,
    ):  # trw-fail-silent-allow: an unreadable receipt is reported as no proof, which fails the review
        return False, "receipt unreadable"
    if not isinstance(receipt, dict):
        return False, "receipt is not an object"
    if not hmac.compare_digest(str(receipt.get("nonce", "")).encode("utf-8"), handshake.nonce.encode("utf-8")):
        return False, "receipt carries another run's nonce"
    tools = receipt.get("tools")
    listed = {t for t in tools if isinstance(t, str)} if isinstance(tools, list) else set()
    if not listed:
        return False, "the server listed no tools"
    missing = sorted(required - listed)
    return (False, f"the server did not list the review tools: {', '.join(missing)}") if missing else (True, "")


def settle(
    handshake: Handshake | None, req: DispatchRequest, silence_reason: str | None, enforcement: EnforcementFields
) -> tuple[str | None, EnforcementFields]:
    """Fold the receipt into ``(silence_reason, enforcement fields)``; a no-op when none was demanded."""
    if handshake is None:
        return silence_reason, enforcement
    proved, why = proof(handshake, _required(req))
    discard(handshake)
    if proved:
        return silence_reason, {
            **enforcement,
            "enforcement_layers": (*enforcement["enforcement_layers"], "mcp_started"),
        }
    note = f"TRW MCP server start NOT proven: {why}"
    joined = f"{enforcement['mcp_role_note']}; {note}" if enforcement["mcp_role_note"] else note
    return silence_reason or NOT_STARTED, {**enforcement, "mcp_role_note": joined}
