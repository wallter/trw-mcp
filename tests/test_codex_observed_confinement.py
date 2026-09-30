"""CODEX-P0-A S2 review round 1: the rollout reader stays inside ``$CODEX_HOME/sessions``, bounded.

Each test pins one reviewer finding (W1-CODEX-P0A-S2-r1): a symlink anywhere below the codex home is
refused (doctor and dispatch share one reader), a traversing ``CODEX_HOME`` is refused, the byte and line
budgets hold while reading, and a non-regular file (a FIFO) is refused without blocking.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

TID = "01a0f03e-df17-7592-8d16-98ffd97ef1e9"
DAY = Path("sessions") / "2026" / "09" / "29"
NAME = f"rollout-2026-09-29T20-57-27-{TID}.jsonl"


def _stdout() -> str:
    return json.dumps({"type": "thread.started", "thread_id": TID})


def _lines(cli: str = "0.99.1", *, secret: str = "SECRET PROMPT") -> str:
    rows = [
        {"type": "session_meta", "payload": {"cli_version": cli, "instructions": secret}},
        {"type": "response_item", "payload": {"content": secret}},
        {"type": "turn_context", "payload": {"model": "gpt-6.1-sol", "effort": "medium"}},
    ]
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def _outside(tmp_path: Path) -> Path:
    target = tmp_path / "outside" / NAME
    target.parent.mkdir(parents=True)
    target.write_text(_lines("OUTSIDE_SECRET"), encoding="utf-8")
    return target


def test_a_symlinked_rollout_is_refused_by_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    home = tmp_path / "home"
    (home / DAY).mkdir(parents=True)
    (home / DAY / NAME).symlink_to(_outside(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(home))
    status, message = codex_observation_row()
    assert "OUTSIDE_SECRET" not in message
    assert status != "PASS"


def test_a_symlinked_rollout_is_refused_by_dispatch(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex

    home = tmp_path / "home"
    (home / DAY).mkdir(parents=True)
    (home / DAY / NAME).symlink_to(_outside(tmp_path))
    seen = observe_codex(_stdout(), home)
    assert seen["cli_version"] == "unknown"


def test_a_symlinked_sessions_directory_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    real = tmp_path / "real"
    (real / DAY).mkdir(parents=True)
    (real / DAY / NAME).write_text(_lines("OUTSIDE_SECRET"), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    (home / "sessions").symlink_to(real / "sessions")
    assert observe_codex(_stdout(), home)["cli_version"] == "unknown"
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert "OUTSIDE_SECRET" not in codex_observation_row()[1]


@pytest.mark.parametrize("value", ["relative/home", "/outside/../outside/custom"])
def test_a_traversing_or_relative_codex_home_is_refused(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._codex_observed import codex_home_dir

    monkeypatch.setenv("CODEX_HOME", value)
    assert codex_home_dir() is None


def test_the_byte_budget_holds_while_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _codex_observed

    (tmp_path / DAY).mkdir(parents=True)
    (tmp_path / DAY / NAME).write_bytes(b"x" * 6_000_000 + b"\n" + _lines().encode())
    consumed: list[int] = []
    real_read = os.read

    def counting_read(fd: int, n: int) -> bytes:
        chunk = real_read(fd, n)
        consumed.append(len(chunk))
        return chunk

    monkeypatch.setattr(_codex_observed.os, "read", counting_read)
    seen = _codex_observed.observe_codex(_stdout(), tmp_path)
    assert sum(consumed) <= _codex_observed._MAX_BYTES
    assert seen["model"] == "unknown"


def test_the_line_budget_holds_while_reading(tmp_path: Path) -> None:
    from trw_mcp.dispatch import _codex_observed

    (tmp_path / DAY).mkdir(parents=True)
    filler = "{}\n" * _codex_observed._MAX_LINES
    (tmp_path / DAY / NAME).write_text(filler + _lines(), encoding="utf-8")
    assert _codex_observed.observe_codex(_stdout(), tmp_path)["model"] == "unknown"


def test_a_fifo_rollout_is_refused_without_blocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    (tmp_path / DAY).mkdir(parents=True)
    os.mkfifo(tmp_path / DAY / NAME)  # no writer: a blocking open would hang the test
    assert observe_codex(_stdout(), tmp_path)["reason"] == "rollout_unreadable"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_observation_row()[0] == "WARN"


def test_no_prompt_or_content_is_retained(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import with_observed

    (tmp_path / DAY).mkdir(parents=True)
    (tmp_path / DAY / NAME).write_text(_lines(), encoding="utf-8")
    out = with_observed({"model": {"applied": "a"}, "effort": {"applied": "b"}}, "codex", _stdout(), tmp_path)
    assert "SECRET" not in json.dumps(out)
    assert set(out["observation"]) == {"cli_version", "by"}


def test_observed_is_never_copied_from_applied(tmp_path: Path) -> None:
    """With no rollout at all, observed is ``unknown`` even though applied holds a value."""
    from trw_mcp.dispatch._codex_observed import with_observed

    policy = {"model": {"applied": "gpt-6.1-sol"}, "effort": {"applied": "medium"}}
    out = with_observed(policy, "codex", _stdout(), tmp_path)
    assert out["model"]["observed"] == out["effort"]["observed"] == "unknown"
    assert "mismatch" not in out["model"] and "mismatch" not in out["effort"]


def test_a_symlinked_codex_home_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 2 P0: the home's own final component (``~/.codex`` or ``$CODEX_HOME``) may not be a symlink."""
    from trw_mcp.dispatch._codex_observed import observe_codex
    from trw_mcp.server._doctor_codex_observation import codex_observation_row

    real = tmp_path / "elsewhere"
    (real / DAY).mkdir(parents=True)
    (real / DAY / NAME).write_text(_lines("OUTSIDE_SECRET"), encoding="utf-8")
    user = tmp_path / "user"
    user.mkdir()
    (user / ".codex").symlink_to(real)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(user))
    assert observe_codex(_stdout())["cli_version"] == "unknown"
    status, message = codex_observation_row()
    assert "OUTSIDE_SECRET" not in message and status != "PASS"
    monkeypatch.setenv("CODEX_HOME", str(user / ".codex"))
    assert "OUTSIDE_SECRET" not in codex_observation_row()[1]


@pytest.mark.parametrize("relative", ["/etc/hosts", "sessions/../../outside/x.jsonl", "other/x.jsonl"])
def test_read_rollout_refuses_a_path_outside_sessions_before_opening(tmp_path: Path, relative: str) -> None:
    from trw_mcp.dispatch._codex_observed import read_rollout

    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "x.jsonl").write_text(_lines("OUTSIDE_SECRET"), encoding="utf-8")
    seen = read_rollout(home, relative)
    assert seen["reason"] == "rollout_refused"
    assert seen["cli_version"] == "unknown"


def test_deeply_nested_json_is_unreadable_not_a_crash(tmp_path: Path) -> None:
    from trw_mcp.dispatch._codex_observed import observe_codex

    (tmp_path / DAY).mkdir(parents=True)
    (tmp_path / DAY / NAME).write_text("[" * 200_000 + "]" * 200_000 + "\n" + _lines(), encoding="utf-8")
    assert observe_codex(_stdout(), tmp_path)["model"] == "gpt-6.1-sol"
