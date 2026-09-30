"""FEEDBACK-LOCAL-OUTBOX: a submission survives a failed send, is never tracked, and is not sent twice by accident."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.tools.submit_feedback import SubmitFeedbackResult

pytestmark = pytest.mark.usefixtures("governing_project")

_SECRET = "sk-ant-api03-" + "A" * 40


class _Cfg:
    resolved_backend_url = "https://api.trw.test"
    resolved_backend_api_key = "key-xyz"


def _trw() -> Path:
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir()


def _send(result: SubmitFeedbackResult, **kwargs: Any) -> tuple[SubmitFeedbackResult, Any]:
    from trw_mcp.tools.submit_feedback import submit_feedback

    args: dict[str, Any] = {"category": "bugfix", "subject": "Recall hangs", "message": f"body with {_SECRET} inside"}
    args.update(kwargs)
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=result) as http,
    ):
        return submit_feedback(**args), http


def _files(sub: str) -> list[Path]:
    return sorted((_trw() / "feedback" / sub).glob("*.json"))


_FAIL = SubmitFeedbackResult(success=False, error="transport error: ConnectError", status_code=0)
_OK = SubmitFeedbackResult(success=True, submission_id="sub_1", status_code=200)


def test_failed_send_keeps_redacted_outbox_record() -> None:
    result, _ = _send(_FAIL)
    assert result.success is False
    [record_path] = _files("outbox")
    text = record_path.read_text(encoding="utf-8")
    assert _SECRET not in text
    record = json.loads(text)
    assert record["attempts"] == 1 and record["last_error"] == "transport error"
    assert record["payload"]["subject"] == "Recall hangs"
    assert record_path.stat().st_mode & 0o777 == 0o600
    assert result.outbox_id == record["id"]


def test_successful_send_moves_record_to_sent() -> None:
    result, _ = _send(_OK)
    assert result.success is True
    assert _files("outbox") == []
    [sent] = _files("sent")
    assert json.loads(sent.read_text(encoding="utf-8"))["submission_id"] == "sub_1"


def test_one_submit_is_one_send_no_opportunistic_flush() -> None:
    _send(_FAIL)
    _, http = _send(_FAIL, subject="Another issue")
    assert http.call_count == 1  # the earlier failed record is not resent by this submit
    assert len(_files("outbox")) == 2


def test_duplicate_within_window_points_to_earlier_report() -> None:
    _send(_OK)
    result, http = _send(_OK, subject="  recall HANGS ")
    http.assert_not_called()
    assert result.success is False
    assert result.duplicate_of == {"id": json.loads(_files("sent")[0].read_text())["id"], "subject": "Recall hangs",
                                   "submission_id": "sub_1"}  # fmt: skip


def test_duplicate_of_a_pending_outbox_record_is_also_refused() -> None:
    _send(_FAIL)
    result, http = _send(_OK)
    http.assert_not_called()
    assert result.duplicate_of["subject"] == "Recall hangs"


def test_force_sends_a_duplicate() -> None:
    _send(_OK)
    result, http = _send(_OK, force=True)
    assert http.call_count == 1 and result.success is True


def test_list_is_read_only_and_flush_retries_oldest_first() -> None:
    from trw_mcp.tools._feedback_cli import flush_outbox, list_outbox

    _send(_FAIL)
    _send(_FAIL, subject="Second")
    before = {p: p.read_bytes() for p in _files("outbox")}
    listed = list_outbox()
    assert [e["subject"] for e in listed["pending"]] == ["Recall hangs", "Second"]
    assert {p: p.read_bytes() for p in _files("outbox")} == before

    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", side_effect=[_OK, _FAIL]) as http,
    ):
        report = flush_outbox(limit=5)
    assert http.call_args_list[0].kwargs["payload"]["subject"] == "Recall hangs"
    assert [r["sent"] for r in report["results"]] == [True, False]
    [left] = _files("outbox")
    assert json.loads(left.read_text())["attempts"] == 2


def test_flush_limit_bounds_the_sends() -> None:
    from trw_mcp.tools._feedback_cli import flush_outbox

    _send(_FAIL)
    _send(_FAIL, subject="Second")
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_OK) as http,
    ):
        flush_outbox(limit=1)
    assert http.call_count == 1 and len(_files("outbox")) == 1


@pytest.mark.parametrize("brownfield", [False, True], ids=["fresh-install", "merged-custom-gitignore"])
def test_outbox_and_sent_are_git_ignored(tmp_path: Path, brownfield: bool) -> None:
    """OQ-024: a feedback record must never be tracked in any project the installer writes into."""
    from importlib.resources import files

    from trw_mcp.bootstrap._template_updater import _ensure_credentials_gitignored

    project = tmp_path / "proj"
    trw = project / ".trw"
    for sub in ("outbox", "sent"):
        (trw / "feedback" / sub).mkdir(parents=True)
        (trw / "feedback" / sub / "x.json").write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    if brownfield:
        (trw / ".gitignore").write_text("logs/\n", encoding="utf-8")
        _ensure_credentials_gitignored(project, {"updated": [], "created": [], "errors": []})
    else:
        bundled = files("trw_mcp.data").joinpath("gitignore.txt").read_text(encoding="utf-8")
        (trw / ".gitignore").write_text(bundled, encoding="utf-8")
    for sub in ("outbox", "sent"):
        done = subprocess.run(["git", "check-ignore", f".trw/feedback/{sub}/x.json"], cwd=project, check=False)
        assert done.returncode == 0, sub


def test_cli_list_is_reviewer_safe_and_flush_is_not() -> None:
    import argparse

    from trw_mcp.server._cli_reviewer_policy import is_reviewer_safe

    assert is_reviewer_safe("feedback list", argparse.Namespace()) is True
    assert is_reviewer_safe("feedback flush", argparse.Namespace()) is False


def test_cli_flush_exits_nonzero_when_a_report_stays_unsent(capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    _send(_FAIL)
    args = _build_arg_parser().parse_args(["feedback", "flush", "--json"])
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_FAIL),
        pytest.raises(SystemExit) as done,
    ):
        SUBCOMMAND_HANDLERS["feedback"](args)
    assert done.value.code == 1
    assert json.loads(capsys.readouterr().out)["results"][0]["sent"] is False


def test_no_trw_dir_stores_nothing_and_creates_no_trw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Creating .trw would itself turn platform contact on for that directory."""
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: bare / ".trw")
    result, _ = _send(_FAIL)
    assert result.outbox_id == "" and not (bare / ".trw").exists()


def test_flush_re_redacts_a_hand_edited_record() -> None:
    """codex r1 block: the stored file is not trusted; a secret written into it never reaches the wire."""
    from trw_mcp.tools._feedback_cli import flush_outbox

    _send(_FAIL, metadata={"custom": "value"})
    [path] = _files("outbox")
    record = json.loads(path.read_text())
    record["payload"]["message"] = f"edited body {_SECRET}"
    record["payload"]["metadata"]["note"] = _SECRET
    path.write_text(json.dumps(record))
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_OK) as http,
    ):
        report = flush_outbox(limit=5)
    assert report["results"][0]["sent"] is True
    assert _SECRET not in json.dumps(http.call_args.kwargs["payload"])


def test_flush_refuses_a_record_that_no_longer_validates() -> None:
    from trw_mcp.tools._feedback_cli import flush_outbox

    _send(_FAIL)
    [path] = _files("outbox")
    record = json.loads(path.read_text())
    record["payload"]["category"] = "system_takeover"
    path.write_text(json.dumps(record))
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_OK) as http,
    ):
        report = flush_outbox(limit=5)
    http.assert_not_called()
    assert "invalid" in report["results"][0]["error"]


def test_delivered_record_is_never_resent_when_sent_copy_cannot_be_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """codex r1 KI: a bookkeeping failure after a 200 must not let flush replay the report."""
    from trw_mcp.tools import _feedback_outbox
    from trw_mcp.tools._feedback_cli import flush_outbox

    real_write = _feedback_outbox._write
    monkeypatch.setattr(_feedback_outbox, "_write", lambda p, r: False if p.parent.name == "sent" else real_write(p, r))
    _send(_OK)
    [path] = _files("outbox")
    assert json.loads(path.read_text())["submission_id"] == "sub_1"
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_OK) as http,
    ):
        flush_outbox(limit=5)
    http.assert_not_called()


def test_malformed_records_do_not_break_list_or_submit() -> None:
    """codex r2 KI: a bad record is skipped, never an exception in an unrelated submit or listing."""
    from trw_mcp.tools._feedback_cli import list_outbox

    outbox = _trw() / "feedback" / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "0-list.json").write_text(json.dumps({"id": "a", "payload": ["x"]}))
    (outbox / "1-naive.json").write_text(json.dumps({"id": "b", "created_at": "2026-09-30T00:00:00", "attempts": "x",
                                                    "payload": {"category": "bugfix", "subject": "Recall hangs"}}))  # fmt: skip
    (outbox / "2-junk.json").write_text("not json")
    result, http = _send(_OK)
    assert http.call_count == 1 and result.success is True
    assert [e["id"] for e in list_outbox()["pending"]] == ["b"]


def test_a_symlinked_outbox_is_neither_read_nor_deleted_through(tmp_path: Path) -> None:
    """codex r2 block: a planted symlink must not turn a containment refusal into a delete outside the project."""
    from trw_mcp.tools._feedback_cli import flush_outbox, list_outbox

    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "20260101T000000000000Z-deadbeef.json"
    victim.write_text(json.dumps({"id": "v", "created_at": "2026-09-30T00:00:00+00:00", "attempts": 0,
                                  "payload": {"category": "bugfix", "subject": "s", "message": "m" * 20}}))  # fmt: skip
    (_trw() / "feedback").mkdir(parents=True, exist_ok=True)
    (_trw() / "feedback" / "outbox").symlink_to(outside, target_is_directory=True)
    assert list_outbox()["pending"] == []
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_OK) as http,
    ):
        flush_outbox(limit=5)
    http.assert_not_called()
    assert victim.exists()


def test_a_symlinked_record_is_never_sent(tmp_path: Path) -> None:
    """codex r3 block: a leaf symlink in a contained outbox must not send a file outside the project."""
    from trw_mcp.tools._feedback_cli import flush_outbox, list_outbox

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"id": "f", "created_at": "2026-09-30T00:00:00+00:00", "attempts": 0,
                                   "payload": {"category": "bugfix", "subject": "s", "message": "m" * 20}}))  # fmt: skip
    outbox = _trw() / "feedback" / "outbox"
    outbox.mkdir(parents=True)
    (outbox / "20260101T000000000000Z-deadbeef.json").symlink_to(foreign)
    assert list_outbox()["pending"] == []
    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", return_value=_OK) as http,
    ):
        flush_outbox(limit=5)
    http.assert_not_called()
    assert foreign.exists()


def test_read_refuses_a_leaf_swapped_to_a_symlink_after_listing(tmp_path: Path) -> None:
    """lead TOCTOU: _read opens without following, so a swap after the is_symlink pre-check is not read."""
    from trw_mcp.tools._feedback_outbox import _read

    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"id": "f", "payload": {"category": "bugfix", "subject": "s"}}))
    leaf = tmp_path / "leaf.json"
    leaf.symlink_to(foreign)
    assert _read(leaf) is None


# --- lead adversarial audit of 0ae2a8ed45 -------------------------------------------------------------------


def _flush(side_effect: Any) -> tuple[dict[str, Any], Any]:
    from trw_mcp.tools._feedback_cli import flush_outbox

    with (
        patch("trw_mcp.models.config.get_config", return_value=_Cfg()),
        patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", side_effect=side_effect) as http,
    ):
        return flush_outbox(limit=5), http


def test_contact_email_is_never_stored_and_a_retry_says_it_went_without() -> None:
    """audit P1: the contact address is not written to outbox/ or sent/; a flushed retry reports its absence."""
    _send(_FAIL, contact_email="someone@example.org")
    [path] = _files("outbox")
    assert "someone@example.org" not in path.read_text()
    report, http = _flush([_OK])
    assert "contact_email" not in http.call_args.kwargs["payload"]
    assert "without contact_email" in report["results"][0]["note"]
    [sent] = _files("sent")
    assert "someone@example.org" not in sent.read_text()


def _returns_within(fn: Any, seconds: float = 5.0) -> bool:
    import threading

    worker = threading.Thread(target=fn, daemon=True)
    worker.start()
    worker.join(seconds)
    return not worker.is_alive()


def test_a_fifo_at_a_record_path_hangs_neither_submit_nor_list() -> None:
    """audit P2: records() lstats before opening, so a FIFO is never opened (and a raced one never blocks)."""
    import os

    from trw_mcp.tools._feedback_cli import list_outbox
    from trw_mcp.tools._feedback_outbox import _read

    outbox = _trw() / "feedback" / "outbox"
    outbox.mkdir(parents=True)
    os.mkfifo(outbox / "20260101T000000000000Z-fifo.json")
    assert _returns_within(list_outbox)
    assert _returns_within(lambda: _send(_OK, subject="Other"))
    assert _returns_within(lambda: _read(outbox / "20260101T000000000000Z-fifo.json"))


def test_a_record_is_sent_once_under_a_concurrent_submit_or_flush() -> None:
    """audit P2: a flush during an in-flight submit, or during another flush, never sends the same record."""
    inner: list[dict[str, Any]] = []

    def send_while_flushing(**_kwargs: Any) -> SubmitFeedbackResult:
        from trw_mcp.tools._feedback_cli import flush_outbox

        inner.append(flush_outbox(limit=5))  # the in-flight record is claimed: nothing to send
        return _FAIL

    with patch("trw_mcp.tools.submit_feedback.submit_feedback_via_http", side_effect=send_while_flushing):
        with patch("trw_mcp.models.config.get_config", return_value=_Cfg()):
            from trw_mcp.tools.submit_feedback import submit_feedback

            submit_feedback(category="bugfix", subject="Recall hangs", message="a long enough body text")
    assert inner == [{"error": "", "results": []}]
    inner.clear()
    report, http = _flush(send_while_flushing)  # the outer flush holds the record while the inner one runs
    assert http.call_count == 1 and inner == [{"error": "", "results": []}]
    assert report["results"][0]["sent"] is False


def test_a_stale_claim_returns_to_pending_at_the_next_flush() -> None:
    import os
    import time

    _send(_FAIL)
    [path] = _files("outbox")
    claimed = path.with_suffix(".sending")
    path.rename(claimed)
    report, _ = _flush([_OK])
    assert report["results"] == []  # a fresh claim is left to its owner
    old = time.time() - 3600
    os.utime(claimed, (old, old))
    report, http = _flush([_OK])
    assert http.call_count == 1 and report["results"][0]["sent"] is True


def test_duplicate_of_an_undelivered_record_says_queued_not_reported() -> None:
    """audit P2: a pending record was never delivered; the refusal must not claim it was."""
    _send(_FAIL)
    result, _ = _send(_OK)
    assert "already queued" in result.error and "not yet delivered" in result.error and "flush" in result.error
    assert "already reported" not in result.error
    _flush([_OK])
    result, _ = _send(_OK)
    assert "already reported" in result.error


def test_feedback_dir_ignores_itself_from_the_first_enqueue(tmp_path: Path) -> None:
    """audit P2: records are ignored even in a project that never ran init or update-project."""
    from trw_mcp.tools._feedback_outbox import enqueue

    project = tmp_path / "plain"
    (project / ".trw").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    path = enqueue(project / ".trw", {"category": "bugfix", "subject": "s", "message": "m"}, contact_dropped=False)
    assert path is not None
    rel = path.relative_to(project)
    assert subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=project, check=False).returncode == 0
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=project,
                            capture_output=True, text=True, check=True).stdout  # fmt: skip
    assert "feedback" not in status


def test_email_and_platform_tokens_are_redacted_everywhere_before_storage_and_send() -> None:
    """audit P3 pin: subject, message and metadata carry neither an e-mail nor a trw_ key, on disk or on the wire."""
    email, token = "leak@example.org", "trw_" + "B" * 30
    _, http = _send(_OK, subject=f"Leak {email} {token}", message=f"body {email} and {token} here",
                    metadata={"note": f"{email} {token}"})  # fmt: skip
    wire = json.dumps(http.call_args.kwargs["payload"])
    [stored] = _files("sent")
    for text in (wire, stored.read_text()):
        assert email not in text and token not in text


# --- lead 2nd audit of dc1a24a01d ----------------------------------------------------------------------------

_OK_NO_ID = SubmitFeedbackResult(success=True, submission_id="", status_code=200)


def test_a_200_without_an_id_is_delivered_not_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    """audit-2 P2: delivery is the 200 (and sent/ membership), not a non-empty id; it is never resent."""
    from trw_mcp.tools import _feedback_outbox

    _send(_OK_NO_ID)
    [sent] = _files("sent")
    assert json.loads(sent.read_text())["submission_id"] == _feedback_outbox.UNKNOWN_ID
    result, http = _send(_OK)
    http.assert_not_called()
    assert "already reported" in result.error and "not yet delivered" not in result.error

    real_write = _feedback_outbox._write
    monkeypatch.setattr(_feedback_outbox, "_write", lambda p, r: False if p.parent.name == "sent" else real_write(p, r))
    _send(_OK_NO_ID, subject="Other issue")
    _, http = _flush([_OK])
    http.assert_not_called()  # the fallback pending copy carries the sentinel, so flush skips it


def test_claim_rechecks_delivery_under_the_claim() -> None:
    """audit-2 P3a: a record delivered after it was listed is handed back, never claimed for a resend."""
    from trw_mcp.tools._feedback_outbox import claim

    _send(_FAIL)
    [path] = _files("outbox")
    record = json.loads(path.read_text())
    record["submission_id"] = "sub_late"
    path.write_text(json.dumps(record))
    assert claim(path) is None
    assert _files("outbox") == [path]


def test_a_settle_finishes_only_its_own_claim() -> None:
    """audit-2 P3b: after a stale release and a re-claim, the first sender's settle cannot finish the second's."""
    from trw_mcp.tools._feedback_outbox import claim, settle

    _send(_FAIL)
    [path] = _files("outbox")
    first = claim(path)
    assert first is not None
    first[0].rename(path)  # released as stale while the first sender is still in flight
    second = claim(path)
    assert second is not None and second[0] != first[0]
    settle(first[0], success=True, submission_id="sub_1", error="", status_code=200)
    assert second[0].exists() and _files("sent") == []


def test_a_corrupt_claim_is_listed_and_released() -> None:
    """audit-2 P3c: an unreadable .sending is shown by list and returned to pending once stale."""
    import os
    import time

    from trw_mcp.tools._feedback_cli import list_outbox

    outbox = _trw() / "feedback" / "outbox"
    outbox.mkdir(parents=True)
    corrupt = outbox / "20260101T000000000000Z-bad.1-ab.sending"
    corrupt.write_text("not json")
    assert corrupt.name in list_outbox()["unreadable"]
    old = time.time() - 3600
    os.utime(corrupt, (old, old))
    _flush([])
    assert (outbox / "20260101T000000000000Z-bad.json").exists() and not corrupt.exists()
