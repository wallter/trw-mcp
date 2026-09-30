"""PRD-CORE-347-FR05: the ``trw-mcp handoff`` family through the real parser and handler table."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest

from tests.handoff._vectors import CRITICAL, STANDARD, VECTORS
from trw_mcp.handoff import digest, load
from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    args = _build_arg_parser().parse_args(["handoff", *argv])
    with pytest.raises(SystemExit) as exc:
        SUBCOMMAND_HANDLERS["handoff"](args)
    out, err = capsys.readouterr()
    return int(exc.value.code or 0), out, err


def test_validate_valid_record_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(capsys, "validate", str(STANDARD)) == (0, "", "")


def test_validate_readback_with_handoff(capsys: pytest.CaptureFixture[str]) -> None:
    readback = VECTORS / "valid" / "07-readback-for-04-critical.json"
    code, _, _ = _run(capsys, "validate", str(readback), "--handoff", str(CRITICAL))
    assert code == 0


def test_validate_invalid_record_prints_rule_ids_as_json_lines(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(capsys, "validate", str(VECTORS / "invalid" / "x02-duplicate-id.json"))
    assert code == 1
    rules = [json.loads(line)["rule"] for line in out.splitlines()]
    assert "X-2" in rules


def test_validate_parse_failure_is_a_finding(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(capsys, "validate", str(VECTORS / "invalid" / "p01-duplicate-member.json"))
    assert code == 1
    assert json.loads(out.splitlines()[0])["rule"] == "R-INT-4"


def test_readback_without_handoff_is_an_input_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(capsys, "validate", str(VECTORS / "valid" / "02-readback-for-01.json"))
    assert (code, out) == (2, "")
    assert "needs its handoff" in err


@pytest.mark.parametrize("content", [None, b"\xff\xfe not utf-8"])
def test_missing_or_non_utf8_file_fails_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes | None
) -> None:
    path = tmp_path / "rec.json"
    if content is not None:
        path.write_bytes(content)
    code, out, err = _run(capsys, "validate", str(path))
    assert "Traceback" not in out + err
    assert code == (2 if content is None else 1)


def test_digest_prints_sha256(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(capsys, "digest", str(STANDARD))
    assert (code, out.strip()) == (0, load(STANDARD)["integrity"]["digest"])


def test_seal_writes_digest_atomically_and_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "rec.json"
    doc = load(STANDARD)
    doc["integrity"]["digest"] = "sha256:" + "0" * 64  # stale digest
    path.write_text(json.dumps(doc), encoding="utf-8")
    code, out, _ = _run(capsys, "seal", str(path))
    assert code == 0
    first = path.read_bytes()
    assert out.strip() == load(path)["integrity"]["digest"] == digest(doc)
    assert _run(capsys, "seal", str(path))[0] == 0
    assert path.read_bytes() == first
    assert list(tmp_path.iterdir()) == [path]  # no temp file left behind
    assert _run(capsys, "validate", str(path))[0] == 0


def test_seal_keeps_file_mode_and_writes_through_symlinks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "rec.json"
    shutil.copy(STANDARD, target)
    target.chmod(0o644)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert _run(capsys, "seal", str(link))[0] == 0
    assert link.is_symlink()
    assert target.stat().st_mode & 0o777 == 0o644


def test_seal_with_unparseable_handoff_is_an_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rec = tmp_path / "rb.json"
    shutil.copy(VECTORS / "valid" / "02-readback-for-01.json", rec)
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert _run(capsys, "seal", str(rec), "--handoff", str(bad))[0] == 2


def test_seal_refuses_an_invalid_record_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "rec.json"
    shutil.copy(VECTORS / "invalid" / "x02-duplicate-id.json", path)
    before = path.read_bytes()
    code, out, err = _run(capsys, "seal", str(path))
    assert code == 1
    assert "X-2" in out and "refused" in err
    assert path.read_bytes() == before


def test_seal_refuses_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "ev.json"
    shutil.copy(VECTORS / "valid" / "05-event-accepted.json", path)
    assert _run(capsys, "seal", str(path))[0] == 2


def test_no_verb_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    args = _build_arg_parser().parse_args(["handoff"])
    with pytest.raises(SystemExit) as exc:
        SUBCOMMAND_HANDLERS["handoff"](args)
    assert exc.value.code == 2


def test_seal_reports_a_refused_unsafe_write_as_an_input_error_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    from trw_memory.safe_fs import UnsafeWriteError

    from tests.handoff._vectors import STANDARD
    from trw_mcp import _checkout_write
    from trw_mcp.server import _subcommands_handoff as handlers

    target = tmp_path / "record.json"
    shutil.copy(STANDARD, target)
    before = target.read_bytes()

    def refuse(*_a: object, **_k: object) -> None:
        raise UnsafeWriteError("refused", path=str(tmp_path), reason="symlink_component")

    monkeypatch.setattr(_checkout_write, "write_checkout_file", refuse)
    args = argparse.Namespace(handoff_command="seal", file=str(target), handoff=None)

    with pytest.raises(SystemExit) as done:
        handlers.run_handoff(args)

    assert done.value.code == 2 and "input error" in capsys.readouterr().err
    assert target.read_bytes() == before
