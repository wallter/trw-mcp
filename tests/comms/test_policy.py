"""CORE274 group-birth policy and rate boundaries through real public tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms, joined_member


@dataclass
class SendScene:
    formation: FormationFixture
    server: FastMCP
    config: Any
    monkeypatch: pytest.MonkeyPatch

    def actor(self, member: str) -> None:
        self.monkeypatch.setenv("TRW_SESSION_ID", "pin-a" if member == "impl-1" else "pin-b")

    def send(self, key: str = "key", body: str = "hello", **overrides: Any) -> dict[str, Any]:
        args = {"recipient_member_id": "impl-2", "request_key": key, "body": body, **overrides}
        result = asyncio.run(self.server.call_tool("trw_send", args))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        from trw_mcp.comms import _store

        conn = _store.sqlite3.connect(_store.database_path(self.formation.manifest_path()))
        try:
            result = conn.execute(sql, parameters).fetchall()
            conn.commit()
            return result
        finally:
            conn.close()


@pytest.fixture
def scene(
    formation_env: FormationFixture,
    comms_server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> SendScene:
    overrides = getattr(request, "param", {})
    config = enable_comms(monkeypatch, **overrides)
    joined_member(formation_env, "impl-1", "pin-a")
    joined_member(formation_env, "impl-2", "pin-b")
    instance = SendScene(formation_env, comms_server, config, monkeypatch)
    instance.actor("impl-2")
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    instance.actor("impl-1")
    return instance


@pytest.mark.parametrize(
    "scene",
    [
        {
            "comms_group_admission_limit": 2,
            "comms_body_max_bytes": 4,
            "comms_recipient_outstanding_limit": 2,
            "comms_sender_admissions_per_minute": 2,
        }
    ],
    indirect=True,
)
def test_enrollment_first_policy_survives_config_reload(scene: SendScene) -> None:
    scene.config.comms_group_admission_limit = 99
    scene.config.comms_body_max_bytes = 99
    scene.config.comms_recipient_outstanding_limit = 99
    scene.config.comms_sender_admissions_per_minute = 99
    assert scene.send("large", "12345")["reason"] == "body_too_large"
    assert scene.send("a", "1234")["status"] == "ok"
    assert scene.send("b", "1234")["status"] == "ok"
    assert scene.send("c", "1234")["reason"] == "group_admission_limit"
    assert scene.rows("SELECT group_limit,body_limit,outstanding_limit,rate_limit,charge FROM groups") == [
        (2, 4, 2, 2, 2)
    ]


@pytest.mark.parametrize("scene", [{"comms_sender_admissions_per_minute": 1}], indirect=True)
def test_rate_window_excludes_exact_sixty_and_clock_cannot_regress(scene: SendScene) -> None:
    from trw_mcp.comms import _store

    now = scene.rows("SELECT group_time FROM groups")[0][0]
    scene.monkeypatch.setattr(_store.time, "time", lambda: now)
    assert scene.send("a")["status"] == "ok"
    scene.monkeypatch.setattr(_store.time, "time", lambda: now - 100)
    assert scene.send("backwards")["reason"] == "sender_rate_limit"
    scene.monkeypatch.setattr(_store.time, "time", lambda: now + 59.999)
    assert scene.send("almost")["reason"] == "sender_rate_limit"
    scene.monkeypatch.setattr(_store.time, "time", lambda: now + 60)
    assert scene.send("boundary")["status"] == "ok"
    assert scene.rows("SELECT charge,group_time FROM groups") == [(2, now + 60)]


@pytest.mark.parametrize("scene", [{"comms_recipient_outstanding_limit": 1}], indirect=True)
def test_outstanding_limit_is_distinct_from_group_limit(scene: SendScene) -> None:
    assert scene.send("a")["status"] == "ok"
    assert scene.send("b")["reason"] == "recipient_outstanding_limit"
    assert scene.rows("SELECT charge FROM groups") == [(1,)]
