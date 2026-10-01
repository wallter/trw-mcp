"""Warnings an upgrade prints that named the wrong file or gave nothing to act on (FB-INSTALL-12/13/18, 15).

Each test builds the situation the reporting agent was in and asserts on the sentence the user reads.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# ── FB-INSTALL-12: the retired-key warning names the file the key is in ───


@pytest.fixture
def _fresh_warned_keys() -> object:
    from trw_mcp.models.config import _retired_keys

    _retired_keys._reset_warned_keys()
    yield
    _retired_keys._reset_warned_keys()


@pytest.mark.usefixtures("_fresh_warned_keys")
class TestUnrecognisedKeyNamesItsFile:
    def _build(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, user: str, project: str) -> tuple[Path, Path]:
        home = tmp_path / "home"
        (home / ".trw").mkdir(parents=True)
        (home / ".trw" / "config.yaml").write_text(user, encoding="utf-8")
        project_dir = tmp_path / "proj"
        (project_dir / ".trw").mkdir(parents=True)
        (project_dir / ".trw" / "config.yaml").write_text(project, encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
        return home / ".trw" / "config.yaml", project_dir / ".trw" / "config.yaml"

    def test_a_key_in_the_user_level_file_names_that_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trw_mcp.models.config._loader import _build_config_unguarded

        user_file, project_file = self._build(
            tmp_path, monkeypatch, user="user_only_gone_key: true\n", project="installation_id: p\n"
        )

        _build_config_unguarded(project_file)

        err = capsys.readouterr().err
        assert f"{user_file} sets 'user_only_gone_key'" in err
        assert "TRW: WARNING — .trw/config.yaml sets 'user_only_gone_key'" not in err

    def test_a_key_in_the_project_file_still_names_the_project_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trw_mcp.models.config._loader import _build_config_unguarded

        _user_file, project_file = self._build(
            tmp_path, monkeypatch, user="installation_id: u\n", project="project_only_gone_key: 1\n"
        )

        _build_config_unguarded(project_file)

        assert "TRW: WARNING — .trw/config.yaml sets 'project_only_gone_key'" in capsys.readouterr().err

    def test_a_key_in_both_files_names_the_project_file_that_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trw_mcp.models.config._loader import _build_config_unguarded

        user_file, project_file = self._build(
            tmp_path, monkeypatch, user="both_files_gone_key: 1\n", project="both_files_gone_key: 2\n"
        )

        _build_config_unguarded(project_file)

        err = capsys.readouterr().err
        assert "TRW: WARNING — .trw/config.yaml sets 'both_files_gone_key'" in err
        assert str(user_file) not in err

    def test_the_value_is_still_never_printed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trw_mcp.models.config._loader import _build_config_unguarded

        _user_file, project_file = self._build(
            tmp_path, monkeypatch, user="user_secret_gone_key: s3cr3t-do-not-print\n", project="installation_id: p\n"
        )

        _build_config_unguarded(project_file)

        assert "s3cr3t-do-not-print" not in capsys.readouterr().err

    def test_a_caller_with_no_source_map_keeps_the_project_file_wording(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trw_mcp.models.config._retired_keys import warn_unrecognised_config_keys

        warn_unrecognised_config_keys({"no_source_gone_key": 1}, set())

        assert "TRW: WARNING — .trw/config.yaml sets 'no_source_gone_key'" in capsys.readouterr().err


# ── FB-INSTALL-13: "kept (disabled skill)" says what is disabled ─────────


def test_a_kept_disabled_skill_names_the_skill_the_flag_and_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills
    from trw_mcp.models.config import TRWConfig

    def _set_flag(enabled: bool) -> None:
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: TRWConfig(assess_enabled=enabled))
        for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED"):
            monkeypatch.delenv(var, raising=False)
        (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".trw" / "config.yaml").write_text(f"assess_enabled: {str(enabled).lower()}\n", encoding="utf-8")

    _set_flag(True)
    generate_cursor_ide_skills(tmp_path)
    edited = tmp_path / ".cursor" / "skills" / "trw-assess" / "SKILL.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nlocal note\n", encoding="utf-8")

    _set_flag(False)
    result: Any = generate_cursor_ide_skills(tmp_path)

    (warning,) = [w for w in result["warnings"] if "trw-assess" in w]
    assert ".cursor/skills/trw-assess/SKILL.md" in warning, "the file is named"
    assert "kept (disabled skill)" not in warning, "the old, unexplained wording is gone"
    assert "assess_enabled" in warning, "the flag that is off is named"
    assert "trw_assess" in warning, "the tool the kept skill still advertises is named"
    assert "delete" in warning.lower(), "the way out is named"
    assert edited.is_file(), "an edited copy is still never deleted"


def test_a_kept_extra_file_is_not_described_as_keeping_the_skill_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only SKILL.md keeps a skill live; a user's own note beside a retired skill must not be called the skill."""
    from trw_mcp.bootstrap._cursor_ide import generate_cursor_ide_skills
    from trw_mcp.models.config import TRWConfig

    def _set_flag(enabled: bool) -> None:
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: TRWConfig(assess_enabled=enabled))
        for var in ("TRW_ASSESS_ENABLED", "TRW_JEV_ENABLED"):
            monkeypatch.delenv(var, raising=False)
        (tmp_path / ".trw").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".trw" / "config.yaml").write_text(f"assess_enabled: {str(enabled).lower()}\n", encoding="utf-8")

    _set_flag(True)
    generate_cursor_ide_skills(tmp_path)
    note = tmp_path / ".cursor" / "skills" / "trw-assess" / "my-notes.md"
    note.write_text("mine\n", encoding="utf-8")

    _set_flag(False)
    result: Any = generate_cursor_ide_skills(tmp_path)

    (warning,) = [w for w in result["warnings"] if "my-notes.md" in w]
    assert "still active" not in warning and "calls trw_assess" not in warning
    assert "does not keep the skill active" in warning
    assert note.is_file()
    assert not (note.parent / "SKILL.md").exists(), "the unchanged SKILL.md was retired"


# ── FB-INSTALL-18: the mcp_security WARN points at the anomaly ───────────


def _write_anomalies(project: Path, anomalies: list[dict[str, str]], *, age_minutes: list[int]) -> None:
    events_dir = project / ".trw" / "context"
    events_dir.mkdir(parents=True)
    now = datetime.now(tz=timezone.utc)
    lines = []
    for index, (anomaly, age) in enumerate(zip(anomalies, age_minutes, strict=True)):
        lines.append(
            json.dumps(
                {
                    "event_id": f"evt-{index}",
                    "ts": (now - timedelta(minutes=age)).isoformat(),
                    "event_type": "mcp_security",
                    "payload": {"decision": "shadow_anomaly", **anomaly},
                }
            )
        )
    (events_dir / f"events-{now.strftime('%Y-%m-%d')}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestMcpSecurityWarnPointsAtTheAnomaly:
    def _row(self, project: Path) -> tuple[str, str]:
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.server._subcommands_doctor import _check_mcp_security

        result = _check_mcp_security(project, TRWConfig())
        return result.status, result.message

    def test_the_newest_anomaly_is_named_with_the_command_for_more(self, tmp_path: Path) -> None:
        _write_anomalies(
            tmp_path,
            [
                {"server": "old-server", "tool": "old_tool", "anomaly_type": "old_kind"},
                {"server": "fs-server", "tool": "write_file", "anomaly_type": "unlisted_tool"},
            ],
            age_minutes=[300, 5],
        )

        status, message = self._row(tmp_path)

        assert status == "WARN"
        assert message.startswith("2 recent anomalies")
        assert "unlisted_tool" in message and "fs-server" in message and "write_file" in message
        assert "old_kind" not in message, "only the NEWEST anomaly is named"
        assert "trw-mcp telemetry security" in message

    def test_newest_means_latest_instant_not_latest_string(self, tmp_path: Path) -> None:
        """Offsets differ across rows; comparing the text would pick the earlier instant here."""
        events_dir = tmp_path / ".trw" / "context"
        events_dir.mkdir(parents=True)
        now = datetime.now(tz=timezone.utc)
        far_east = timezone(timedelta(hours=14))
        rows = [
            # 5 h ago, written at +14:00: its text sorts AFTER the newer row's text below.
            ((now - timedelta(hours=5)).astimezone(far_east).isoformat(), "older_kind"),
            ((now - timedelta(minutes=10)).isoformat(), "newer_kind"),
        ]
        lines = [
            json.dumps(
                {
                    "event_id": f"e{i}",
                    "ts": ts,
                    "event_type": "mcp_security",
                    "payload": {"decision": "shadow_anomaly", "server": "s", "tool": "t", "anomaly_type": kind},
                }
            )
            for i, (ts, kind) in enumerate(rows)
        ]
        (events_dir / f"events-{now.strftime('%Y-%m-%d')}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

        _status, message = self._row(tmp_path)

        assert "newer_kind" in message and "older_kind" not in message

    def test_the_details_command_keeps_the_doctors_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``telemetry security`` reads the project it runs in, so a doctor pointed elsewhere must say where to cd."""
        project = tmp_path / "some project"
        _write_anomalies(project, [{"server": "s", "tool": "t", "anomaly_type": "k"}], age_minutes=[1])
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        _status, message = self._row(project)

        assert f"cd '{project}' && trw-mcp telemetry security" in message

    def test_the_details_command_is_bare_when_the_target_is_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_anomalies(tmp_path, [{"server": "s", "tool": "t", "anomaly_type": "k"}], age_minutes=[1])
        monkeypatch.chdir(tmp_path)

        _status, message = self._row(tmp_path)

        assert message.endswith("details: trw-mcp telemetry security")

    def test_a_single_anomaly_reads_as_singular_and_survives_missing_fields(self, tmp_path: Path) -> None:
        _write_anomalies(tmp_path, [{}], age_minutes=[1])

        status, message = self._row(tmp_path)

        assert status == "WARN"
        assert message.startswith("1 recent anomaly")
        assert "anomalies" not in message
        assert "trw-mcp telemetry security" in message

    def test_event_text_is_printable_in_the_row(self, tmp_path: Path) -> None:
        """Server and tool names come from event rows; control characters never reach the terminal."""
        _write_anomalies(
            tmp_path,
            [{"server": "bad\x1b[31mserver", "tool": "t", "anomaly_type": "k"}],
            age_minutes=[1],
        )

        _status, message = self._row(tmp_path)

        assert "\x1b" not in message

    def test_a_clean_project_still_passes(self, tmp_path: Path) -> None:
        status, message = self._row(tmp_path)

        assert status == "PASS"
        assert "telemetry security" not in message


# ── FB-INSTALL-15: the doctor distill row cites a place the user can reach ─


def test_the_distill_skip_row_does_not_cite_a_repo_only_doc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _doctor_distill

    monkeypatch.setattr(_doctor_distill.importlib.util, "find_spec", lambda _name: None)

    status, message = _doctor_distill.distill_row(tmp_path, TRWConfig())

    assert status == "SKIP"
    assert "docs/deployment" not in message
    assert "https://trwframework.com/waitlist" in message
