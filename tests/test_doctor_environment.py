"""PRD-INFRA-189 FR02/FR05: environment-parity doctor rows."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.server._doctor_environment import (
    claude_code_version_row,
    foreign_client_paths_row,
    gnu_timeout_row,
    stray_servers_row,
)


class TestGnuTimeout:
    def test_absent_is_informational_and_names_the_remedy(self) -> None:
        status, message = gnu_timeout_row(which=lambda _name: None)
        assert status == "PASS"
        assert message.startswith("INFO:")
        assert "_trw_bounded_python" in message
        assert "coreutils" in message

    def test_gtimeout_alone_is_enough(self) -> None:
        status, message = gnu_timeout_row(which=lambda name: "/opt/bin/gtimeout" if name == "gtimeout" else None)
        assert status == "PASS"
        assert "gtimeout on PATH" in message
        assert not message.startswith("INFO:")


class TestForeignClientPaths:
    def test_another_machines_home_warns_with_file_and_remedy(self, tmp_path: Path) -> None:
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "hooks.json").write_text(
            '{"command": "/home/bob/src/repo/.codex/hooks/trw-stop.sh"}', encoding="utf-8"
        )
        status, message = foreign_client_paths_row(tmp_path, home=tmp_path / "me")
        assert status == "WARN"
        assert ".codex/hooks.json" in message
        assert "/home/bob/src/repo/.codex/hooks/trw-stop.sh" in message
        assert "trw-mcp update-project" in message

    def test_checkout_home_and_system_paths_pass(self, tmp_path: Path) -> None:
        home = Path("/Users/me")
        (tmp_path / ".cursor").mkdir()
        (tmp_path / ".cursor" / "hooks.json").write_text(
            f'{{"a": "{tmp_path.resolve()}/.cursor/hooks/x.sh", "b": "/Users/me/.local/bin/trw-mcp", '
            '"c": "/usr/bin/env bash"}',
            encoding="utf-8",
        )
        status, _message = foreign_client_paths_row(tmp_path, home=home)
        assert status == "PASS"

    def test_no_client_directories_is_not_applicable(self, tmp_path: Path) -> None:
        status, _message = foreign_client_paths_row(tmp_path, home=tmp_path)
        assert status == "SKIP"


def test_rows_are_registered_in_the_doctor_catalogue() -> None:
    from trw_mcp.server._subcommands_doctor import _CHECKS

    names = [name for name, _fn in _CHECKS]
    # Order among these rows, not the catalogue's tail: later slices append rows after them.
    ordered = [
        "gnu_timeout",
        "foreign_client_paths",
        "version_status",
        "jev",
        "retrieval",
        "stray_servers",
        "claude_code_version",
        "hook_python",
    ]
    assert [name for name in names if name in ordered] == ordered


#: PRD-FIX-153 FR05: the row only checks servers recorded in pins.json.
_SCOPE_SENTENCE = "Only servers recorded in pins.json are checked."


class TestStrayServersRow:
    def test_stray_servers_warn_with_each_line(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        lines = ["trw-mcp pid 7 is orphaned (its client exited): stop it with `kill 7`"]
        monkeypatch.setattr("trw_mcp.server._doctor_environment.stray_servers", lambda trw_dir: lines)

        status, message = stray_servers_row(tmp_path)

        assert status == "WARN"
        assert "1 stray" in message and "kill 7" in message
        assert _SCOPE_SENTENCE in message

    def test_no_stray_server_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[Path] = []
        monkeypatch.setattr(
            "trw_mcp.server._doctor_environment.stray_servers", lambda trw_dir: seen.append(trw_dir) or []
        )

        status, message = stray_servers_row(tmp_path)
        assert status == "PASS"
        assert _SCOPE_SENTENCE in message
        assert seen == [tmp_path / ".trw"]


# PRD-CORE-289 FR07: Claude Code 2.1.280 is the first release that runs Opus 5.5. 2.1.99 and
# 10.0.0 separate numeric from string comparison. Anything TRW cannot read is WARN, never PASS.
@pytest.mark.parametrize(
    ("script", "status", "needles"),
    [
        ("echo '2.1.279 (Claude Code)'", "WARN", ("2.1.279", "2.1.280", "Opus 5.5")),
        ("echo '2.1.99 (Claude Code)'", "WARN", ("2.1.99", "2.1.280")),
        ("echo '2.1.280 (Claude Code)'", "PASS", ("2.1.280",)),
        ("echo '10.0.0 (Claude Code)'", "PASS", ("10.0.0",)),
        ("echo 'Claude Code (nightly)'", "WARN", ("could not read",)),
        ("echo 'boom' >&2; exit 3", "WARN", ("exited 3",)),
        ("exec /bin/sleep 5", "WARN", ("timeout",)),
    ],
    ids=["below", "below-numeric", "floor", "above-numeric", "malformed", "nonzero-exit", "timeout"],
)
def test_claude_code_version_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str, status: str, needles: tuple[str, ...]
) -> None:
    claude = tmp_path / "claude"
    claude.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))

    # A loaded host takes over a second just to start `sh` (2 of 3 failing xdist runs at load ~15 hit the
    # probe timeout), so only the case that asserts the timeout keeps the short budget.
    got, message = claude_code_version_row(timeout_s=1 if "sleep" in script else 30)

    assert got == status, message
    assert all(needle in message for needle in needles), message


def test_claude_code_version_row_skips_without_a_claude_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert claude_code_version_row(timeout_s=1)[0] == "SKIP"
