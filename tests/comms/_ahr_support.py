"""Shared AHR fixtures for the PRD-CORE-349 store tests: sealed records between two formation members.

The records are the vendored ``standard-happy-path`` lifecycle vector re-addressed to the scene's
members and re-dated around the real clock, then sealed, so every test starts from a record and a
read-back the reference checker accepts.
"""

from __future__ import annotations

import asyncio
import copy
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.comms.test_policy import SendScene
from trw_mcp.handoff import digest, load, seal, validate

VECTOR = Path(__file__).resolve().parents[1] / "handoff" / "vectors" / "lifecycle" / "valid" / "standard-happy-path"


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def handoff_doc(
    *,
    sender: str = "impl-1",
    receiver: str = "impl-2",
    handoff_id: str = "01J9ZK5W1STD000000000001",
    subject: str = "swap-dirty-worktree",
    tier: str = "standard",
    created: float | None = None,
    expires_in: float | None = 3600.0,
    supersedes: list[dict[str, str]] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    """A sealed, valid handoff from *sender* to *receiver*."""
    doc = load(VECTOR / "handoff.json")
    doc.pop("integrity", None)
    created = time.time() - 120 if created is None else created
    doc.update(handoff_id=handoff_id, subject=subject, tier=tier, created_at=stamp(created))
    doc["from"] = {"id": sender, "kind": "agent"}
    doc["to"] = {"id": receiver, "kind": "agent"}
    doc["as_of"]["at"] = stamp(created - 30)
    doc["supersedes"] = supersedes or []
    for action in doc["next_actions"]:
        action["owner"] = receiver
    if expires_in is None:
        doc.pop("expires_at", None)
    else:
        doc["expires_at"] = stamp(created + 120 + expires_in)
    if tier == "minimal":
        doc.pop("readback", None)
    if tier == "critical":
        doc["readback"]["verifier"] = {"id": "lead", "kind": "human"}
        doc["readback"]["reverify"] = ["c1", "c2"]
        doc["next_read"][0]["digest"] = "sha256:" + "a" * 64
    doc.update(changes)
    sealed = seal(doc)
    assert validate(sealed) == [], validate(sealed)
    return sealed


def readback_doc(
    handoff: dict[str, Any],
    *,
    by: str = "impl-2",
    readback_id: str = "01J9ZK5W1RB0000000000001",
    at: float | None = None,
    disposition: str = "ready",
    contradicted: bool = False,
    check: bool = True,
) -> dict[str, Any]:
    """A sealed read-back of *handoff* by *by* that passes L1 against it."""
    doc = load(VECTOR / "readback.json")
    doc.pop("integrity", None)
    doc["readback_id"] = readback_id
    doc["handoff"] = {"handoff_id": handoff["handoff_id"], "digest": digest(handoff)}
    doc["by"] = {"id": by, "kind": "agent"}
    doc["at"] = stamp(time.time() - 5 if at is None else at)
    doc["reverified"][0]["evidence"]["at"] = doc["at"]
    doc["reverified"][0]["evidence"]["producer"] = by
    doc["disposition"] = disposition
    if disposition == "questions":
        doc["questions"] = ["Which worktree is the swap target?"]
    if contradicted:
        doc["reverified"][0]["result"] = "contradicted"
        doc["reverified"][0]["evidence"]["result"] = "contradicts"
        doc["discrepancies"] = [{"text": "c2 fails on the receiver's run.", "claim_id": "c2"}]
    sealed = seal(doc)
    findings = validate(sealed, handoff)
    assert findings == [] or not check, findings
    return sealed


def write(root: Path, name: str, doc: dict[str, Any] | bytes) -> str:
    """Write *doc* under the project root and return its repo-relative path."""
    path = root / "handoffs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(doc, bytes):
        path.write_bytes(doc)
    else:
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return f"handoffs/{name}"


def call(scene: SendScene, tool: str, **arguments: Any) -> dict[str, Any]:
    result = asyncio.run(scene.server.call_tool(tool, arguments)).structured_content
    assert isinstance(result, dict)
    return result


def offer(
    scene: SendScene, handoff: dict[str, Any], *, key: str = "ahr-1", recipient: str = "impl-2"
) -> dict[str, Any]:
    """impl-1 offers *handoff* through trw_send; the scene is left acting as impl-1."""
    scene.actor("impl-1")
    path = write(scene.formation.project_root, f"{handoff['handoff_id']}.json", handoff)
    return call(scene, "trw_send", recipient_member_id=recipient, request_key=key, body="", handoff={"path": path})


def inbox(scene: SendScene, member: str, action: str, message_id: str, **arguments: Any) -> dict[str, Any]:
    scene.actor(member)
    return call(scene, "trw_inbox", action=action, message_ids=[message_id], **arguments)


def read_back(scene: SendScene, message_id: str, readback: dict[str, Any], *, member: str = "impl-2") -> dict[str, Any]:
    path = write(scene.formation.project_root, f"rb-{readback['readback_id']}.json", readback)
    return inbox(scene, member, "read_back", message_id, handoff={"path": path})


def events(scene: SendScene, handoff_id: str) -> list[tuple[int, str, str]]:
    return [
        (int(seq), str(event), str(actor))
        for seq, event, actor in scene.rows(
            "SELECT seq,event,actor FROM ahr_events WHERE handoff_id=? ORDER BY seq", (handoff_id,)
        )
    ]


def clone(doc: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(doc)
