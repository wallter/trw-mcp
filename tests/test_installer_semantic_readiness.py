"""The installer must notice a missing semantic-embeddings stack — every run.

Defect this pins (verified 2026-09-15): ``sentence-transformers`` and ``torch``
were absent from a *working* TRW install. Nothing errored. ``trw_recall``
silently fell back to keyword-only retrieval and attached a
``retrieval_warning`` ("The semantic model was not ready when recall started
... This response does not establish semantic coverage") that the user only
found by reading it in passing. The install had appeared to succeed.

Contract asserted here:

* the probe runs against the interpreter TRW will actually run under, and
  distinguishes "library missing" from "weights missing" — different remedies;
* the check is wired into ``main`` UNCONDITIONALLY, after the install doctor,
  not behind ``is_reinstall``/``install_ai``/a marker file, so an install that
  predates the check is re-offered the fix on every run;
* the non-interactive path warns and returns instead of reading stdin;
* a healthy environment prints nothing alarming and installs nothing.

Only the TEMPLATE is loaded. ``dist/install-trw.py`` is a generated artifact
(untracked, rebuilt by ``make installer``); the template->dist drift gate
(``scripts/check-installer-drift.py``) is what guarantees they agree.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE = _REPO_ROOT / "trw-mcp" / "scripts" / "install-trw.template.py"
_MEMORY_CI = _REPO_ROOT / "trw-memory" / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_trw_semantic_readiness", _TEMPLATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── probe classification ─────────────────────────────────────────────


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
    assert installer.probe_semantic_stack("/some/python") == getattr(installer, expected_attr)


def test_probe_targets_the_supplied_interpreter_not_sys_executable(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env TRW runs in can differ from the installer's own interpreter."""
    seen: list[list[str]] = []

    def fake_probe(cmd: list[str], target_dir: str = "", timeout: int = 60) -> int:
        seen.append(cmd)
        return 0

    monkeypatch.setattr(installer, "_run_python_probe", fake_probe)
    installer.probe_semantic_stack("/opt/trw-venv/bin/python", target_dir="/pip/target")
    assert seen and seen[0][0] == "/opt/trw-venv/bin/python"


def test_probe_source_checks_both_libraries_and_never_hits_the_network(installer: ModuleType) -> None:
    source = installer._semantic_probe_source()
    compile(source, "<probe>", "exec")  # it must be valid Python in the target
    assert "sentence_transformers" in source
    assert "torch" in source
    # Weights presence is resolved from the local HF cache only: a probe that
    # downloaded would turn a read-only check into a multi-hundred-MB surprise.
    assert "local_files_only=True" in source


def test_pinned_fixture_matches_the_trw_memory_ci_fixture(installer: ModuleType) -> None:
    """Do not invent a second pinned model: reuse trw-memory CI's snapshot.

    The step this reads was absent from the whole tracked history of that
    workflow until 2026-09-17 (``git log -S 'repo_id=' --`` returned nothing),
    so CI installed sentence-transformers and resolved whatever revision the hub
    served while the installer probed a pinned one with ``local_files_only=True``
    — one pin in name only. This is the drift guard for the restored pair.
    """
    ci = _MEMORY_CI.read_text(encoding="utf-8")
    assert f'repo_id="{installer.SEMANTIC_MODEL_REPO_ID}"' in ci
    assert f'revision="{installer.SEMANTIC_MODEL_REVISION}"' in ci


# ── phase behaviour ──────────────────────────────────────────────────


def _ui(installer: ModuleType, interactive: bool = False):
    return installer.UI(interactive=interactive)


def test_healthy_environment_is_quiet_and_changes_nothing(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Idempotence: a second run with the extra present must not alarm or act."""
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *a, **k: installer.SEMANTIC_OK)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pytest.fail("healthy env must not install anything"))
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: pytest.fail("healthy env must not prompt"))
    status = installer.phase_semantic_readiness(_ui(installer), "/py", interactive=True)
    out = capsys.readouterr().out
    assert status == installer.SEMANTIC_OK
    assert not re.search(r"WARN|not active|keyword-only", out), out


def test_non_interactive_missing_library_warns_and_never_reads_stdin(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *a, **k: installer.SEMANTIC_MISSING_LIBRARY)
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: pytest.fail("non-interactive run must not prompt"))
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pytest.fail("non-interactive run must not install"))

    status = installer.phase_semantic_readiness(_ui(installer), "/opt/py", interactive=False)
    out = capsys.readouterr().out

    assert status == installer.SEMANTIC_MISSING_LIBRARY
    # Honest copy: names what is lost, says recall still works, names the fix.
    assert "Semantic retrieval is NOT active" in out
    assert "keyword-only" in out
    assert "recall still works" in out
    assert "trw-memory[embeddings]" in out
    assert "/opt/py" in out


def test_missing_weights_offers_a_download_not_a_pip_install(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Library-present-but-weights-absent is a different failure + different fix."""
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *a, **k: installer.SEMANTIC_MISSING_WEIGHTS)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pytest.fail("weights gap must not pip-install"))

    status = installer.phase_semantic_readiness(_ui(installer), "/opt/py", interactive=False)
    out = capsys.readouterr().out

    assert status == installer.SEMANTIC_MISSING_WEIGHTS
    assert "model weights are not in the local cache" in out
    assert "snapshot_download" in out
    assert installer.SEMANTIC_MODEL_REVISION in out
    assert "pip install" not in out


def test_interactive_missing_library_installs_the_extra_on_yes(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    statuses = iter([installer.SEMANTIC_MISSING_LIBRARY, installer.SEMANTIC_OK])
    installed: list[str] = []
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *a, **k: next(statuses))
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: True)

    def fake_pip(python: str, package: str, label: str, ui, target_dir: str = "") -> bool:
        installed.append(package)
        return True

    monkeypatch.setattr(installer, "pip_install", fake_pip)
    status = installer.phase_semantic_readiness(_ui(installer, interactive=True), "/opt/py", interactive=True)
    assert installed == [installer.SEMANTIC_EXTRA_SPEC]
    assert status == installer.SEMANTIC_OK


def test_offline_skips_repair_but_still_warns(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(installer, "probe_semantic_stack", lambda *a, **k: installer.SEMANTIC_MISSING_LIBRARY)
    monkeypatch.setattr(installer, "pip_install", lambda *a, **k: pytest.fail("offline must not install"))
    monkeypatch.setattr(installer, "prompt_yes_no", lambda *a, **k: pytest.fail("offline must not prompt"))

    installer.phase_semantic_readiness(_ui(installer), "/opt/py", interactive=True, offline=True)
    out = capsys.readouterr().out
    assert "Offline mode" in out
    assert "Semantic retrieval is NOT active" in out


# ── wiring ───────────────────────────────────────────────────────────


def test_main_calls_the_check_unconditionally_after_the_doctor() -> None:
    """Re-prompt on re-run: the call must not sit behind any conditional.

    A marker-gated or ``install_ai``-gated call would leave every pre-existing
    install permanently keyword-only, which is the whole defect.
    """
    text = _TEMPLATE.read_text(encoding="utf-8")
    doctor = "        run_install_doctor(ui, python, target_dir, pip_target=args.pip_target)\n"
    assert text.count(doctor) == 1
    tail = text.split(doctor, 1)[1]
    call = "        phase_semantic_readiness(\n"
    assert call in tail, "phase_semantic_readiness is not called after run_install_doctor in main()"
    between = tail.split(call, 1)[0]
    # Same block indentation as the doctor call, and no branch opened between
    # them -> the call is unconditional on the install path.
    assert not re.search(r"^\s*(if|elif|else|try|for|while)\b", between, re.MULTILINE), between
