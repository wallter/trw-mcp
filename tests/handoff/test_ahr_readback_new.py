"""``trw-mcp handoff readback-new`` and the critical tier end to end (X-9, X-11, X-13, X-14, X-17, X-18).

Sender and receiver run as different harness sessions (``CLAUDE_CODE_SESSION_ID``), the way two
sessions of one agent hand over (R-ROLE-1).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.handoff._cli_support import _fill, _new, _run, repo  # noqa: F401
from trw_mcp.handoff import digest, load, validate
from trw_mcp.handoff._validate import PLACEHOLDER

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _as(monkeypatch: pytest.MonkeyPatch, session: str, harness: str = "CLAUDE_CODE_SESSION_ID") -> None:
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CODEX_THREAD_ID", "CODEX_CLI_VERSION", "CODEX_SANDBOX_TYPE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(harness, session)


def _sealed_handoff(capsys: pytest.CaptureFixture[str], *argv: str) -> Path:
    path = _new(capsys, "--subject", "swap-refusal", "--next-read", "a.txt", *argv)
    path.write_text(json.dumps(_fill(load(path)), indent=2), encoding="utf-8")
    code, out, err = _run(capsys, "seal", str(path))
    assert code == 0, out + err
    return path


def _readback_new(capsys: pytest.CaptureFixture[str], handoff: Path, *argv: str) -> Path:
    code, out, err = _run(capsys, "readback-new", str(handoff), *argv)
    assert code == 0, err
    return Path(out.strip().splitlines()[-1])


def _fill_readback(doc: dict[str, Any], repo: Path) -> dict[str, Any]:
    """What a receiver writes after re-running each procedure: confirmed, evidence with raw output at critical."""
    raw = repo / "c1-output.txt"
    raw.write_text("1 passed\n", encoding="utf-8")
    choices = {"result": "confirmed", "disposition": "ready"}

    def fill(value: Any, key: str = "", parent: str = "") -> Any:
        if isinstance(value, dict):
            return {k: fill(v, k, key) for k, v in value.items()}
        if isinstance(value, list):
            return [fill(v, key, parent) for v in value]
        if not (isinstance(value, str) and PLACEHOLDER in value):
            return value
        if parent == "evidence" and key == "result":
            return "supports"
        if parent == "raw":
            return {"uri": "file:c1-output.txt", "digest": "sha256:" + hashlib.sha256(raw.read_bytes()).hexdigest()}[
                key
            ]
        if key == "at":
            return doc["at"]  # ran within the window; the read-back's at is stamped after the re-runs
        return choices.get(key, f"In my own words, the {parent or key} as I understand it.")

    return fill(doc)


def _seal_readback(capsys: pytest.CaptureFixture[str], rb: Path, handoff: Path) -> tuple[int, str]:
    code, out, err = _run(capsys, "seal", str(rb), "--handoff", str(handoff))
    return code, out + err


@pytest.mark.parametrize("tier", ["minimal", "standard"])
def test_readback_new_draft_fills_mechanics_and_seals_once_filled(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tier: str
) -> None:
    _as(monkeypatch, "sender-1")
    handoff = _sealed_handoff(capsys, "--tier", tier)
    _as(monkeypatch, "receiver-2")
    rb = _readback_new(capsys, handoff)
    draft = load(rb)
    assert rb.name.startswith(f"{load(handoff)['handoff_id']}.readback.rb-")
    assert draft["handoff"] == {"handoff_id": load(handoff)["handoff_id"], "digest": digest(load(handoff))}
    assert draft["by"] == {"id": "claude-code:receiver-2", "kind": "agent"}
    assert draft["pointer_checks"] == [
        {"index": 0, "status": "match", "observed_digest": load(handoff)["next_read"][0]["digest"]}
    ]
    assert {f.rule for f in validate(draft, load(handoff))} == {"placeholder"}
    assert _seal_readback(capsys, rb, handoff)[0] == 1  # an unfilled draft never seals
    rb.write_text(json.dumps(_fill_readback(draft, repo)), encoding="utf-8")
    code, out = _seal_readback(capsys, rb, handoff)
    assert code == 0, out


def test_critical_end_to_end(repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """new --tier critical -> fill -> seal -> readback-new (as the addressed agent) -> fill -> seal."""
    _as(monkeypatch, "sender-1")
    handoff = _sealed_handoff(capsys, "--tier", "critical")
    doc = load(handoff)
    assert doc["to"] == {"id": "claude-code:next", "kind": "agent"}  # the continuing agent, not the operator
    assert doc["readback"]["verifier"] == {"id": "operator", "kind": "human"}  # X-17: verifier != receiver
    _as(monkeypatch, "receiver-2")
    rb = _readback_new(capsys, handoff)
    draft = load(rb)
    assert draft["by"] == doc["to"]  # X-13: the read-back author is the addressed receiver
    assert "not_checked" not in draft["reverified"][0]["result"]  # X-9: not offered at critical
    assert "raw" in draft["reverified"][0]["evidence"]  # X-14
    assert [c["index"] for c in draft["constraints_restated"]] == [0]  # X-18
    rb.write_text(json.dumps(_fill_readback(draft, repo)), encoding="utf-8")
    code, out = _seal_readback(capsys, rb, handoff)
    assert code == 0, out
    assert validate(load(rb), load(handoff)) == []


def test_critical_readback_with_not_checked_or_no_raw_is_refused(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, "sender-1")
    handoff = _sealed_handoff(capsys, "--tier", "critical")
    _as(monkeypatch, "receiver-2")
    rb = _readback_new(capsys, handoff)
    filled = _fill_readback(load(rb), repo)
    unchecked = json.loads(json.dumps(filled))
    unchecked["reverified"][0] = {"claim_id": "c1", "result": "not_checked"}
    rb.write_text(json.dumps(unchecked), encoding="utf-8")
    code, out = _seal_readback(capsys, rb, handoff)
    assert code == 1 and "X-9" in out
    filled["reverified"][0]["evidence"].pop("raw")
    rb.write_text(json.dumps(filled), encoding="utf-8")
    code, out = _seal_readback(capsys, rb, handoff)
    assert code == 1 and "X-14" in out


def test_readback_new_refuses_a_record_addressed_to_someone_else(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, "sender-1")
    handoff = _sealed_handoff(capsys, "--tier", "critical", "--to-id", "codex:next")
    _as(monkeypatch, "receiver-2")  # a Claude Code session, not codex
    code, _, err = _run(capsys, "readback-new", str(handoff))
    assert code == 2 and "addressed to codex:next" in err
    rb = _readback_new(capsys, handoff, "--as-addressee")  # the user confirmed this session is codex:next
    assert load(rb)["by"] == {"id": "codex:next", "kind": "agent"}


def test_new_refuses_the_operator_as_addressee(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = _run(capsys, "new", "--subject", "s", "--tier", "critical", "--to-id", "operator")
    assert code == 2 and "verifier" in err


def test_readback_new_prefills_drift_discrepancies(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _as(monkeypatch, "sender-1")
    handoff = _sealed_handoff(capsys, "--tier", "standard")
    (repo / "a.txt").write_text("edited\n", encoding="utf-8")
    _as(monkeypatch, "receiver-2")
    draft = load(_readback_new(capsys, handoff))
    assert draft["pointer_checks"][0]["status"] == "drift"
    assert draft["discrepancies"][0]["pointer_index"] == 0 and PLACEHOLDER in draft["discrepancies"][0]["text"]
