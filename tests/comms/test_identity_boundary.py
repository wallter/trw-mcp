"""PRD-CORE-274-FR01/FR10 public-tool regressions; synthetic files, no guard patches.

Native formation create/join/revise and native pin writes establish authority.
Only configuration getters/environment are isolated; the actual loader and
FastMCP dispatch are exercised. These are in-process handler proofs, not a
fresh stdio server or full middleware acceptance claim.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastmcp import FastMCP

from trw_mcp import formation
from trw_mcp.comms import _identity
from trw_mcp.models import config as config_module
from trw_mcp.models.config import TRWConfig
from trw_mcp.state import _paths
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import pin_active_run
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools


@dataclass
class Scene:
    root: Path
    owner: Path
    config: TRWConfig
    server: FastMCP

    def call(self, action: str, cursor: str | None = None) -> dict[str, Any]:
        arguments: dict[str, Any] = {"action": action}
        if cursor is not None:
            arguments["cursor"] = cursor
        result = asyncio.run(self.server.call_tool("trw_peers", arguments))
        payload = result.structured_content
        assert isinstance(payload, dict), "public route did not return structured payload"
        return payload


@pytest.fixture
def scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Scene:
    root = tmp_path / "synthetic-project"
    from tests import _path_isolation

    _path_isolation.set_current_root(root)
    home = tmp_path / "synthetic-home"
    home.mkdir()
    owner = root / ".trw" / "runs" / "owner"
    (owner / "meta").mkdir(parents=True)
    (owner / "meta" / "run.yaml").write_text("run_id: owner\ntask: diagnostic\nstatus: active\n")
    config = TRWConfig(comms_enabled=True, ctx_isolation_enabled=True, cleanup_on_boot=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    monkeypatch.setenv("TRW_SESSION_ID", "diagnostic-" + tmp_path.name)
    # Model getter copies are configuration seams, not authorization stubs.
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setattr(_paths, "get_config", lambda: config)
    context = build_call_context(None)
    formation.create(
        owner,
        {"formation_id": "diagnostic", "members": [{"member_id": "lead", "client": "codex", "open_join": True}]},
        trw_dir=root / ".trw",
        prds_dir=root / "prds",
    )
    formation.join("diagnostic", "lead", owner, pin_key=context.session_id, trw_dir=root / ".trw")
    pin_active_run(owner, context=context)
    loaded = formation.load(owner, trw_dir=root / ".trw")
    assert loaded is not None
    assert loaded.is_orchestrator
    assert loaded.manifest.members[0].status == "joined"
    assert loaded.manifest.members[0].pin_key == context.session_id
    assert not list(root.rglob("comms.sqlite3"))
    print("identity_sha256=" + hashlib.sha256(Path(_identity.__file__).read_bytes()).hexdigest())
    server = FastMCP("independent-public-t1-diagnostic")
    register_swarm_comms_tools(server)
    return Scene(root, owner, config, server)


def test_disabled_context_isolation_refuses_before_database_creation(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Disabled pin isolation legitimately selects the process pin. Populate that
    # native binding too so a missing-pin error cannot masquerade as this guard.
    scene.config.ctx_isolation_enabled = False
    context = build_call_context(None)
    formation.revise("diagnostic", scene.owner, {"lead": {"pin_key": context.session_id}}, trw_dir=scene.root / ".trw")
    pin_active_run(scene.owner, context=context)
    monkeypatch.setenv("TRW_SESSION_ID", context.session_id)
    refused = scene.call("enroll")
    databases_after_disabled = list(scene.root.rglob("comms.sqlite3"))
    print(f"disabled_context_result={refused} database_count={len(databases_after_disabled)}")
    scene.config.ctx_isolation_enabled = True
    positive = scene.call("enroll")
    assert positive["status"] == "ok", "valid native binding never reached enrollment"
    assert positive["member_id"] == "lead"
    assert refused["status"] == "refused", "ctx isolation disabled but public operation accepted"
    assert databases_after_disabled == [], "disabled context wrote communications storage"


def test_owning_run_member_stamp_mismatch_refuses_via_native_loader(scene: Scene) -> None:
    assert scene.call("enroll")["status"] == "ok", "positive public enrollment never reached"
    run_yaml = scene.owner / "meta" / "run.yaml"
    raw = yaml.safe_load(run_yaml.read_text())
    assert raw["member_id"] == "lead"
    raw["member_id"] = "foreign"
    run_yaml.write_text(yaml.safe_dump(raw))
    loaded = formation.load(scene.owner, trw_dir=scene.root / ".trw")
    assert loaded is not None and loaded.is_orchestrator
    assert loaded.manifest.members[0].member_id == "lead"
    result = scene.call("list")
    print(f"owning_run_foreign_stamp_result={result}")
    assert result["status"] == "refused"
    assert result["reason"] == "stamped_identity_mismatch"


@pytest.mark.parametrize("cursor", [None, "!malformed"])
def test_observed_all_terminal_group_cannot_reopen_on_active_revision(scene: Scene, cursor: str | None) -> None:
    assert scene.call("enroll")["status"] == "ok", "positive public enrollment never reached"
    terminal = formation.revise(
        "diagnostic", scene.owner, {"lead": {"status": "abandoned"}}, trw_dir=scene.root / ".trw"
    )
    assert all(member.status in formation.TERMINAL_STATUSES for member in terminal.members)
    observed = scene.call("list", cursor)
    assert observed["status"] == "refused", "terminal observation did not reach refusal path"
    active = formation.revise("diagnostic", scene.owner, {"lead": {"status": "active"}}, trw_dir=scene.root / ".trw")
    assert active.revision > terminal.revision
    assert active.members[0].status == "active"
    after = scene.call("enroll")
    again = scene.call("heartbeat")
    print(f"terminal_observation={observed} active_revision_enroll={after} heartbeat={again}")
    assert after["status"] == "refused", "observed terminal group reopened after revision"
    assert after["reason"] == "group_closed"
    assert again["status"] == "refused"
