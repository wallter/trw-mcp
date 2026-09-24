"""The trw_assess backend's one enablement answer: process env, then project scope, then user
scope, then off (first explicit wins).

The kill switch this protects was observed failing open under Codex (the client dropped
``TRW_JEV_ENABLED=false`` and the machine switch won); these pin every layer the server can see.
2026-09-23 operator decision relaxed the prior rule (a project could only ever disable): a
project's own ``.trw/config.yaml`` or ``.env`` may now enable the backend too, provided nothing
higher in the precedence chain said otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_KEY = "sk-or-enablement-test-000000000000000"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _layers(
    home: Path,
    project: Path,
    machine: object,
    project_yaml: object,
    dotenv: str | None,
) -> None:
    if machine is not None:
        (home / ".trw" / "config.yaml").write_text(f"assess_enabled: {str(machine).lower()}\n", encoding="utf-8")
    if project_yaml is not None:
        (project / ".trw").mkdir(parents=True, exist_ok=True)
        (project / ".trw" / "config.yaml").write_text(
            f"assess_enabled: {str(project_yaml).lower()}\n", encoding="utf-8"
        )
    if dotenv is not None:
        (project / ".env").write_text(f"TRW_JEV_ENABLED={dotenv}\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("env", "machine", "project_yaml", "dotenv", "expected"),
    [
        # -- Nothing anywhere: off. --------------------------------------------------------------
        (None, None, None, None, (False, "")),
        # -- One layer at a time. -----------------------------------------------------------------
        ("true", None, None, None, (True, "TRW_JEV_ENABLED")),
        (None, True, None, None, (True, "~/.trw/config.yaml")),
        (None, None, True, None, (True, "project .trw/config.yaml")),
        (None, None, None, "true", (True, "project .env")),
        # -- A value that is not truthy is false, not ignored. -----------------------------------
        ("maybe", True, None, None, (False, "TRW_JEV_ENABLED")),
        # -- Blank is unset: what a client forwarding ${env:X} passes when X is unset. -----------
        ("", True, None, None, (True, "~/.trw/config.yaml")),
        ("  ", True, None, None, (True, "~/.trw/config.yaml")),
        # -- Process env is top of precedence: it wins over EVERY other layer, on or off. --------
        ("false", True, None, None, (False, "TRW_JEV_ENABLED")),
        ("true", False, None, None, (True, "TRW_JEV_ENABLED")),
        ("true", None, False, None, (True, "TRW_JEV_ENABLED")),
        ("false", None, None, "true", (False, "TRW_JEV_ENABLED")),
        # -- Project scope beats user scope, on or off; config.yaml is read before .env. ---------
        (None, True, False, None, (False, "project .trw/config.yaml")),
        (None, False, True, None, (True, "project .trw/config.yaml")),
        (None, True, None, "false", (False, "project .env")),
        (None, False, None, "true", (True, "project .env")),
        (None, None, False, "true", (False, "project .trw/config.yaml")),
        # -- 2026-09-23: a project may now enable via its .env, where before it could only disable,
        # regardless of what the user's own machine switch says. ---------------------------------
        (None, True, None, "true", (True, "project .env")),
    ],
)
def test_precedence_first_explicit_layer_wins(
    home: Path,
    tmp_path: Path,
    env: str | None,
    machine: object,
    project_yaml: object,
    dotenv: str | None,
    expected: tuple[bool, str],
) -> None:
    from trw_mcp.tools._assess_enablement import backend_enablement

    project = tmp_path / "project"
    project.mkdir()
    _layers(home, project, machine, project_yaml, dotenv)

    assert backend_enablement(project, {} if env is None else {"TRW_JEV_ENABLED": env}) == expected


def test_malformed_project_yaml_fails_closed_against_a_permissive_user_switch(home: Path, tmp_path: Path) -> None:
    """2026-09-23 review fix: a project file that EXISTS but is broken must never be silently
    skipped in favor of a permissive user-scope machine switch.
    """
    from trw_mcp.tools._assess_enablement import backend_enablement

    (home / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("assess_enabled: [unterminated\n", encoding="utf-8")

    assert backend_enablement(project, {}) == (False, "project .trw/config.yaml")


def test_non_boolean_project_yaml_value_fails_closed_against_a_permissive_user_switch(
    home: Path, tmp_path: Path
) -> None:
    """``assess_enabled: 1`` is a YAML int, not a recognized bool/truthy-string."""
    from trw_mcp.tools._assess_enablement import backend_enablement

    (home / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("assess_enabled: 1\n", encoding="utf-8")

    assert backend_enablement(project, {}) == (False, "project .trw/config.yaml")


def test_blank_project_yaml_value_fails_closed_against_a_permissive_user_switch(home: Path, tmp_path: Path) -> None:
    """Round 2 review fix: ``assess_enabled: ""`` is present and deliberate, not "say nothing"."""
    from trw_mcp.tools._assess_enablement import backend_enablement

    (home / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text('assess_enabled: ""\n', encoding="utf-8")

    assert backend_enablement(project, {}) == (False, "project .trw/config.yaml")


def test_project_yaml_refused_by_the_hardened_reader_fails_closed_against_a_permissive_user_switch(
    home: Path, tmp_path: Path
) -> None:
    """Round 2 review fix: a project ``.trw/config.yaml`` that EXISTS but the hardened reader
    refuses (non-UTF-8 here) must fail closed, not fall through as if genuinely absent.
    """
    from trw_mcp.tools._assess_enablement import backend_enablement

    (home / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_bytes(b"assess_enabled: true\n\xff\xfe\x00binary\n")

    assert backend_enablement(project, {}) == (False, "project .trw/config.yaml")


def test_an_explicit_process_env_setting_reads_no_file(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-23 review fix: layers are evaluated lazily -- an explicit process env value must
    short-circuit before any project or user config file is even opened.
    """
    from trw_memory.decisions import _enablement

    from trw_mcp.tools._assess_enablement import backend_enablement

    def _boom_yaml(*_a: object, **_kw: object) -> bool | None:
        raise AssertionError("a decided process-env layer must not read any YAML file")

    def _boom_dotenv(*_a: object, **_kw: object) -> bool | None:
        raise AssertionError("a decided process-env layer must not read any dotenv file")

    monkeypatch.setattr(_enablement, "_read_yaml_bool", _boom_yaml)
    monkeypatch.setattr(_enablement, "_read_dotenv_bool", _boom_dotenv)
    project = tmp_path / "project"
    project.mkdir()

    assert backend_enablement(project, {"TRW_JEV_ENABLED": "false"}) == (False, "TRW_JEV_ENABLED")
    assert backend_enablement(project, {"TRW_JEV_ENABLED": "true"}) == (True, "TRW_JEV_ENABLED")


def test_the_tool_builds_no_egress_judge_when_the_process_env_switches_it_off(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Process env still outranks a project that enables itself (env on/off beats project)."""
    import os

    from trw_memory.decisions import NullJudge, judge_from_env
    from trw_memory.decisions._jev_http import JevHttpJudge

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("TRW_JEV_ENABLED", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)
    assert isinstance(judge_from_env(dict(os.environ), project_root=project), JevHttpJudge)

    monkeypatch.setenv("TRW_JEV_ENABLED", "false")
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")

    assert isinstance(judge_from_env(dict(os.environ), project_root=project), NullJudge)


def test_the_tool_builds_a_live_judge_when_only_the_project_enables_it(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-09-23: with nothing set at the process-env or user-scope layer, a project's own
    ``.trw/config.yaml`` is enough to enable the backend (the key still comes from the project
    ``.env`` or the process env).
    """
    import os

    from trw_memory.decisions._jev_http import JevHttpJudge

    from trw_mcp.tools.assess import toolkit_from_env

    project = tmp_path / "project"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", _KEY)

    kit = toolkit_from_env(dict(os.environ), redactor=lambda s: s, project_root=project, dotenv_path=project / ".env")

    assert isinstance(kit.judge, JevHttpJudge)
