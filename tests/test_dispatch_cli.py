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


def test_unknown_client_rejected_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(client="not-a-real-cli"))
    assert exc.value.code == 2
    assert "disabled" in capsys.readouterr().err


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


def test_output_file_symlink_is_refused_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[object] = []
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda req: calls.append(req) or _fake_result("X"))
    target = tmp_path / "precious.txt"
    target.write_text("keep me", encoding="utf-8")
    link = tmp_path / "result.json"
    link.symlink_to(target)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(output_file=str(link)))
    assert exc.value.code == 2
    assert "symlink" in capsys.readouterr().err
    assert calls == []
    assert target.read_text(encoding="utf-8") == "keep me"


def test_output_file_write_never_follows_a_symlink_swapped_in_during_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "precious.txt"
    target.write_text("keep me", encoding="utf-8")
    out_file = tmp_path / "result.json"

    def _swap(_req: object) -> DispatchResult:
        out_file.symlink_to(target)
        return _fake_result("X")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _swap)
    with pytest.raises(SystemExit):
        run_dispatch(_ns(output_file=str(out_file)))
    assert target.read_text(encoding="utf-8") == "keep me"
    assert not out_file.is_symlink()
    assert json.loads(out_file.read_text())["text"] == "X"
    assert out_file.stat().st_mode & 0o777 == 0o600


# --- PRD-CORE-299-FR04: --variant-of writes a named, provenance-stamped variant ---


def _variant_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    base = tmp_path / "docs" / "research" / "design.md"
    base.parent.mkdir(parents=True)
    base.write_text("# design\n")
    return base


def test_variant_of_writes_next_free_round_without_overwrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _variant_env(monkeypatch, tmp_path)
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("Finding one."))
    for _ in range(2):
        with pytest.raises(SystemExit) as exc:
            run_dispatch(_ns(variant_of=str(base), role="code-review"))
        assert exc.value.code == 0
    first = base.parent / "design.review-codex-r1.md"
    second = base.parent / "design.review-codex-r2.md"
    assert first.is_file() and second.is_file()
    text = first.read_text()
    assert "producer: codex" in text and "role: code-review" in text and "ok: true" in text
    assert text.rstrip().endswith("Finding one.")
    assert second.name in capsys.readouterr().err


def test_variant_of_without_role_is_notes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = _variant_env(monkeypatch, tmp_path)
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("x"))
    with pytest.raises(SystemExit):
        run_dispatch(_ns(variant_of=str(base)))
    assert (base.parent / "design.notes-codex-r1.md").is_file()


def test_failed_dispatch_still_leaves_a_variant_marked_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = _variant_env(monkeypatch, tmp_path)
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: _fake_result("", ok_exit=1))
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(variant_of=str(base), role="adversarial-audit"))
    assert exc.value.code == 1
    text = (base.parent / "design.audit-codex-r1.md").read_text()
    assert "ok: false" in text
    assert "dispatch failed" in text.lower()


def test_variant_of_discovery_directory_exits_2_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    rule = tmp_path / ".claude" / "rules" / "testing.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("x")
    called: list[object] = []

    def _record(req: object) -> DispatchResult:
        called.append(req)
        return _fake_result("x")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _record)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(variant_of=str(rule)))
    assert exc.value.code == 2
    assert called == []
    assert ".claude/rules" in capsys.readouterr().err
    assert sorted(p.name for p in rule.parent.iterdir()) == ["testing.md"]


def test_variant_of_base_that_reads_as_a_variant_exits_2_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    base = tmp_path / "design.review.md"
    base.write_text("x")
    called: list[object] = []

    def _record(req: object) -> DispatchResult:
        called.append(req)
        return _fake_result("x")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _record)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(variant_of=str(base)))
    assert exc.value.code == 2
    assert called == []
    assert "already reads as a variant" in capsys.readouterr().err
    assert [p.name for p in tmp_path.iterdir()] == ["design.review.md"]


def test_variant_of_symlink_to_a_variant_named_base_exits_2_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    (tmp_path / "design.review.md").write_text("x")
    alias = tmp_path / "alias.md"
    alias.symlink_to(tmp_path / "design.review.md")
    called: list[object] = []

    def _record(req: object) -> DispatchResult:
        called.append(req)
        return _fake_result("x")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", _record)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(variant_of=str(alias)))
    assert exc.value.code == 2
    assert called == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["alias.md", "design.review.md"]


def test_variant_write_rejecting_the_producer_exits_2_with_write_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _variant_env(monkeypatch, tmp_path)
    # model_copy skips validation, so an id the slug grammar refuses reaches write_variant.
    bad = _fake_result("answer").model_copy(update={"client": "Bad_Client"})
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", lambda _req: bad)
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(variant_of=str(base)))
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--variant-of write failed" in err and "Bad_Client" in err
    assert [p.name for p in base.parent.iterdir()] == ["design.md"]


def test_variant_of_missing_base_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        run_dispatch(_ns(variant_of=str(tmp_path / "nope.md")))
    assert exc.value.code == 2


def test_variant_of_parses_from_argv() -> None:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    args = _build_arg_parser().parse_args(["dispatch", "--prompt", "p", "--variant-of", "docs/x.md"])
    assert args.variant_of == "docs/x.md"
