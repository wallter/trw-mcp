"""Public-tool paging proofs against isolated native formations and pins."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP


@dataclass
class Pages:
    server: FastMCP
    root: Path
    ids: list[str]
    config: Any

    def call(self, action: str = "list", cursor: str | None = None) -> dict[str, Any]:
        result = asyncio.run(self.server.call_tool("trw_peers", {"action": action, "cursor": cursor}))
        assert isinstance(result.structured_content, dict)
        return result.structured_content


@pytest.fixture
def pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pages:
    from trw_mcp import formation
    from trw_mcp.models import config as config_module
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state import _paths
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import pin_active_run
    from trw_mcp.tools.swarm_comms import register_swarm_comms_tools

    root = tmp_path / "project"
    from tests import _path_isolation

    _path_isolation.set_current_root(root)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    config = TRWConfig(
        comms_enabled=True,
        ctx_isolation_enabled=True,
        cleanup_on_boot=False,
        comms_body_max_bytes=1,
        comms_response_max_bytes=4102,
        comms_fetch_max_items=64,
    )
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setattr(_paths, "get_config", lambda: config)
    ids = ["lead"] + [f"p{n:03}" + "z" * 60 for n in range(40)]
    runs = [root / ".trw" / "runs" / member for member in ids]
    for run in runs:
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text("run_id: diagnostic\ntask: paging\nstatus: active\n")
    formation.create(
        runs[0],
        {
            "formation_id": "paging",
            "members": [{"member_id": member, "client": "codex", "open_join": True} for member in ids],
        },
        trw_dir=root / ".trw",
        prds_dir=root / "prds",
    )
    server = FastMCP("paging-diagnostic")
    register_swarm_comms_tools(server)
    fixture = Pages(server, root, ids, config)
    for member, run in zip(ids, runs, strict=True):
        monkeypatch.setenv("TRW_SESSION_ID", member)
        context = build_call_context(None)
        formation.join("paging", member, run, pin_key=context.session_id, trw_dir=root / ".trw")
        pin_active_run(run, context=context)
        assert fixture.call("enroll")["status"] == "ok"
    monkeypatch.setenv("TRW_SESSION_ID", "lead")
    return fixture


def wire_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def collect(pages: Pages) -> tuple[list[str], int]:
    cursor = None
    members: list[str] = []
    count = 0
    while True:
        result = pages.call(cursor=cursor)
        assert result["status"] == "ok"
        assert len(wire_bytes(result)) <= pages.config.comms_response_max_bytes
        assert len(result["peers"]) <= pages.config.comms_fetch_max_items
        members.extend(peer["member_id"] for peer in result["peers"])
        count += 1
        cursor = result.get("next_cursor")
        if cursor is None:
            return members, count
        assert result["peers"], "continuation made no progress"
        assert count <= len(pages.ids)


def test_tight_byte_limit_paginates_all_rows_without_silent_loss(pages: Pages) -> None:
    # Positive control: the same real roster exceeds the tighter ceiling when
    # the operator allows a larger response. The setup is not vacuously small.
    pages.config.comms_response_max_bytes = 65536
    unlimited = pages.call()
    assert len(wire_bytes(unlimited)) > 4102
    assert len(unlimited["peers"]) == len(pages.ids)
    pages.config.comms_response_max_bytes = 4102
    members, count = collect(pages)
    assert members == pages.ids
    assert count >= 2
    for action in ("enroll", "heartbeat"):
        first = pages.call(action)
        assert first["status"] == "ok"
        assert first["next_cursor"]
        assert len(wire_bytes(first)) <= 4102
        assert pages.call(cursor=first["next_cursor"])["status"] == "ok"


def test_count_limit_and_stable_member_order(pages: Pages) -> None:
    from trw_mcp.comms import _store

    pages.config.comms_fetch_max_items = 3
    first = pages.call()
    seen = [peer["member_id"] for peer in first["peers"]]
    assert len(seen) == 3
    # Enrollment time changes on reincarnation; it must not be a sort key.
    conn = _store.sqlite3.connect(next(pages.root.rglob(_store.DATABASE_FILENAME)))
    try:
        conn.execute("UPDATE endpoints SET enrolled_at = enrolled_at - 100 WHERE member_id > ?", (seen[-1],))
        conn.commit()
    finally:
        conn.close()
    second = pages.call(cursor=first["next_cursor"])
    assert [peer["member_id"] for peer in second["peers"]] == pages.ids[3:6]
    members, count = collect(pages)
    assert members == pages.ids
    assert count == 14


@pytest.mark.parametrize(
    "kind", ["malformed", "oversized", "cross_group", "cross_caller", "wrong_version", "wrong_action"]
)
def test_cursor_rejection_precedes_operation_mutation(pages: Pages, kind: str) -> None:
    from trw_mcp.comms import _store

    cursor = pages.call()["next_cursor"]
    assert cursor
    action = "list"
    if kind == "malformed":
        cursor = "!not-base64!"
    elif kind == "oversized":
        cursor = "a" * 513
    elif kind == "wrong_action":
        action = "heartbeat"
    else:
        parts = json.loads(base64.urlsafe_b64decode(cursor))
        if kind == "cross_group":
            parts[1] = "0" * 32
        elif kind == "cross_caller":
            parts[2] = "different-peer"
        else:
            parts[0] = 2
        cursor = base64.urlsafe_b64encode(wire_bytes(parts)).decode()
    db = next(pages.root.rglob(_store.DATABASE_FILENAME))
    before = db.read_bytes()
    refused = pages.call(action, cursor)
    assert refused["reason"] == "invalid_cursor"
    assert db.read_bytes() == before


def test_cursor_does_not_replace_current_pin_authority(pages: Pages, monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = pages.call()["next_cursor"]
    monkeypatch.setenv("TRW_SESSION_ID", "not-a-pinned-peer")
    result = pages.call(cursor=cursor)
    assert result["status"] == "refused"
    assert result["reason"] == "no_pinned_run"


def test_real_other_formation_cursor_refuses_before_first_database(pages: Pages) -> None:
    from trw_mcp import formation
    from trw_mcp.comms import _store
    from trw_mcp.state._call_context import build_call_context
    from trw_mcp.state._paths import pin_active_run

    cursor = pages.call()["next_cursor"]
    other = pages.root / ".trw" / "runs" / "other-owner"
    (other / "meta").mkdir(parents=True)
    (other / "meta" / "run.yaml").write_text("run_id: other\ntask: paging\nstatus: active\n")
    formation.create(
        other,
        {"formation_id": "other", "members": [{"member_id": "lead", "client": "codex", "open_join": True}]},
        trw_dir=pages.root / ".trw",
        prds_dir=pages.root / "prds",
    )
    context = build_call_context(None)
    formation.join("other", "lead", other, pin_key=context.session_id, trw_dir=pages.root / ".trw")
    pin_active_run(other, context=context)
    assert list(other.rglob(_store.DATABASE_FILENAME)) == []
    assert pages.call(cursor=cursor)["reason"] == "invalid_cursor"
    assert list(other.rglob(_store.DATABASE_FILENAME)) == []
    assert pages.call("enroll")["status"] == "ok"
    assert len(list(other.rglob(_store.DATABASE_FILENAME))) == 1
