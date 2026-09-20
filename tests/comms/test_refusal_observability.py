"""CORE274 sanitized INFO observability without writes or authority changes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401
from tests.comms.conftest import core
from tests.comms.test_identity_boundary import Scene, scene  # noqa: F401
from trw_mcp import comms, formation

TOOLS = ("trw_peers", "trw_send", "trw_inbox")
EVENT = "comms_identity_refused"
STORAGE_EVENT = "comms_storage_refused"
ENDPOINT_EVENT = "comms_endpoint_refused"


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def malformed(scene: Scene, monkeypatch: pytest.MonkeyPatch, case: str) -> str:
    if case == "unpinned":
        monkeypatch.setenv("TRW_SESSION_ID", "private-pin-never-log-me")
        return "no_pinned_run"
    if case == "corrupt":
        formation.manifest_path_for_run(scene.owner).write_text("[private-malformed-authority")
        return "formation_unavailable"
    formation.revise(
        "diagnostic", scene.owner, {"lead": {"pin_key": "private-rebound-pin"}}, trw_dir=scene.root / ".trw"
    )
    return "no_matching_member"


def call(scene: Scene, tool: str) -> dict[str, Any]:
    arguments = (
        {"recipient_member_id": "private-recipient", "request_key": "private-key", "body": "private-body"}
        if tool == "trw_send"
        else {}
    )
    result = asyncio.run(scene.server.call_tool(tool, arguments)).structured_content
    assert isinstance(result, dict)
    return result


def assert_observation(logs: list[dict[str, Any]], reason: str) -> None:
    events = [record for record in logs if record.get("event") == EVENT]
    assert events == [{"event": EVENT, "reason": reason, "log_level": "info"}], (
        "identity refusal event missing or unsanitized"
    )


def refusal_proof(scene: Scene, tool: str, reason: str, logs: list[dict[str, Any]]) -> None:
    before = snapshot(scene.root)
    logs.clear()
    result = call(scene, tool)
    assert result["status"] == "refused" and result["reason"] == reason
    assert snapshot(scene.root) == before, "identity refusal wrote project storage"
    assert_observation(logs, reason)


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("case", ["unpinned", "corrupt", "mismatch"])
@pytest.mark.parametrize("existing_store", [False, True])
def test_public_identity_refusal_logs_only_closed_reason_without_storage_writes(
    scene: Scene,
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, Any]],
    tool: str,
    case: str,
    existing_store: bool,
) -> None:
    if existing_store:
        assert scene.call("enroll")["status"] == "ok"
    reason = malformed(scene, monkeypatch, case)
    refusal_proof(scene, tool, reason, captured_structlog)
    if not existing_store:
        assert not list(scene.root.rglob("comms.sqlite3*"))


@pytest.mark.parametrize("tool", TOOLS)
def test_explicit_off_is_silent_even_with_invalid_authority(
    scene: Scene,
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, Any]],
    tool: str,
) -> None:
    scene.config.comms_enabled = False
    malformed(scene, monkeypatch, "unpinned")
    before = snapshot(scene.root)
    captured_structlog.clear()
    assert core(call(scene, tool)) == {"status": "disabled", "reason": "comms_disabled"}
    assert not [record for record in captured_structlog if record.get("event") == EVENT]
    assert snapshot(scene.root) == before


@pytest.mark.parametrize("tool", TOOLS)
def test_removing_only_logger_fails_unchanged_observation_oracle(
    scene: Scene,
    monkeypatch: pytest.MonkeyPatch,
    captured_structlog: list[dict[str, Any]],
    tool: str,
) -> None:
    reason = malformed(scene, monkeypatch, "unpinned")
    monkeypatch.setattr(comms, "_logger", SimpleNamespace(info=lambda *args, **kwargs: None))
    with pytest.raises(AssertionError, match="identity refusal event missing or unsanitized"):
        refusal_proof(scene, tool, reason, captured_structlog)


@pytest.mark.parametrize("tool", TOOLS)
def test_storage_refusal_is_observed_under_its_own_event(
    scene: Scene, captured_structlog: list[dict[str, Any]], tool: str
) -> None:
    """A corrupt mailbox must leave a server-side record, under its OWN event.

    An independent audit found only identity refusals were logged, so a corrupt
    or contended mailbox existed nowhere but the calling agent's response. Silent
    storage corruption is precisely how evidence is lost.

    The event is distinct from the identity one because they are different
    operator problems: an identity refusal means a caller could not be bound, a
    storage refusal means the mailbox itself is unusable. Collapsing them would
    make the log unactionable.
    """
    assert scene.call("enroll")["status"] == "ok"
    database = next(scene.root.rglob("comms.sqlite3"))
    database.write_bytes(b"corrupt mailbox evidence")
    before = snapshot(scene.root)
    captured_structlog.clear()

    result = call(scene, tool)

    assert result["status"] == "refused" and result["reason"] == "storage_corrupt"
    assert not [record for record in captured_structlog if record.get("event") == EVENT]
    observed = [record for record in captured_structlog if record.get("event") == STORAGE_EVENT]
    assert observed == [
        {"event": STORAGE_EVENT, "reason": "storage_corrupt", "retryable": False, "log_level": "info"}
    ], "storage refusal is unobserved or unsanitized"
    assert snapshot(scene.root) == before


@pytest.mark.parametrize("tool", TOOLS)
def test_removing_the_logger_fails_the_storage_oracle_too(
    scene: Scene, captured_structlog: list[dict[str, Any]], tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NEGATIVE CONTROL for the storage event, matching the identity one."""
    assert scene.call("enroll")["status"] == "ok"
    next(scene.root.rglob("comms.sqlite3")).write_bytes(b"corrupt mailbox evidence")
    monkeypatch.setattr(comms, "_logger", SimpleNamespace(info=lambda *args, **kwargs: None))
    captured_structlog.clear()

    call(scene, tool)

    assert not [record for record in captured_structlog if record.get("event") == STORAGE_EVENT]
