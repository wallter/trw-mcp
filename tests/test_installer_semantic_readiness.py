"""The installer makes semantic retrieval work by default, or fails loudly (docs/sprint-mcp7/PLAN.md §3b item 2).

Defect this pins (verified 2026-09-15): ``sentence-transformers`` and ``torch``
were absent from a *working* TRW install. Nothing errored. ``trw_recall``
silently fell back to keyword-only retrieval, and the install had appeared to
succeed. 6.1.0 closes the rest of that gap:

* embeddings are ON by default in every mode; ``--no-embeddings`` (or a prior
  ``embeddings_enabled: false``) is the only way to keyword-only, and the choice
  is written to the project config;
* ``--script`` repairs without asking; interactive asks, and "no" is the opt-out;
* a wanted-but-broken stack fails the install with a non-zero exit;
* the model is the project's configured ``retrieval_embedding_model``, resolved
  by trw-mcp's own config loader in the target interpreter, and it is fetched as
  the embedder loads it — ``SentenceTransformer(model)`` at revision ``main``,
  which writes the cache ref a commit-pinned ``snapshot_download`` never wrote.

Only the TEMPLATE is loaded. ``dist/install-trw.py`` is a generated artifact
(untracked, rebuilt by ``make installer``); the template->dist drift gate
(``scripts/check-installer-drift.py``) is what guarantees they agree.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._install_trw_main_support import drive_main, make_project
from tests._layout import PACKAGE_ROOT

_TEMPLATE = PACKAGE_ROOT / "scripts" / "install-trw.template.py"
_MODEL = "org/configured-model"


@pytest.fixture
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_semantic_readiness", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── probe, model resolution and download ─────────────────────────────


@pytest.mark.parametrize(
    ("exit_code_attr", "expected_attr"),
    [
        (None, "SEMANTIC_OK"),
        ("_SEMANTIC_EXIT_MISSING_LIBRARY", "SEMANTIC_MISSING_LIBRARY"),
        ("_SEMANTIC_EXIT_MISSING_WEIGHTS", "SEMANTIC_MISSING_WEIGHTS"),
        ("_SEMANTIC_EXIT_UNKNOWN", "SEMANTIC_UNKNOWN"),
    ],
)
def test_probe_maps_exit_codes_to_outcomes(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, exit_code_attr: str | None, expected_attr: str
) -> None:
    code = 0 if exit_code_attr is None else getattr(installer, exit_code_attr)
    monkeypatch.setattr(installer, "_run_python_probe", lambda *a, **k: code)
    assert installer.probe_semantic_stack("/some/python", _MODEL) == getattr(installer, expected_attr)


def test_an_unresolved_model_is_unknown_without_probing(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer, "_run_python_probe", lambda *a, **k: pytest.fail("no model, nothing to probe"))
    assert installer.probe_semantic_stack("/some/python", "") == installer.SEMANTIC_UNKNOWN


def test_probe_targets_the_supplied_interpreter_and_model(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env TRW runs in can differ from the installer's own interpreter."""
    seen: list[tuple[list[str], str]] = []

    def fake_probe(cmd: list[str], target_dir: str = "", timeout: int = 60) -> int:
        seen.append((cmd, target_dir))
        return 0

    monkeypatch.setattr(installer, "_run_python_probe", fake_probe)
    installer.probe_semantic_stack("/opt/trw-venv/bin/python", _MODEL, target_dir="/pip/target")
    cmd, target = seen[0]
    assert cmd[0] == "/opt/trw-venv/bin/python"
    assert cmd[-1] == _MODEL
    assert target == "/pip/target"


def test_the_probe_is_trw_mcps_own_retrieval_probe(installer: ModuleType) -> None:
    """One verdict: the installer asks the probe ``trw-mcp doctor``'s retrieval row reports."""
    source = installer._SEMANTIC_PROBE_SOURCE
    compile(source, "<probe>", "exec")
    assert "from trw_mcp.state._retrieval_capability import probe_retrieval" in source


def test_the_download_loads_the_model_as_the_embedder_does(installer: ModuleType) -> None:
    """Revision ``main``, via sentence-transformers: a commit-pinned snapshot writes no refs/main,
    so the runtime's cache-first load could not see it; and no model name is hard-coded."""
    source = installer._SEMANTIC_DOWNLOAD_SOURCE
    compile(source, "<download>", "exec")
    assert "SentenceTransformer(sys.argv[1])" in source
    template = _TEMPLATE.read_text(encoding="utf-8")
    assert "snapshot_download(" not in template
    assert "all-MiniLM" not in template


def test_download_passes_the_model_to_the_target_interpreter(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], str]] = []
    monkeypatch.setattr(
        installer, "_run_python_smoke", lambda cmd, target_dir="", timeout=120: seen.append((cmd, target_dir)) or True
    )
    assert installer.download_semantic_model("/opt/py", _MODEL, target_dir="/pip/target")
    assert seen == [(["/opt/py", "-B", "-c", installer._SEMANTIC_DOWNLOAD_SOURCE, _MODEL], "/pip/target")]


def test_the_model_is_read_from_the_projects_config(installer: ModuleType, tmp_path: Path) -> None:
    """Resolved by trw-mcp's own config loader in the target interpreter: not a second hard-coded name."""
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(f"retrieval_embedding_model: {_MODEL}\n", encoding="utf-8")

    assert installer.configured_embedding_model(sys.executable, tmp_path) == _MODEL


def test_an_unconfigured_project_gets_trw_mcps_default_model(installer: ModuleType, tmp_path: Path) -> None:
    from trw_mcp.models.config import TRWConfig

    assert installer.configured_embedding_model(sys.executable, tmp_path) == TRWConfig().retrieval_embedding_model


# ── phase behaviour ──────────────────────────────────────────────────


@pytest.fixture
def stack(installer: ModuleType, monkeypatch: pytest.MonkeyPatch) -> dict[str, list[object]]:
    """Stub model resolution and record every install/download/prompt the phase makes."""
    calls: dict[str, list[object]] = {"pip": [], "download": [], "prompt": []}
    monkeypatch.setattr(installer, "configured_embedding_model", lambda *a, **k: _MODEL)

    def fake_pip(python: str, package: str, label: str, ui: object, target_dir: str = "") -> bool:
        calls["pip"].append((package, target_dir))
        return True

    def fake_download(python: str, model: str, target_dir: str = "", timeout: int = 900) -> bool:
        calls["download"].append((model, target_dir))
        return True

    monkeypatch.setattr(installer, "pip_install", fake_pip)
    monkeypatch.setattr(installer, "download_semantic_model", fake_download)
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: calls["prompt"].append(a) or True)
    return calls


def _statuses(installer: ModuleType, monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    sequence = iter([getattr(installer, name) for name in names])
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *a, **k: next(sequence))


def _run(installer: ModuleType, *, interactive: bool = False, offline: bool = False) -> str:
    return installer.phase_semantic_readiness(
        installer.UI(interactive=interactive),
        "/opt/py",
        project_dir=Path("/project"),
        interactive=interactive,
        pip_target="",
        offline=offline,
    )


def test_healthy_environment_is_quiet_and_changes_nothing(
    installer: ModuleType,
    stack: dict[str, list[object]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Idempotence: a second run with everything present must not alarm or act."""
    _statuses(installer, monkeypatch, "SEMANTIC_OK")

    assert _run(installer, interactive=True) == installer.SEMANTIC_OK
    assert stack == {"pip": [], "download": [], "prompt": []}
    assert not re.search(r"WARN|not active|keyword-only", capsys.readouterr().out)


def test_script_mode_installs_the_extra_then_the_model_without_asking(
    installer: ModuleType, stack: dict[str, list[object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _statuses(installer, monkeypatch, "SEMANTIC_MISSING_LIBRARY", "SEMANTIC_MISSING_WEIGHTS", "SEMANTIC_OK")

    assert _run(installer) == installer.SEMANTIC_OK
    assert stack["pip"] == [(installer.SEMANTIC_EXTRA_SPEC, "")]
    assert stack["download"] == [(_MODEL, "")]
    assert stack["prompt"] == [], "a non-interactive run never reads stdin"


def test_missing_weights_downloads_the_configured_model_and_never_pip_installs(
    installer: ModuleType,
    stack: dict[str, list[object]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _statuses(installer, monkeypatch, "SEMANTIC_MISSING_WEIGHTS", "SEMANTIC_OK")

    assert _run(installer) == installer.SEMANTIC_OK
    assert stack["pip"] == []
    assert stack["download"] == [(_MODEL, "")]
    assert f"SentenceTransformer('{_MODEL}')" in capsys.readouterr().out


def test_a_failed_repair_returns_the_gap_not_ok(
    installer: ModuleType, stack: dict[str, list[object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _statuses(installer, monkeypatch, "SEMANTIC_MISSING_LIBRARY")
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: False)

    assert _run(installer) == installer.SEMANTIC_MISSING_LIBRARY


def test_interactive_no_is_the_opt_out(
    installer: ModuleType, stack: dict[str, list[object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    _statuses(installer, monkeypatch, "SEMANTIC_MISSING_LIBRARY")
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: False)

    assert _run(installer, interactive=True) == installer.SEMANTIC_DECLINED
    assert stack["pip"] == [] and stack["download"] == []


def test_offline_fetches_nothing_and_names_the_opt_out(
    installer: ModuleType,
    stack: dict[str, list[object]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _statuses(installer, monkeypatch, "SEMANTIC_MISSING_LIBRARY")

    assert _run(installer, interactive=True, offline=True) == installer.SEMANTIC_MISSING_LIBRARY
    assert stack == {"pip": [], "download": [], "prompt": []}
    out = capsys.readouterr().out
    assert "Offline mode" in out
    assert "--no-embeddings" in out


# ── the choice ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("flag", "prior", "expected"),
    [
        (None, {}, True),
        (None, {"embeddings": True}, True),
        (None, {"embeddings": False}, False),
        (True, {"embeddings": False}, True),
        (False, {}, False),
    ],
)
def test_embeddings_are_on_unless_the_flag_or_a_prior_opt_out_says_otherwise(
    installer: ModuleType, flag: bool | None, prior: dict[str, object], expected: bool
) -> None:
    assert installer.resolve_embeddings_choice(flag, prior) is expected


def test_the_choice_replaces_or_appends_the_config_line(installer: ModuleType, tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("installation_id: p\nembeddings_enabled: true\nother: 1", encoding="utf-8")

    installer.persist_embeddings_choice(config, False)
    assert config.read_text(encoding="utf-8") == "installation_id: p\nother: 1\nembeddings_enabled: false\n"

    installer.persist_embeddings_choice(config, True)
    assert config.read_text(encoding="utf-8").count("embeddings_enabled") == 1
    assert "embeddings_enabled: true\n" in config.read_text(encoding="utf-8")

    installer.persist_embeddings_choice(tmp_path / "absent.yaml", False)
    assert not (tmp_path / "absent.yaml").exists()


# ── main() ───────────────────────────────────────────────────────────


def _config(target: Path) -> str:
    return (target / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_a_default_script_install_runs_the_phase_and_records_embeddings_on(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = make_project(tmp_path)

    run = drive_main(installer, monkeypatch, target)

    assert len(run.calls["semantic"]) == 1
    assert run.calls["semantic"][0][1]["project_dir"] == target
    assert "embeddings_enabled: true" in _config(target)


def test_no_embeddings_skips_the_phase_and_records_the_choice(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = make_project(tmp_path)

    run = drive_main(installer, monkeypatch, target, extra_argv=("--no-embeddings",))

    assert run.calls["semantic"] == []
    assert "embeddings_enabled: false" in _config(target)


def test_a_prior_opt_out_is_kept_until_embeddings_is_passed(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = make_project(tmp_path)
    (target / ".trw" / "config.yaml").write_text("installation_id: proj\nembeddings_enabled: false\n", encoding="utf-8")

    assert drive_main(installer, monkeypatch, target).calls["semantic"] == []
    assert "embeddings_enabled: false" in _config(target)

    assert len(drive_main(installer, monkeypatch, target, extra_argv=("--embeddings",)).calls["semantic"]) == 1
    assert "embeddings_enabled: true" in _config(target)


def test_a_declined_repair_installs_keyword_only_on_purpose(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = make_project(tmp_path)

    drive_main(installer, monkeypatch, target, semantic="declined")

    assert "embeddings_enabled: false" in _config(target)


@pytest.mark.parametrize("status", ["missing_library", "missing_weights", "unknown"])
def test_wanted_but_not_working_embeddings_fail_the_install(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    """Never a green banner over keyword-only recall: setup finishes, then the exit is non-zero."""
    target = make_project(tmp_path)

    with pytest.raises(SystemExit) as exc:
        drive_main(installer, monkeypatch, target, semantic=status)

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "--no-embeddings" in captured.out + captured.err
    assert "embeddings_enabled: true" in _config(target), "the wanted state is still recorded"


def test_a_host_provided_stack_is_recorded_on_and_not_installed(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PRD-EVAL-031 eval containers: the overlay feeds the MCP server's env, not the installer's."""
    target = make_project(tmp_path)

    run = drive_main(installer, monkeypatch, target, env={"TRW_EMBEDDINGS_AVAILABLE": "1"}, semantic="missing_library")

    assert run.calls["semantic"] == []
    assert "embeddings_enabled: true" in _config(target)


def test_no_embeddings_outranks_the_host_overlay(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = make_project(tmp_path)

    drive_main(installer, monkeypatch, target, extra_argv=("--no-embeddings",), env={"TRW_EMBEDDINGS_AVAILABLE": "1"})

    assert "embeddings_enabled: false" in _config(target)
