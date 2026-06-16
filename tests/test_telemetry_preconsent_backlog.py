"""PRD-SEC-004-FR01 (pre-consent backlog exclusion): events recorded while
telemetry was OFF must never be uploaded after a later opt-in.

telemetry/sender.py + the writers tag each record with the consent state in
effect AT WRITE TIME. BatchSender.send() uploads only consented records and
DROPS the pre-consent backlog on the first consented flush, so flipping
platform_telemetry_enabled=true cannot wholesale-upload the historical queue.

Behavior tests: patch the HTTP transport and assert exactly which records POST.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from trw_mcp.telemetry.sender import BatchSender, stamp_consent


def _make_sender(tmp_path: Path) -> tuple[Any, Path]:
    input_path = tmp_path / "logs" / "tool-telemetry.jsonl"
    sender = BatchSender(
        platform_urls=["https://api.example.com"],
        input_path=input_path,
        batch_size=100,
        max_retries=1,
        backoff_base=0.0,
        platform_telemetry_enabled=True,  # opted IN now
    )
    return sender, input_path


def _write_raw(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _read(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_pre_consent_records_are_not_uploaded(tmp_path: Path) -> None:
    """Records written while telemetry was OFF (consent=False) never POST after opt-in."""
    sender, input_path = _make_sender(tmp_path)
    _write_raw(
        input_path,
        [
            stamp_consent({"tool": "trw_learn", "idx": 0}, consented=False),
            stamp_consent({"tool": "trw_learn", "idx": 1}, consented=False),
        ],
    )

    posted: list[list[dict[str, object]]] = []

    def _fake_post(url: str, payload: list[dict[str, object]]) -> bool:
        posted.append(payload)
        return True

    sender._http_post = _fake_post  # type: ignore[method-assign]
    result = sender.send()

    assert posted == [], "pre-consent backlog must not be uploaded"
    assert result["sent"] == 0
    assert result["skipped_reason"] == "no_consented_events"
    # The pre-consent backlog is dropped from the queue (can never be reconsidered).
    assert _read(input_path) == []


def test_legacy_untagged_records_are_fail_closed(tmp_path: Path) -> None:
    """An UNTAGGED record (no consent marker at all) is treated as pre-consent."""
    sender, input_path = _make_sender(tmp_path)
    _write_raw(input_path, [{"tool": "trw_learn", "idx": 0}])  # no marker

    posted: list[list[dict[str, object]]] = []
    sender._http_post = lambda url, payload: (posted.append(payload), True)[1]  # type: ignore[method-assign]
    result = sender.send()

    assert posted == []
    assert result["sent"] == 0


def test_only_consented_records_upload_marker_stripped(tmp_path: Path) -> None:
    """A mixed queue uploads ONLY the consented rows, and the local-only consent
    marker is stripped from the outgoing payload."""
    sender, input_path = _make_sender(tmp_path)
    _write_raw(
        input_path,
        [
            stamp_consent({"tool": "old", "idx": 0}, consented=False),
            stamp_consent({"tool": "new", "idx": 1}, consented=True),
        ],
    )

    posted: list[dict[str, object]] = []

    def _fake_post(url: str, payload: list[dict[str, object]]) -> bool:
        posted.extend(payload)
        return True

    sender._http_post = _fake_post  # type: ignore[method-assign]
    result = sender.send()

    assert result["sent"] == 1
    assert [p["idx"] for p in posted] == [1]
    # The local-only consent marker must not leave the machine.
    assert all("_trw_consent" not in p for p in posted)
    # Queue is drained (consented sent, pre-consent dropped).
    assert _read(input_path) == []
