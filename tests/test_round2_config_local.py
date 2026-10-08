from __future__ import annotations

import argparse
from pathlib import Path

import pytest


def test_line_status_uses_nearest_trw_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server import _subcommands_misc as misc

    project = tmp_path / "project"
    nested = project / "one" / "two"
    (project / ".trw").mkdir(parents=True)
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(misc, "_local_status_machine", lambda _args, _fmt: None)
    seen: list[Path | None] = []
    original = misc._nearest_trw_ancestor

    def record(cwd: Path) -> Path | None:
        result = original(cwd)
        seen.append(result)
        return result

    monkeypatch.setattr(misc, "_nearest_trw_ancestor", record)
    misc._run_local(argparse.Namespace(local_command="status", status_format="line", json=False))
    assert seen == [project]


def test_deleted_cwd_binding_reports_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.server import _subcommands_misc as misc

    monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    monkeypatch.setattr(Path, "cwd", classmethod(lambda _cls: (_ for _ in ()).throw(FileNotFoundError("gone"))))
    with pytest.raises(SystemExit) as exc:
        with misc.enclosing_project_bound():
            pytest.fail("the verb must not run with an invalid cwd")
    assert exc.value.code == 1
    assert capsys.readouterr().out == "Error: cannot determine current directory (gone)\n"


def test_set_comment_only_bom_config(tmp_path: Path) -> None:
    from trw_mcp.tools._config_writer import set_config_value

    (tmp_path / ".trw").mkdir()
    path = tmp_path / ".trw" / "config.yaml"
    path.write_text("\ufeff# existing note\n", encoding="utf-8")
    set_config_value("installation_id", "abc", target_dir=tmp_path, scope="project")
    rendered = path.read_text(encoding="utf-8")
    assert rendered.count("\ufeff") == 1
    assert "installation_id: abc" in rendered


def test_project_retired_key_hint_quotes_target_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.models.config._retired_keys import warn_unrecognised_config_keys

    project = tmp_path / "folder with spaces"
    config = project / ".trw" / "config.yaml"
    warn_unrecognised_config_keys(
        {"user_tier_enabled": True}, set(), key_sources=lambda: {"user_tier_enabled": str(config)}
    )
    assert f"--target-dir '{project}'" in capsys.readouterr().err


def test_local_deliver_state_error_is_clean_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.server import _subcommands_misc as misc

    monkeypatch.setattr(
        "trw_mcp.services.orchestration_service.mark_local_delivered",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(StateError("bad run yaml")),
    )
    args = argparse.Namespace(local_command="deliver", run_path="/tmp/run", json=True, message="done")
    with pytest.raises(SystemExit) as exc:
        misc._run_local_verb(args)
    assert exc.value.code == 1
    assert capsys.readouterr().out == '{"error": "bad run yaml"}\n'


def test_local_status_state_error_is_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.server import _subcommands_misc as misc

    monkeypatch.setattr(
        "trw_mcp.services.orchestration_service.read_local_status",
        lambda **_kwargs: (_ for _ in ()).throw(StateError("bad run yaml")),
    )
    args = argparse.Namespace(local_command="status", run_path="/tmp/run", status_format="text", json=False)
    with pytest.raises(SystemExit) as exc:
        misc._run_local_verb(args)
    assert exc.value.code == 1
    assert capsys.readouterr().out == "Error: bad run yaml\n"
