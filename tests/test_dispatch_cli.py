"""Behavior tests for the ``trw-mcp dispatch`` CLI handler."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from trw_mcp.dispatch import DispatchResult
from trw_mcp.dispatch._cli import run_dispatch


def _ns(**kw: object) -> argparse.Namespace:
    base: dict[str, object] = {
        "client": "codex",
        "prompt": "review this",
        "prompt_file": None,
        "role": None,
        "model": None,
        "cwd": None,
        "timeout": 600,
        "output_file": None,
        "no_isolate": False,
        "allow_writes": False,
        "pty": False,
        "json": False,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _fake_result(text: str = "All good.", *, ok_exit: int = 0) -> DispatchResult:
    return DispatchResult(
        client="codex",
        argv_redacted=["codex", "exec", "<prompt:11 chars>"],
        read_only_enforced=True,
        exit_code=ok_exit,
        timed_out=False,
        duration_s=0.1,
        text=text,
        raw_stdout=text,
        raw_stderr="",
        structured=None,
    )


def test_gemini_client_rejected_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(client="gemini"))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "agy" in err
    assert "EOL" in err


def test_prints_text_and_exits_zero_on_ok(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("The answer."))
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns())
    assert exc.value.code == 0
    assert "The answer." in capsys.readouterr().out


def test_exit_one_when_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("", ok_exit=1))
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns())
    assert exc.value.code == 1


def test_json_flag_prints_full_result(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("X"))
    with pytest.raises(SystemExit):
        run_dispatch(_ns(json=True))
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["client"] == "codex"
    assert parsed["ok"] is True


def test_output_file_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("X"))
    out_file = tmp_path / "result.json"
    with pytest.raises(SystemExit):
        run_dispatch(_ns(output_file=str(out_file)))
    data = json.loads(out_file.read_text())
    assert data["text"] == "X"


def test_prompt_file_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _capture(req: object) -> DispatchResult:
        captured["prompt"] = getattr(req, "prompt")
        return _fake_result("ok")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _capture)
    pf = tmp_path / "p.txt"
    pf.write_text("prompt from file")
    with pytest.raises(SystemExit):
        run_dispatch(_ns(prompt=None, prompt_file=str(pf)))
    assert captured["prompt"] == "prompt from file"


def test_role_applied_to_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _capture(req: object) -> DispatchResult:
        captured["prompt"] = getattr(req, "prompt")
        return _fake_result("ok")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _capture)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(role="adversarial-audit", prompt="check X"))
    prompt = str(captured["prompt"])
    assert prompt.endswith("check X")
    assert "read-only" in prompt.lower()


def test_both_prompt_and_file_is_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    pf = tmp_path / "p.txt"
    pf.write_text("x")
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(prompt="inline", prompt_file=str(pf)))
    assert exc.value.code == 2


def test_no_prompt_at_all_is_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(prompt=None, prompt_file=None))
    assert exc.value.code == 2


def test_prompt_file_too_large_exits_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    big = tmp_path / "big.txt"
    big.write_text("A" * 1_000_001)  # one byte over the 1MB ceiling
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(prompt=None, prompt_file=str(big)))
    assert exc.value.code == 2
    assert "too large" in capsys.readouterr().err


def test_prompt_file_missing_exits_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.txt"
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(prompt=None, prompt_file=str(missing)))
    assert exc.value.code == 2
    assert "Cannot read --prompt-file" in capsys.readouterr().err


def test_prompt_file_unreadable_exits_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    # A directory passes stat() but read_text() raises IsADirectoryError (OSError)
    # — the second read failure branch must also exit 2 cleanly.
    a_dir = tmp_path / "iam-a-dir"
    a_dir.mkdir()
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(prompt=None, prompt_file=str(a_dir)))
    assert exc.value.code == 2
    assert "Cannot read --prompt-file" in capsys.readouterr().err


def test_output_file_nested_dir_is_created(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("X"))
    nested = tmp_path / "a" / "b" / "c" / "result.json"
    with pytest.raises(SystemExit):
        run_dispatch(_ns(output_file=str(nested)))
    assert nested.exists()
    assert json.loads(nested.read_text())["text"] == "X"
