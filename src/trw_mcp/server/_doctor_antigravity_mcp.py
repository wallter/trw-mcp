"""``antigravity_mcp`` doctor row (PRD-FIX-133).

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling so the
doctor module stays under the effective-LOC gate, the same shape
``_doctor_memory_daemon`` and ``_doctor_embedding_egress`` already use.

Why the check exists
---------------------
``generate_antigravity_mcp_config`` (``bootstrap/_antigravity_cli.py``) writes
the ``trw`` MCP entry into Antigravity's real, GLOBAL config file. That write
can still silently fail to reach the running client — a stale ``agy`` build, a
second competing installer, or a hand-edited config could all leave the
client's live view unregistered even though the file on disk looks correct.
This row is the one live check that asks the client itself, via
``agy mcp list``, rather than re-reading the file TRW just wrote.

Verdict semantics
-----------------
``SKIP`` when Antigravity is not a selected client for this project (nothing
to verify), and — the load-bearing case — ``SKIP`` (never ``PASS``) when the
``agy`` binary is absent from ``PATH``: an absent binary means registration is
UNKNOWN, not confirmed, and a doctor that reported success for a tool it could
not find would train an operator to trust a check that never ran. ``PASS``
when ``agy mcp list`` runs and names ``trw``. ``WARN`` when it runs cleanly but
``trw`` is absent (names the remedy), or when the probe itself times out or
exits nonzero (names the captured detail). Never ``FAIL`` — a missing
Antigravity MCP registration degrades one client's capability, not the
install.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

__all__ = ["antigravity_mcp_row"]

_SERVER_NAME = "trw"
_AGY_BINARY = "agy"
_PROBE_TIMEOUT_S = 5.0
_REMEDY = (
    "run the TRW installer again (it writes ~/.gemini/config/mcp_config.json), or 'agy mcp add trw trw-mcp' directly."
)


def _antigravity_selected(target: Path) -> bool:
    """Whether this project selected ``antigravity-cli``, reusing the shared reader."""
    from trw_mcp.server._doctor_agent_parity import _selected_clients

    clients, _unreadable_reason = _selected_clients(target)
    return "antigravity-cli" in clients


def _lists_trw_server(stdout: str) -> bool:
    """True only when a line of ``agy mcp list`` output names the ``trw`` server itself.

    A substring test would also match ``trw-memory`` or a remedy hint such as
    ``Try: agy mcp add trw ...`` and confirm a registration that never happened,
    so the match is anchored to the first whitespace-delimited token of a line.
    """
    for line in stdout.splitlines():
        tokens = line.strip().split()
        if tokens and tokens[0] == _SERVER_NAME:
            return True
    return False


def antigravity_mcp_row(target: Path) -> tuple[str, str]:
    """Return ``(status, message)`` for the ``antigravity_mcp`` doctor row.

    A probe, never a fix: it reads ``agy mcp list`` and reports what it finds.
    """
    if not _antigravity_selected(target):
        return "SKIP", "antigravity-cli is not a selected client for this project — nothing to verify."

    agy_path = shutil.which(_AGY_BINARY)
    if agy_path is None:
        return (
            "SKIP",
            f"'{_AGY_BINARY}' binary not found on PATH — MCP registration is UNKNOWN, not confirmed.",
        )

    try:
        completed = subprocess.run(  # noqa: S603 — fixed argv, no shell, resolved via PATH lookup above
            [agy_path, "mcp", "list"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "WARN", f"'{_AGY_BINARY} mcp list' timed out after {_PROBE_TIMEOUT_S:.0f}s — could not verify."
    except OSError as exc:
        return "WARN", f"could not run '{_AGY_BINARY} mcp list': {exc}"

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        return "WARN", f"'{_AGY_BINARY} mcp list' failed: {detail}"

    if _lists_trw_server(completed.stdout):
        return "PASS", f"'trw' MCP server is registered with {_AGY_BINARY} ({_AGY_BINARY} mcp list confirms it)."
    return "WARN", f"'trw' is not registered with {_AGY_BINARY} — {_REMEDY}"
