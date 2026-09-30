"""CODEX-P0-B-ZERO-TOOL, server side: the receipt the dispatcher's runner demands (dispatch/_handshake.py).

The end-to-end case starts the real FastMCP server in-process as a reviewer, lists tools through a real client, and feeds
the file the middleware wrote to the runner's own ``proof`` - so the two halves are checked against each other, not
against a hand-written receipt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trw_mcp.dispatch import _handshake
from trw_mcp.middleware import handshake_receipt as hr
from trw_mcp.models.surface_packs import REVIEWER_TOOLS


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "receipt"
    directory.mkdir(mode=0o700)
    path = directory / "receipt.json"
    monkeypatch.setenv(hr.PATH_ENV, str(path))
    monkeypatch.setenv(hr.NONCE_ENV, "a" * 32)
    return path


def test_an_ordinary_session_writes_nothing_and_builds_no_middleware(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(hr.PATH_ENV, raising=False)
    monkeypatch.delenv(hr.NONCE_ENV, raising=False)
    assert hr.maybe_handshake_middleware() is None
    assert hr.write_receipt(["trw_recall"]) is False


@pytest.mark.parametrize("missing", [hr.PATH_ENV, hr.NONCE_ENV])
def test_both_variables_are_needed(target: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.delenv(missing)
    assert hr.maybe_handshake_middleware() is None and hr.write_receipt(["t"]) is False and not target.exists()


def test_the_receipt_carries_the_nonce_and_the_sorted_tool_names(target: Path) -> None:
    assert hr.write_receipt(["trw_recall", "trw_code", "trw_recall"]) is True
    receipt = json.loads(target.read_text(encoding="utf-8"))
    assert receipt["nonce"] == "a" * 32 and receipt["tools"] == ["trw_code", "trw_recall"]
    assert receipt["pid"] == os.getpid() and receipt["schema"] == 1
    assert (target.stat().st_mode & 0o777) == 0o600


def test_a_second_listing_replaces_the_receipt_atomically(target: Path) -> None:
    hr.write_receipt(["a"])
    hr.write_receipt(["a", "b"])
    assert json.loads(target.read_text(encoding="utf-8"))["tools"] == ["a", "b"]
    assert [p.name for p in target.parent.iterdir()] == ["receipt.json"], "no temp file is left behind"


def test_a_symlinked_or_unplaceable_target_is_refused_without_raising(
    target: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("keep", encoding="utf-8")
    target.symlink_to(elsewhere)
    assert hr.write_receipt(["t"]) is False and elsewhere.read_text(encoding="utf-8") == "keep"
    target.unlink()
    monkeypatch.setenv(hr.PATH_ENV, str(tmp_path / "no-such-dir" / "r.json"))
    assert hr.write_receipt(["t"]) is False
    monkeypatch.setenv(hr.PATH_ENV, "relative/r.json")
    assert hr.maybe_handshake_middleware() is None


def test_a_failed_replace_leaves_no_partial_receipt(target: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_a: object, _b: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(hr.os, "replace", boom)
    assert hr.write_receipt(["t"]) is False
    assert list(target.parent.iterdir()) == []


@pytest.mark.usefixtures("no_memory_daemon")
async def test_the_real_reviewer_server_writes_the_receipt_the_runner_accepts(
    target: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastmcp import Client

    import trw_mcp.server._boot_deferred as boot_deferred

    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    monkeypatch.setattr(boot_deferred, "_resolve_backend_sync", lambda: None)
    from trw_mcp.server._app import create_app
    from trw_mcp.server._tools import _tool_registrars

    server = create_app()
    for register in _tool_registrars():
        register(server)
    async with Client(server) as client:
        shown = {tool.name for tool in await client.list_tools()}

    handshake = _handshake.Handshake(target.parent, "a" * 32)
    assert json.loads(target.read_text(encoding="utf-8"))["tools"] == sorted(shown), (
        "the receipt IS what the client saw"
    )
    assert shown <= REVIEWER_TOOLS and shown, "the reviewer surface was in force"
    assert _handshake.proof(handshake, REVIEWER_TOOLS) == (True, ""), (
        "the whole reviewer surface, as the runner demands it"
    )
    assert _handshake.proof(_handshake.Handshake(target.parent, "b" * 32), REVIEWER_TOOLS)[0] is False


def test_a_failed_write_never_deletes_a_temp_path_it_did_not_create(target: Path) -> None:
    squatter = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    squatter.write_text("someone else's file", encoding="utf-8")
    assert hr.write_receipt(["t"]) is False
    assert squatter.read_text(encoding="utf-8") == "someone else's file"
