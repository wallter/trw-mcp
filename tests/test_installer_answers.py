"""The installer asks each question once and remembers the answer (operator feedback: "that's just a bad UX").

A first interactive install answers a mix of yes and no; a second shows ZERO prompts and applies the same
choices. An explicit flag wins over a record, ``--reconfigure`` asks everything once more, and a run with no
terminal asks and records nothing. The record is the ``installer_answers`` map of the project's (or the machine's)
``config.yaml``, written through the real ``trw-mcp config set`` and read back by the installer.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_memory_daemon")]

_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def _installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_answers", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Tty(io.StringIO):
    def close(self) -> None:  # the prompt closes its tty; keep the buffer readable
        pass


class Session:
    """One scripted interactive install: the terminal answers from *answers* and every question is counted."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        home: Path,
        project: Path,
        answers: list[str],
        *,
        reconfigure: bool = False,
    ) -> None:
        self.installer = _installer()
        self.capsys = capsys
        self.project = project
        self.answers = list(answers)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("TRW_HEADLESS", raising=False)
        monkeypatch.setattr(self.installer.Path, "home", classmethod(lambda _cls: home))
        monkeypatch.setattr(self.installer, "_open_tty", self._tty)
        monkeypatch.setattr(
            self.installer, "find_trw_cmd", lambda *_a, **_k: [sys.executable, "-B", "-m", "trw_mcp.server"]
        )
        self.installer.set_answer_store(
            self.installer.AnswerStore(
                project / ".trw" / "config.yaml", home / ".trw" / "config.yaml", reconfigure=reconfigure
            )
        )
        self.ui = self.installer.UI(interactive=True)
        self.installer._ALLOW_SYSTEM_PYTHON = None

    def _tty(self) -> Any:
        return _Tty((self.answers.pop(0) if self.answers else "") + "\n")

    def prompts(self) -> int:
        out = self.capsys.readouterr().out
        self.out = out
        return out.count("[Y/n]") + out.count("[y/N]")

    def finish(self) -> None:
        self.installer.flush_answers(self.ui, sys.executable, self.project)


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    (home / ".trw").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".trw").mkdir(parents=True)
    (project / ".trw" / "config.yaml").write_text("installation_id: proj\n", encoding="utf-8")
    return home, project


def _the_questions(session: Session, project: Path) -> dict[str, bool]:
    """Everything an interactive install asks that is not a flag-resolved choice, driven through the real phases."""
    inst = session.installer
    marker = project / inst.PROPRIETARY_MARKER_RELPATH
    marker.write_text('{"trw-distill": "1.0.0"}', encoding="utf-8")
    db = project / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"x")
    inst._run_python_output = lambda *_a, **_k: (0, "")  # the store holds learnings
    ran: list[bool] = []
    inst._run_migrate_command = lambda *_a, **_k: ran.append(True) or (0, "", "")
    session.migrations = ran
    return {
        "ai": inst.phase_prompt_features(session.ui, None, {}),
        "system_python": inst._allow_system_python(session.ui, "/usr/bin/python3"),
        "proprietary_upgrade": inst._resolve_proprietary_from_marker(project, False, True, session.ui, offline=False),
        # whether the migration COMMAND ran: the phase returns True both when it declines and when it migrates
        "migrated": inst.phase_migrate_store(session.ui, sys.executable, project, migrate=True, interactive=True)
        and bool(ran),
    }


def test_a_second_interactive_install_asks_nothing_and_applies_the_same_choices(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(
        monkeypatch, capsys, home, project, ["n", "y", "n", "n"]
    )  # AI no, system python yes, upgrade no, migrate no
    choices = _the_questions(first, project)
    asked = first.prompts()
    first.finish()

    assert asked == 4
    assert choices["ai"] is False and choices["system_python"] is True and choices["proprietary_upgrade"] is False

    second = Session(monkeypatch, capsys, home, project, [])
    again = _the_questions(second, project)

    assert second.prompts() == 0, second.out
    assert again == choices
    assert "your earlier answer" in second.out


def test_the_answers_are_recorded_where_they_belong(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    session = Session(monkeypatch, capsys, home, project, ["n", "y", "n", "n"])
    _the_questions(session, project)
    session.finish()

    inst = session.installer
    on_project = inst._read_installer_answers(project / ".trw" / "config.yaml")
    on_machine = inst._read_installer_answers(home / ".trw" / "config.yaml")
    assert on_project["ai_extras"] is False and on_project["proprietary_upgrade"] is False
    (declined,) = [v for k, v in on_project.items() if k.startswith("migrate_learnings_")]
    assert declined is False  # bound to this source and destination
    assert list(on_machine.values()) == [True] and next(iter(on_machine)).startswith("system_python_")
    assert "system_python" not in "".join(on_project)  # a machine choice stays out of the project config
    # the file is still a config the production loader accepts, with no unknown-key warning
    from trw_mcp.models.config._loader import config_for_trw_dir

    assert config_for_trw_dir(project / ".trw").installer_answers["ai_extras"] is False


def test_a_no_for_ai_extras_is_never_asked_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["n"])
    assert first.installer.phase_prompt_features(first.ui, None, {}) is False
    first.finish()
    first.prompts()

    for _ in range(3):
        again = Session(monkeypatch, capsys, home, project, [])
        assert again.installer.phase_prompt_features(again.ui, None, {}) is False
        assert again.prompts() == 0
        assert "AI extras: off (your earlier answer; change with --ai / --no-ai or --reconfigure)" in again.out


def test_a_flag_wins_over_a_recorded_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["n"])
    first.installer.phase_prompt_features(first.ui, None, {})
    first.finish()
    first.prompts()  # drain the first run's output

    second = Session(monkeypatch, capsys, home, project, [])
    assert second.installer.phase_prompt_features(second.ui, True, {}) is True  # --ai
    assert second.prompts() == 0


def test_reconfigure_asks_everything_once_more_and_records_the_new_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["n", "y", "n", "n"])
    _the_questions(first, project)
    first.finish()
    first.prompts()

    redo = Session(monkeypatch, capsys, home, project, ["y", "n", "y", "n"], reconfigure=True)
    changed = _the_questions(redo, project)
    asked = redo.prompts()
    redo.finish()

    assert asked == 4
    assert changed["ai"] is True and changed["system_python"] is False and changed["proprietary_upgrade"] is True
    final = redo.installer._read_installer_answers(project / ".trw" / "config.yaml")
    assert final["ai_extras"] is True and final["proprietary_upgrade"] is True

    after = Session(monkeypatch, capsys, home, project, [])
    assert _the_questions(after, project) == changed
    assert after.prompts() == 0


def test_a_run_without_a_terminal_asks_nothing_and_records_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    session = Session(monkeypatch, capsys, home, project, [])
    monkeypatch.setattr(session.installer, "_open_tty", lambda: None)

    assert session.installer.phase_prompt_features(session.ui, None, {}) is False  # the default, as before
    session.finish()

    assert session.prompts() == 0
    assert session.installer._read_installer_answers(project / ".trw" / "config.yaml") == {}


def test_declining_the_platform_is_remembered_and_a_key_ends_the_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    prompts_for_key: list[int] = []
    first = Session(monkeypatch, capsys, home, project, ["", "n"])  # project name Enter; telemetry no
    first.installer._prompt_api_key = lambda _ui: prompts_for_key.append(1) or ""
    first.installer.phase_configure(
        first.ui, 1, 1, project, True, "", "", None, {}, skip_auth=False, target_platforms=None
    )
    first.finish()
    first.prompts()

    second = Session(monkeypatch, capsys, home, project, [""])
    second.installer._prompt_api_key = lambda _ui: prompts_for_key.append(2) or ""
    prior = second.installer._load_prior_config(project)
    second.installer.phase_configure(
        second.ui, 1, 1, project, True, "", "", None, prior, skip_auth=False, target_platforms=None
    )
    second.prompts()

    assert prompts_for_key == [1]  # asked once; the second run reused "offline"
    assert "Platform: offline (your earlier answer" in second.out


def test_reconfigure_asks_the_platform_question_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    calls: list[int] = []
    first = Session(monkeypatch, capsys, home, project, ["", "n"])
    first.installer._prompt_api_key = lambda _ui: ""
    first.installer.phase_configure(
        first.ui, 1, 1, project, True, "", "", None, {}, skip_auth=False, target_platforms=None
    )
    first.finish()

    redo = Session(monkeypatch, capsys, home, project, ["", "n"], reconfigure=True)
    redo.installer._prompt_api_key = lambda _ui: calls.append(1) or ""
    prior = redo.installer._load_prior_config(project)
    redo.installer.phase_configure(
        redo.ui, 1, 1, project, True, "", "", None, prior, skip_auth=False, target_platforms=None
    )

    assert calls == [1]


def test_the_reader_ignores_other_blocks_and_comments(tmp_path: Path) -> None:
    inst = _installer()
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "dispatch_default_models:\n  codex: true\n# note\ninstaller_answers:\n  ai_extras: false  # no thanks\n"
        "  migrate_learnings: TRUE\nother:\n  ai_extras: true\n",
        encoding="utf-8",
    )

    assert inst._read_installer_answers(cfg) == {"ai_extras": False, "migrate_learnings": True}
    assert inst._read_installer_answers(tmp_path / "missing.yaml") == {}


@pytest.mark.parametrize(
    "text",
    [
        "installation_id: proj\ninstaller_answers:\n  ai_extras: false\n",
        'installation_id: proj\n"installer_answers":\n  ai_extras: false\n',
        "{installation_id: proj, installer_answers: {ai_extras: false}}\n",
    ],
    ids=["block-root", "quoted-root", "whole-document-flow"],
)
def test_real_config_writer_round_trips_all_root_answer_layouts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    world: tuple[Path, Path],
    text: str,
) -> None:
    home, project = world
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(text, encoding="utf-8")
    session = Session(monkeypatch, capsys, home, project, [])

    assert session.installer._read_installer_answers(cfg) == {"ai_extras": False}
    session.installer._ANSWERS.remember("proprietary_upgrade", True)
    session.finish()

    assert session.installer._read_installer_answers(cfg) == {"ai_extras": False, "proprietary_upgrade": True}


@pytest.mark.parametrize(
    "text",
    [
        "installer_answers:\n  __CONSENT__: false\n",
        '"installer_answers":\n  __CONSENT__: false\n',
        "{installer_answers: {__CONSENT__: false}}\n",
    ],
    ids=["block-root", "quoted-root", "whole-document-flow"],
)
def test_unattended_migration_honours_refusal_under_each_root_layout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    world: tuple[Path, Path],
    text: str,
) -> None:
    home, project = world
    monkeypatch.delenv("TRW_USER_DIR", raising=False)
    session = Session(monkeypatch, capsys, home, project, [])
    db = project / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"x")
    consent = session.installer.bound_key("migrate_learnings", str(db.resolve()), str((home / ".trw").resolve()))
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(text.replace("__CONSENT__", consent), encoding="utf-8")

    assert _migrate_in(session, project, interactive=False) is False
    assert session.prompts() == 0


def test_unreadable_answer_layout_warns_once_and_returns_unknown_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    import builtins

    inst = _installer()
    cfg = tmp_path / "config.yaml"
    cfg.write_text("{installer_answers: {ai_extras: false}}\n", encoding="utf-8")
    real_import = builtins.__import__

    def without_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml" or name.startswith("ruamel"):
            raise ImportError("optional YAML parser unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_yaml)
    monkeypatch.setattr(inst, "_WARNED_UNREADABLE_ANSWERS", False)

    assert inst._read_installer_answers(cfg) == {}
    assert inst._read_installer_answers(cfg) == {}
    captured = capsys.readouterr()
    assert captured.err.count("Could not read installer_answers") == 1

    cfg.write_text('"installer_answers":\n  ai_extras: false\n', encoding="utf-8")
    assert inst._read_installer_answers(cfg) == {"ai_extras": False}


def test_main_exposes_reconfigure_and_builds_the_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tests._install_trw_main_support import drive_main, make_project

    installer = _installer()
    run = drive_main(installer, monkeypatch, make_project(tmp_path), extra_argv=("--reconfigure",))

    assert run.calls["install"] is not None
    assert installer._ANSWERS is not None and installer._ANSWERS.reconfigure is True
    assert "--reconfigure" in _TEMPLATE.read_text(encoding="utf-8")


def test_a_declined_proprietary_install_is_not_asked_again_and_reconfigure_asks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world

    def run(session: Session) -> list[str]:
        return session.installer.phase_install_proprietary(  # type: ignore[no-any-return]
            session.ui, 1, 1, sys.executable, "key", {}, "https://example.invalid", project_dir=project
        )

    first = Session(monkeypatch, capsys, home, project, ["n"])
    assert run(first) == []
    assert first.prompts() == 1
    first.finish()

    again = Session(monkeypatch, capsys, home, project, [])
    assert run(again) == []
    assert again.prompts() == 0
    assert (
        "Proprietary install: skip (your earlier answer; change with --with-proprietary or --reconfigure)" in again.out
    )
    assert "will fetch from" not in again.out  # the package list is for the question, not for the reused answer

    redo = Session(monkeypatch, capsys, home, project, ["n"], reconfigure=True)
    assert run(redo) == []
    assert redo.prompts() == 1


def test_an_embeddings_opt_out_is_reused_and_reconfigure_does_not_reuse_it() -> None:
    inst = _installer()
    inst.set_answer_store(None)
    assert inst.resolve_embeddings_choice(None, {"embeddings": False}) is False  # the earlier NO, reused
    assert inst.resolve_embeddings_choice(True, {"embeddings": False}) is True  # --embeddings wins
    inst.set_answer_store(inst.AnswerStore(Path("/nonexistent/p"), Path("/nonexistent/m"), reconfigure=True))
    assert inst.resolve_embeddings_choice(None, {"embeddings": False}) is False  # no terminal: nothing is re-asked


def test_reconfigure_asks_the_telemetry_question_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    reuse = Session(monkeypatch, capsys, home, project, [])
    assert (
        reuse.installer._resolve_interactive_telemetry(reuse.ui, opt_telemetry=None, prior_config={"telemetry": False})
        is False
    )
    assert reuse.prompts() == 0

    redo = Session(monkeypatch, capsys, home, project, ["y"], reconfigure=True)
    assert (
        redo.installer._resolve_interactive_telemetry(redo.ui, opt_telemetry=None, prior_config={"telemetry": False})
        is True
    )
    assert redo.prompts() == 1


def test_the_system_python_decision_is_per_interpreter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["y"])
    assert first.installer._allow_system_python(first.ui, "/usr/bin/python3") is True
    first.finish()
    first.prompts()

    other = Session(monkeypatch, capsys, home, project, ["n"])
    assert other.installer._allow_system_python(other.ui, "/opt/other/bin/python3") is False  # a different Python asks
    assert other.prompts() == 1


def test_headless_run_honors_recorded_system_python_consent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["y"])
    assert first.installer._allow_system_python(first.ui, "/usr/bin/python3") is True
    first.finish()

    second = Session(monkeypatch, capsys, home, project, [])
    second.ui.interactive = False
    assert second.installer._allow_system_python(second.ui, "/usr/bin/python3") is True


# ── Review: a remembered consent is bound to the context it was given in ────────────────────────────────────────


def _migrate(session: Session, project: Path) -> bool:
    """Run the migrate phase against a store that holds learnings; True when the migration COMMAND ran."""
    inst = session.installer
    db = project / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"x")
    ran: list[bool] = []
    inst._run_python_output = lambda *_a, **_k: (0, "")
    inst._run_migrate_command = lambda *_a, **_k: ran.append(True) or (0, "", "")
    inst.phase_migrate_store(session.ui, sys.executable, project, migrate=True, interactive=True)
    return bool(ran)


def test_an_approved_migration_is_never_silently_repeated_after_a_rollback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["y"])
    assert _migrate(first, project) is True
    first.prompts()
    first.finish()

    # a rollback put the learnings back: the same store holds rows again, and the earlier YES must not run it
    again = Session(monkeypatch, capsys, home, project, ["n"])
    assert _migrate(again, project) is False  # asked, declined: the command did not run
    assert again.prompts() == 1


def test_a_declined_migration_is_bound_to_its_source_and_destination(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path], tmp_path: Path
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["n"])
    assert _migrate(first, project) is False
    first.prompts()
    first.finish()

    same = Session(monkeypatch, capsys, home, project, [])
    assert _migrate(same, project) is False and same.prompts() == 0

    monkeypatch.setenv("TRW_USER_DIR", str(tmp_path / "another-user-store"))  # a different destination
    moved = Session(monkeypatch, capsys, home, project, ["n"])
    assert _migrate(moved, project) is False and moved.prompts() == 1


def _proprietary(session: Session, project: Path, backend: str, pins: dict[str, str] | None = None) -> list[str]:
    inst = session.installer

    def refuse(*_a: Any, **_k: Any) -> dict[str, str]:
        raise RuntimeError("stop before any network call")

    inst._post_proprietary_entitlement = refuse
    return inst.phase_install_proprietary(  # type: ignore[no-any-return]
        session.ui, 1, 1, sys.executable, "key", pins or {}, backend, project_dir=project
    )


def test_a_proprietary_consent_is_bound_to_the_backend_origin_and_the_package_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["y"])
    _proprietary(first, project, "https://backend.example")
    assert first.prompts() == 1
    first.finish()

    same = Session(monkeypatch, capsys, home, project, [])
    _proprietary(same, project, "HTTPS://Backend.Example:443/")  # the same origin, spelled differently
    assert same.prompts() == 0 and "will fetch from" not in same.out

    other_backend = Session(monkeypatch, capsys, home, project, ["n"])
    _proprietary(other_backend, project, "https://elsewhere.example")
    assert other_backend.prompts() == 1 and "will fetch from" in other_backend.out  # the listing is back too

    other_scope = Session(monkeypatch, capsys, home, project, ["n"])
    _proprietary(other_scope, project, "https://backend.example", {"trw-distill": "9.9.9"})
    assert other_scope.prompts() == 1


def test_a_recorded_ai_no_beats_an_anthropic_another_project_left_in_a_shared_interpreter(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["n"])
    assert first.installer.phase_prompt_features(first.ui, None, {}) is False
    first.finish()
    first.prompts()

    shared = Session(monkeypatch, capsys, home, project, [])
    detected = {"ai": True}  # `anthropic` imports: it came from somewhere else
    assert shared.installer.phase_prompt_features(shared.ui, None, detected) is False
    assert shared.prompts() == 0 and "AI extras: off (your earlier answer" in shared.out
    assert "from prior install" not in shared.out

    assert shared.installer.phase_prompt_features(shared.ui, True, detected) is True  # --ai still wins first


def test_reconfigure_asks_about_ai_extras_even_when_anthropic_imports(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    redo = Session(monkeypatch, capsys, home, project, [""], reconfigure=True)  # Enter keeps the detected default
    assert redo.installer.phase_prompt_features(redo.ui, None, {"ai": True}) is True
    assert redo.prompts() == 1


def test_a_detected_anthropic_is_still_used_when_nothing_was_ever_recorded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    session = Session(monkeypatch, capsys, home, project, [])
    assert session.installer.phase_prompt_features(session.ui, None, {"ai": True}) is True
    assert session.prompts() == 0 and "from prior install" in session.out


@pytest.mark.parametrize(("answer", "expected"), [("", False), ("n", False), ("y", True)])
def test_reconfigure_asks_the_embeddings_preference_even_when_the_stack_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    world: tuple[Path, Path],
    answer: str,
    expected: bool,
) -> None:
    """A prior refusal plus a healthy stack: --reconfigure used to turn it into True with no question."""
    home, project = world
    session = Session(monkeypatch, capsys, home, project, [answer], reconfigure=True)
    chosen = session.installer.resolve_embeddings_choice(None, {"embeddings": False}, interactive=True)

    assert chosen is expected  # Enter keeps the refusal; only an explicit yes changes it
    assert session.prompts() == 1


def test_a_failed_flush_keeps_the_update_pending_and_names_the_old_answer_and_the_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    monkeypatch.setenv("HOME", str(home))  # the machine layer `config set` writes is under HOME
    inst = _installer()
    key = "system_python_abc123"
    (home / ".trw" / "config.yaml").write_text(f"installer_answers:\n  {key}: true\n", encoding="utf-8")
    store = inst.AnswerStore(project / ".trw" / "config.yaml", home / ".trw" / "config.yaml")
    store.remember(key, False, "machine")
    ui = inst.UI(interactive=True)

    store.flush(ui, [sys.executable, "-c", "import sys; sys.exit(1)"], project)  # the writer fails
    out = capsys.readouterr().out
    assert "PREVIOUS answer (true) is still on file" in out
    assert f"installer_answers.{key} false" in out and "--scope machine" in out  # the exact command
    assert "will be asked again" not in out
    assert store._pending == [("machine", key)]  # kept for the next attempt

    store.flush(ui, [sys.executable, "-B", "-m", "trw_mcp.server"], project)  # trw-mcp works now
    assert inst._read_installer_answers(home / ".trw" / "config.yaml")[key] is False
    assert store._pending == []


def test_an_explicit_api_key_beats_a_saved_platform_decline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["", "n"])
    first.installer._prompt_api_key = lambda _ui: ""
    first.installer.phase_configure(
        first.ui, 1, 1, project, True, "", "", None, {}, skip_auth=False, target_platforms=None
    )
    first.finish()
    first.prompts()

    key = "trw_dk_" + "a" * 32
    asked: list[int] = []
    second = Session(monkeypatch, capsys, home, project, ["", "n"])
    second.installer._prompt_api_key = lambda _ui: asked.append(1) or ""
    second.installer.phase_configure(
        second.ui, 1, 1, project, True, "", key, None, {}, skip_auth=False, target_platforms=None
    )
    second.prompts()

    assert asked == []  # not asked, and not told "offline"
    assert "API key: accepted (--api-key)" in second.out and "Platform: offline" not in second.out
    assert key in (project / ".trw" / "credentials.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    [
        "installer_answers:\n  ai_extras: false\n  migrate_learnings_ab12: true\n",
        "installer_answers: {ai_extras: false, migrate_learnings_ab12: true}\n",
        "installer_answers: {\n  ai_extras: false,\n  migrate_learnings_ab12: true  # kept\n}\n",
        "installer_answers:\n    'ai_extras': false\n    \"migrate_learnings_ab12\": true\n",
        "other: 1\ninstaller_answers: { ai_extras: False , migrate_learnings_ab12: TRUE }\nafter: 2\n",
    ],
    ids=["block", "flow", "flow-wrapped", "quoted-keys", "flow-between-keys"],
)
def test_the_reader_reads_every_representation(tmp_path: Path, text: str) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text, encoding="utf-8")
    assert _installer()._read_installer_answers(cfg) == {"ai_extras": False, "migrate_learnings_ab12": True}


def test_flow_style_survives_the_real_writer_round_trip(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("installation_id: proj\ninstaller_answers: {ai_extras: false}\n", encoding="utf-8")
    session = Session(monkeypatch, capsys, home, project, ["y"])
    assert (
        session.installer.phase_prompt_features(session.ui, None, {}) is False
    )  # the flow-style NO is read, not asked
    assert session.prompts() == 0
    session.installer._ANSWERS.remember("proprietary_upgrade", True)
    session.finish()  # config set rewrites the file

    assert session.installer._read_installer_answers(cfg) == {"ai_extras": False, "proprietary_upgrade": True}
    from trw_mcp.models.config._loader import config_for_trw_dir

    assert config_for_trw_dir(project / ".trw").installer_answers == {"ai_extras": False, "proprietary_upgrade": True}


# ── Answers survive an abort ────────────────────────────────────────────────────────────────────────────────────


def test_answers_are_on_file_before_a_later_step_can_abort(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    session = Session(monkeypatch, capsys, home, project, ["y", "n"])
    session.installer.persist_answers_as_given(session.ui, sys.executable, project)

    session.installer.phase_prompt_features(session.ui, None, {})  # the AI prompt: yes
    on_file = session.installer._read_installer_answers(project / ".trw" / "config.yaml")
    assert on_file == {"ai_extras": True}  # already written; nothing waited for a "step 6"

    session.installer.ask_once(session.ui, "proprietary_upgrade", "Upgrade?", label="x", change="y")  # no
    assert session.installer._read_installer_answers(project / ".trw" / "config.yaml")["proprietary_upgrade"] is False


def test_an_abort_in_project_setup_does_not_lose_the_ai_answer_for_an_unattended_rerun(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from tests._install_trw_main_support import drive_main, make_project

    home = tmp_path / "home"
    (home / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    project = make_project(tmp_path)
    installer = _installer()
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda _cls: home))
    monkeypatch.setattr(installer, "find_trw_cmd", lambda *_a, **_k: [sys.executable, "-B", "-m", "trw_mcp.server"])

    with pytest.raises(SystemExit):
        drive_main(installer, monkeypatch, project, interactive=True, tty_answers=["y"], abort_in_project_setup=True)
    capsys.readouterr()
    assert installer._read_installer_answers(project / ".trw" / "config.yaml").get("ai_extras") is True

    rerun = _installer()
    monkeypatch.setattr(rerun.Path, "home", classmethod(lambda _cls: home))
    drive_main(rerun, monkeypatch, project)  # unattended: no terminal, no flag
    out = capsys.readouterr().out
    assert "AI extras: on (your earlier answer" in out


def test_an_exit_hook_writes_whatever_is_still_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    session = Session(monkeypatch, capsys, home, project, [])
    hooks: list[Any] = []
    monkeypatch.setattr(session.installer.atexit, "register", hooks.append)
    session.installer.persist_answers_as_given(session.ui, sys.executable, project)
    store = session.installer._ANSWERS
    store.writer = None  # an answer given while writing is impossible, so it is left pending
    store.remember("ai_extras", True)
    assert session.installer._read_installer_answers(project / ".trw" / "config.yaml") == {}

    (hook,) = hooks
    hook()  # what the interpreter runs on any exit, including an abort

    assert session.installer._read_installer_answers(project / ".trw" / "config.yaml") == {"ai_extras": True}


# ── Review round 2 ──────────────────────────────────────────────────────────────────────────────────────────────


def _migrate_in(session: Session, project: Path, *, interactive: bool) -> bool:
    inst = session.installer
    db = project / ".trw" / "memory" / "memory.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"x")
    ran: list[bool] = []
    inst._run_python_output = lambda *_a, **_k: (0, "")
    inst._run_migrate_command = lambda *_a, **_k: ran.append(True) or (0, "", "")
    inst.phase_migrate_store(session.ui, sys.executable, project, migrate=True, interactive=interactive)
    return bool(ran)


def test_an_unattended_rerun_honours_a_remembered_migration_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    first = Session(monkeypatch, capsys, home, project, ["n"])
    assert _migrate_in(first, project, interactive=True) is False  # declined at the prompt
    first.finish()
    first.prompts()

    unattended = Session(monkeypatch, capsys, home, project, [])
    assert _migrate_in(unattended, project, interactive=False) is False  # the refusal binds a run that cannot ask
    unattended.prompts()
    assert "your earlier answer" in unattended.out


def test_an_unattended_run_with_no_answer_on_file_keeps_its_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    home, project = world
    session = Session(monkeypatch, capsys, home, project, [])
    assert _migrate_in(session, project, interactive=False) is True  # as before: migrate


def test_an_interrupt_during_the_first_write_loses_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], world: tuple[Path, Path]
) -> None:
    """Review P1-2: flush() emptied _pending first, so a Ctrl-C mid-write dropped the current and later entries."""
    home, project = world
    monkeypatch.setenv("HOME", str(home))
    session = Session(monkeypatch, capsys, home, project, [])
    inst = session.installer
    store = inst._ANSWERS
    for key in ("ai_extras", "proprietary_upgrade", "platform_connect"):
        store.remember(key, False)
    events: list[str] = []
    hooks: list[Any] = []
    monkeypatch.setattr(inst.atexit, "register", lambda fn: (events.append("register"), hooks.append(fn)))
    real_run = inst.subprocess.run

    def interrupted(*_a: Any, **_k: Any) -> Any:
        events.append("write")
        raise KeyboardInterrupt

    monkeypatch.setattr(inst.subprocess, "run", interrupted)

    with pytest.raises(KeyboardInterrupt):
        inst.persist_answers_as_given(session.ui, sys.executable, project)

    assert events == ["register", "write"]  # the exit hook was in place before the first write
    assert len(store._pending) == 3  # nothing was dropped
    monkeypatch.setattr(inst.subprocess, "run", real_run)
    (hook,) = hooks
    hook()  # the interpreter's exit: retries the lot
    assert inst._read_installer_answers(project / ".trw" / "config.yaml") == {
        "ai_extras": False,
        "proprietary_upgrade": False,
        "platform_connect": False,
    }
    assert store._pending == []
