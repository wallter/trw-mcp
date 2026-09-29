"""PRD-CORE-333 S3b: the UserPromptSubmit hook's filtered read is one request per daemon call.

S3 (FR03) moved the hook's candidates from the entries mirror onto the store, and the
read cost 1.7-2.2 s a prompt. Most of it was not the store: each of the read's two
daemon calls (the FR07 security check, then the list page) opened an MCP session --
``initialize``, ``notifications/initialized``, the call, a ``tools/list`` -- and the
process imported fastmcp to do it. The daemon serves stateless JSON, so each call is
now ONE ``tools/call`` POST and fastmcp never loads.

Wall time is too noisy to gate on; this counts what the time was made of, against a
real daemon with a quarantined identity in the ledger, and checks the fast read
recalls exactly what a session read recalls. Timings: the S3b receipt in
``docs/documentation/operational-knowledge/auto-recall-calibration.md``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout

#: The real hook module, run as the hook runs it, with every HTTP request counted.
#: ``session`` empties ``trw_memory.daemon._direct.DIRECT_TOOLS``, so the same read goes through MCP sessions;
#: ``reply:<json>`` answers the list page with that body instead of the daemon's.
_COUNTING_HOOK = """
import json, runpy, sys
import httpx
mode = sys.argv[1]
sent = []
real_send = httpx.AsyncClient.send
async def send(self, request, **kwargs):
    body = json.loads(request.content) if request.method == "POST" else {}
    sent.append(body.get("method", "") if request.method == "POST" else request.method)
    if mode.startswith("reply:") and body.get("params", {}).get("name") == "memory_list_page":
        return httpx.Response(200, json=json.loads(mode[6:]), request=request)
    return await real_send(self, request, **kwargs)
httpx.AsyncClient.send = send
if mode == "session":
    from trw_memory.daemon import _direct
    _direct.DIRECT_TOOLS = frozenset()
sys.argv = ["_auto_recall_hook", *sys.argv[2:]]
try:
    runpy.run_module("trw_mcp.state._auto_recall_hook", run_name="__main__")
except SystemExit:
    pass
sys.stderr.write("REQUESTS " + json.dumps({"sent": sent, "fastmcp": "fastmcp" in sys.modules, "trwconfig": "trw_mcp.models.config" in sys.modules}) + "\\n")
"""

_PROMPT = "wal reset corruption recovery path"
#: max_results, max_tokens, min_score, scan_cap: the hook's shipped defaults.
_TUNABLES = ("3", "100", "0.35", "10000")


def _seed(checkout: DaemonCheckout) -> dict[str, str]:
    """Matching rows (one of them ledgered as quarantined) among unrelated ones; their ids by label."""
    from trw_memory.daemon import DaemonPaths
    from trw_memory.models.config import MemoryConfig
    from trw_memory.security.quarantine_ledger import LedgerIdentity, ledger_for_config

    async def _store() -> dict[str, str]:
        ids = {}
        for label in ("safe", "poison", "partial", "other"):
            content = {"partial": "wal reset notes", "other": "structlog keyword gotcha"}.get(
                label, f"wal reset corruption recovery {label} path"
            )
            ids[label] = str((await checkout.client.store(content, checkout.namespace))["memory_id"])
        return ids

    ids = asyncio.run(_store())
    paths = DaemonPaths.resolve()
    config = MemoryConfig(storage_path=str(paths.user_memory_dir), memory_single_store_path=str(paths.store))
    ledger_for_config(config).append(
        LedgerIdentity(namespace=checkout.namespace, entry_id=ids["poison"]), "quarantined", actor="test"
    )
    return ids


def _read(checkout: DaemonCheckout, mode: str, injected: Path) -> tuple[str, str, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-c", _COUNTING_HOOK, mode, str(checkout.trw_dir.parent), _PROMPT, str(injected), *_TUNABLES],
        text=True,
        capture_output=True,
        env=dict(os.environ),
        check=True,
    )
    counted = next(line for line in completed.stderr.splitlines() if line.startswith("REQUESTS "))
    diagnostic = next(line for line in completed.stderr.splitlines() if line.startswith("event=AutoRecall"))
    return completed.stdout, diagnostic, json.loads(counted.removeprefix("REQUESTS "))


def test_the_hook_read_is_one_post_per_daemon_call_and_imports_neither_fastmcp_nor_trwconfig(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    ids = _seed(daemon_checkout)

    stdout, diagnostic, counted = _read(daemon_checkout, "direct", tmp_path / "injected-direct")

    assert counted["sent"] == ["tools/call", "tools/call"], "the security check and one page, nothing else"
    assert counted["fastmcp"] is False
    assert counted["trwconfig"] is False, "the pin read imported TRWConfig (~110 ms, S3c)"
    assert "decision=fired" in diagnostic
    assert ids["safe"] in stdout
    assert ids["poison"] not in stdout, "a quarantined identity reached the prompt through the fast read"


def test_the_fast_read_recalls_what_a_session_read_recalls(daemon_checkout: DaemonCheckout, tmp_path: Path) -> None:
    """Same learnings, same order, same filtering: only the transport changed."""
    ids = _seed(daemon_checkout)

    direct, direct_diagnostic, _ = _read(daemon_checkout, "direct", tmp_path / "injected-direct")
    session, session_diagnostic, counted = _read(daemon_checkout, "session", tmp_path / "injected-session")

    assert "initialize" in counted["sent"], "the session arm did not exercise the session path"
    assert direct == session
    assert direct_diagnostic.split(" elapsed_ms=")[0] == session_diagnostic.split(" elapsed_ms=")[0]
    assert (tmp_path / "injected-direct").read_text() == (tmp_path / "injected-session").read_text()
    assert ids["poison"] not in session


@pytest.mark.parametrize(
    "reply",
    [
        {"jsonrpc": "2.0", "id": 1, "result": {"content": [], "structuredContent": "invalid", "isError": False}},
        {"jsonrpc": "2.0", "id": 1, "result": {"content": {"text": "denied"}, "isError": True}},
        {"jsonrpc": "2.0", "id": 1},
    ],
    ids=["structuredContent-string", "refusal-content-dict", "no-result"],
)
def test_a_malformed_or_refused_page_records_store_unavailable(
    daemon_checkout: DaemonCheckout, tmp_path: Path, reply: dict[str, object]
) -> None:
    """Review r1 P2: the hook fails closed on a bad reply -- no recall, no crash into "no interpreter"."""
    _seed(daemon_checkout)

    stdout, diagnostic, _ = _read(daemon_checkout, "reply:" + json.dumps(reply), tmp_path / "injected")

    assert stdout == ""
    assert "decision=store_unavailable" in diagnostic
    assert not (tmp_path / "injected").exists()
