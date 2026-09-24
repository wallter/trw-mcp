"""PRD-INFRA-189 FR02/FR05: environment-parity doctor rows."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.server._doctor_environment import foreign_client_paths_row, gnu_timeout_row


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
    assert names[-5:] == ["gnu_timeout", "foreign_client_paths", "version_status", "jev", "retrieval"]
