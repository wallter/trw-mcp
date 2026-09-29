"""``trw_checkpoint`` refuses a malformed ``factory:1`` payload at write time (FACTORY-START-VALIDATE).

W1 recorded STARTs with ``"subject"`` instead of ``"attempt"``. The tool accepted them silently and they only surfaced
in ``factory status`` as a negative start_to_ready that excluded three attempts. The journal is append-only, so the
fix belongs at the write: a known kind, a required ``attempt``, the reader's own schema, and no unknown keys, each
refusal naming the key. New writes only; the reader's classification of history is unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state import _factory_experiment as fx

pytestmark = pytest.mark.integration

SHA = "a" * 40


@pytest.fixture
def run(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_project.parent))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "1")
    monkeypatch.setattr(fx, "_utc_now", lambda: datetime(2026, 9, 29, 12, tzinfo=timezone.utc))
    path = tmp_project / ".trw" / "runs" / "task" / "run1"
    (path / "meta").mkdir(parents=True)
    (path / "meta" / "run.yaml").write_text("run_id: run1\nstatus: active\n", encoding="utf-8")
    (path / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return path


def _write(run: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    return execute_checkpoint(str(run), json.dumps({"factory": 1, **payload}), None)  # type: ignore[return-value]


def _journal(run: Path) -> str:
    return (run / "meta" / "events.jsonl").read_text(encoding="utf-8")


def _refused(run: Path, payload: dict[str, Any], *fragments: str) -> None:
    before = _journal(run)
    result = _write(run, payload)
    assert result["recorded"] is False, result
    assert result["reason"] == "factory_payload_invalid" and result["error_type"] == "factory_payload_invalid"
    for fragment in fragments:
        assert fragment in str(result["remedy"]), (fragment, result["remedy"])
    assert _journal(run) == before, "a refused payload must not touch the journal"


def test_a_start_with_subject_instead_of_attempt_is_refused_naming_the_missing_key(run: Path) -> None:
    _refused(run, {"kind": "START", "subject": "loop-speed-X"}, "missing key: attempt", "subject")


def test_an_unknown_key_is_refused_naming_it(run: Path) -> None:
    _refused(run, {"kind": "START", "attempt": "a1", "subjct_sha": SHA}, "unknown key", "subjct_sha")


def test_an_unknown_kind_is_refused(run: Path) -> None:
    _refused(run, {"kind": "STOP", "attempt": "a1", "reason": "x"}, "unknown kind", "STOP")
    _refused(run, {"attempt": "a1"}, "unknown kind")


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"kind": "READY", "attempt": "a1", "receipts": {"verification": ["v1"]}}, "verification belongs to USED"),
        ({"kind": "START", "attempt": "a1", "receipts": {"build": ["b1"]}}, "START carries no receipts"),
        ({"kind": "USED", "attempt": "a1"}, "requires a nonempty receipts map"),
        ({"kind": "VOID", "attempt": "a1"}, "printable reason"),
        ({"kind": "START", "attempt": "has space"}, "attempt id must match"),
    ],
)
def test_the_readers_own_schema_is_enforced_at_write_time(run: Path, payload: dict[str, Any], fragment: str) -> None:
    _refused(run, payload, fragment)


def test_an_unknown_key_for_the_kind_is_refused_even_when_another_kind_allows_it(run: Path) -> None:
    _refused(run, {"kind": "VOID", "attempt": "a1", "reason": "r", "receipts": {"build": ["b1"]}}, "receipts")


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "START", "attempt": "a1"},
        {"kind": "START", "attempt": "a1", "subject_sha": SHA, "branch": "b", "base": "c", "note": "n"},
        {"kind": "START", "attempt": "a1", "model_id": "m", "tier": "frontier", "effort": "high", "client": "claude"},
        {"kind": "VOID", "attempt": "a1", "reason": "verifier FAIL"},
        {"kind": "VOID", "attempt": "a1", "reason": "verifier FAIL", "by": "swarm-verifier"},
    ],
)
def test_every_shape_the_workers_and_the_verifier_really_write_is_still_accepted(
    run: Path, payload: dict[str, Any]
) -> None:
    assert _write(run, payload)["recorded"] is True


def test_ready_and_used_with_real_receipts_are_still_accepted(run: Path, build_check_invoke: Any) -> None:
    built = build_check_invoke(tests_passed=True, scope="feature", run_path=str(run))
    ready = _write(
        run,
        {"kind": "READY", "attempt": "a1", "subject_sha": SHA, "receipts": {"build": [built["build_receipt_id"]]}},
    )
    assert ready["recorded"] is True, ready

    used_shape = {
        "kind": "USED",
        "attempt": "a1",
        "by": "swarm-verifier",
        "receipts": {"verification": [{"run_path": ".trw/runs/task/run1", "receipt_id": "verification-x"}]},
    }
    # The receipt does not exist, so the RECEIPT gate refuses it; the point is that the PAYLOAD gate does not.
    result = _write(run, used_shape)
    assert result["recorded"] is False and result["reason"] == "factory_receipt_unresolved", result


def test_non_factory_messages_are_never_touched(run: Path) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    for message in ("finished the parser", '{"factory": 2, "kind": "whatever"}', '{"note": "no discriminator"}'):
        assert execute_checkpoint(str(run), message, None)["recorded"] is True  # type: ignore[index]
