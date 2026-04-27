"""Behavioral regression tests for install-trw.py --pip-target hardening."""

from __future__ import annotations

import importlib.util
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_INSTALLER_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"
_INSTALLER_ARTIFACT = Path(__file__).resolve().parent.parent / "dist" / "install-trw.py"
_INSTALLER_PATHS = [_INSTALLER_TEMPLATE, _INSTALLER_ARTIFACT]


def _load_installer_module(installer_path: Path):
    spec = importlib.util.spec_from_file_location(f"install_trw_test_{installer_path.stem}", installer_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_packages_writes_wrapper_and_verifies_imports_from_pip_target(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    pip_target = str(tmp_path / "trw-pip")
    wrapper_path = Path(pip_target) / "bin" / "trw-mcp"
    memory_whl = tmp_path / "trw-memory.whl"
    mcp_whl = tmp_path / "trw-mcp.whl"
    pip_calls: list[tuple[str, str]] = []
    run_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": pip_calls.append((package, target_dir)) or True,
    )

    def fake_run(
        cmd,
        env=None,
        stdout=None,
        stderr=None,
        *,
        capture_output=False,
        text=False,
        timeout=None,
        check=False,
        input=None,
        **_kwargs,
    ):
        run_calls.append({"cmd": cmd, "env": dict(env or {})})
        # The 2026-04-21 MCP preflight probe passes capture_output=True and
        # inspects stdout/stderr; return a response that contains
        # trw_session_start so the probe gate passes.
        return SimpleNamespace(
            returncode=0,
            stdout='{"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"trw_session_start"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    # The preflight probe runs the wrapper binary directly; in this test it
    # resolves to a non-existent path (wrapper is written via Path.write_text
    # but the subprocess would normally fork the wrapper script). Pre-create
    # the wrapper file so the Path.is_file() gate passes.
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)

    module.phase_install_packages(
        ui,
        2,
        4,
        sys.executable,
        memory_whl,
        mcp_whl,
        pip_target=pip_target,
    )

    # pip_install may or may not be called depending on whether the
    # installer routes the pip-target install through the helper or runs
    # `subprocess.run` directly. Either is acceptable as long as BOTH
    # wheels end up installed; assert on the union of observed calls.
    installed_wheels: list[tuple[str, str]] = list(pip_calls)
    for call in run_calls:
        cmd = call["cmd"]
        if isinstance(cmd, list) and "pip" in cmd and "install" in cmd:
            # 2026-04-21 L-8heG v2: phase_install_packages now invokes pip
            # with BOTH wheels in a single command (--find-links). Count
            # every .whl arg, not just the first.
            for arg in cmd:
                if str(arg).endswith(".whl"):
                    installed_wheels.append((str(arg), pip_target))
    assert (str(memory_whl), pip_target) in installed_wheels
    assert (str(mcp_whl), pip_target) in installed_wheels

    assert wrapper_path.read_text(encoding="utf-8") == (
        "#!/bin/bash\n"
        f"export PYTHONPATH={pip_target}:$PYTHONPATH\n"
        f'exec {sys.executable} -B -c "from trw_mcp.server import main; main()" "$@"\n'
    )
    # Filter run_calls to just the import-verification calls to assert on.
    verify_calls = [call for call in run_calls if "-c" in call["cmd"]]
    assert [call["cmd"] for call in verify_calls] == [
        [sys.executable, "-B", "-c", "import trw_memory"],
        [sys.executable, "-B", "-c", "import trw_mcp"],
    ]
    assert all(call["env"]["PYTHONPATH"] == pip_target for call in verify_calls)
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in verify_calls)
    assert all(call["env"]["PIP_NO_CACHE_DIR"] == "1" for call in verify_calls)
    assert all(call["env"]["PIP_CACHE_DIR"] == f"{pip_target}/.cache/pip" for call in verify_calls)
    assert all(call["env"]["XDG_CACHE_HOME"] == f"{pip_target}/.cache" for call in verify_calls)
    assert all(call["env"]["TMPDIR"] == f"{pip_target}/.tmp" for call in verify_calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_packages_keeps_default_install_behavior_without_pip_target(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    wrapper_path = tmp_path / "trw-pip" / "bin" / "trw-mcp"
    memory_whl = tmp_path / "trw-memory.whl"
    mcp_whl = tmp_path / "trw-mcp.whl"
    pip_calls: list[tuple[str, str]] = []
    run_calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": pip_calls.append((package, target_dir)) or True,
    )
    monkeypatch.setenv("PYTHONPATH", "preexisting-path")

    def fake_run(
        cmd,
        env=None,
        stdout=None,
        stderr=None,
        *,
        capture_output=False,
        text=False,
        timeout=None,
        check=False,
        **_kwargs,
    ):
        run_calls.append({"cmd": cmd, "env": dict(env or {})})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.phase_install_packages(
        ui,
        2,
        4,
        sys.executable,
        memory_whl,
        mcp_whl,
    )

    # 2026-04-21 L-8heG v2: phase_install_packages now tries a combined
    # pip invocation first, then falls back to sequential pip_install calls.
    # The combined call shows up in run_calls; sequential calls (if any)
    # show up in pip_calls. Assert the union installs BOTH wheels.
    installed_wheels: list[str] = [p for p, _ in pip_calls]
    for call in run_calls:
        cmd = call["cmd"]
        if isinstance(cmd, list) and "pip" in cmd and "install" in cmd:
            for arg in cmd:
                if str(arg).endswith(".whl"):
                    installed_wheels.append(str(arg))
    assert str(memory_whl) in installed_wheels, f"expected memory wheel install; got {installed_wheels}"
    assert str(mcp_whl) in installed_wheels, f"expected mcp wheel install; got {installed_wheels}"
    assert not wrapper_path.exists()
    # Filter run_calls to just the import-verification calls.
    verify_calls = [call for call in run_calls if "-c" in call["cmd"]]
    assert [call["cmd"] for call in verify_calls] == [
        [sys.executable, "-B", "-c", "import trw_memory"],
        [sys.executable, "-B", "-c", "import trw_mcp"],
    ]
    assert all(call["env"]["PYTHONPATH"] == "preexisting-path" for call in verify_calls)
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in verify_calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_disables_bytecode_writes(installer_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[dict[str, object]] = []

    def fake_run(
        cmd,
        env=None,
        stdout=None,
        stderr=None,
        timeout=None,
        *,
        capture_output=False,
        text=False,
        check=False,
        **_kwargs,
    ):
        calls.append({"cmd": cmd, "env": dict(env or {}), "timeout": timeout})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module.pip_install(sys.executable, "trw-mcp[ai]", "trw-mcp", ui, target_dir="/tmp/trw-pip") is True
    assert [call["cmd"] for call in calls] == [
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--quiet",
            "--target",
            "/tmp/trw-pip",
            "trw-mcp[ai]",
        ]
    ]
    assert all(call["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in calls)
    assert all(call["env"]["PIP_NO_CACHE_DIR"] == "1" for call in calls)
    assert all(call["env"]["PIP_CACHE_DIR"] == "/tmp/trw-pip/.cache/pip" for call in calls)
    assert all(call["env"]["XDG_CACHE_HOME"] == "/tmp/trw-pip/.cache" for call in calls)
    assert all(call["env"]["TMPDIR"] == "/tmp/trw-pip/.tmp" for call in calls)


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_adds_no_deps_for_target_wheels_when_runtime_deps_are_already_present(
    installer_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[list[str]] = []

    monkeypatch.setattr(module, "_wheel_runtime_dependencies_satisfied", lambda wheel_path: True)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda cmd, env=None, stdout=None, stderr=None, timeout=None: (
            calls.append(cmd) or SimpleNamespace(returncode=0)
        ),
    )

    assert module.pip_install(
        sys.executable, "/tmp/trw_mcp-0.41.1-py3-none-any.whl", "trw-mcp", ui, target_dir="/tmp/trw-pip"
    )
    assert calls == [
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--quiet",
            "--no-deps",
            "--target",
            "/tmp/trw-pip",
            "/tmp/trw_mcp-0.41.1-py3-none-any.whl",
        ]
    ]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_pip_install_keeps_dependency_resolution_for_target_packages_when_runtime_deps_are_missing(
    installer_path: Path, monkeypatch
) -> None:
    """When the runtime dep check returns False (ambiguous), pip_install
    must NOT force --no-deps — it must let pip's resolver fetch transitive
    deps from PyPI normally. Forcing --no-deps here was the 2026-04-21
    iter-18-replication-v2 regression: structlog was skipped and trw-mcp
    crashed on import in container Python that lacked the dev env's
    pre-installed deps. The correct fix lives in phase_install_packages:
    install BOTH bundled wheels in one pip invocation with --find-links so
    the resolver satisfies internal deps locally while fetching external
    deps from PyPI. See test_phase_install_packages_uses_combined_find_links.
    """
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    calls: list[list[str]] = []

    monkeypatch.setattr(module, "_wheel_runtime_dependencies_satisfied", lambda wheel_path: False)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda cmd, env=None, stdout=None, stderr=None, timeout=None: (
            calls.append(cmd) or SimpleNamespace(returncode=0)
        ),
    )

    assert module.pip_install(
        sys.executable, "/tmp/trw_mcp-0.41.1-py3-none-any.whl", "trw-mcp", ui, target_dir="/tmp/trw-pip"
    )
    assert calls == [
        [
            sys.executable,
            "-B",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--quiet",
            "--target",
            "/tmp/trw-pip",
            "/tmp/trw_mcp-0.41.1-py3-none-any.whl",
        ]
    ]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_runtime_dependencies_satisfied_parses_requires_dist(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            "demo-0.1.0.dist-info/METADATA",
            (
                "Metadata-Version: 2.1\n"
                "Name: demo\n"
                "Version: 0.1.0\n"
                "Requires-Dist: fastmcp>=3.0.0\n"
                "Requires-Dist: platformdirs>=4.0.0; python_version >= '3.10'\n"
            ),
        )

    versions = {"fastmcp": "3.2.3", "platformdirs": "4.9.6"}
    monkeypatch.setattr(module.importlib_metadata, "version", lambda name: versions[name])

    assert module._wheel_runtime_dependencies_satisfied(wheel_path) is True


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_runtime_dependencies_satisfied_returns_false_when_a_dependency_is_missing(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            "demo-0.1.0.dist-info/METADATA",
            ("Metadata-Version: 2.1\nName: demo\nVersion: 0.1.0\nRequires-Dist: missing-dep>=1.0.0\n"),
        )

    def fake_version(name: str) -> str:
        raise module.importlib_metadata.PackageNotFoundError

    monkeypatch.setattr(module.importlib_metadata, "version", fake_version)

    assert module._wheel_runtime_dependencies_satisfied(wheel_path) is False


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_wheel_runtime_dependencies_satisfied_returns_false_when_packaging_is_unavailable(
    installer_path: Path, tmp_path: Path
) -> None:
    module = _load_installer_module(installer_path)
    wheel_path = tmp_path / "demo-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr(
            "demo-0.1.0.dist-info/METADATA",
            ("Metadata-Version: 2.1\nName: demo\nVersion: 0.1.0\nRequires-Dist: fastmcp>=3.0.0\n"),
        )

    real_import = __import__

    def fake_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> Any:
        if name == "packaging.requirements":
            raise ModuleNotFoundError("No module named 'packaging'")
        return real_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=fake_import):
        assert module._wheel_runtime_dependencies_satisfied(wheel_path) is False


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_install_extras_passes_pip_target_to_all_optional_installs(installer_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    pip_calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        module,
        "pip_install",
        lambda python, package, label, ui, target_dir="": pip_calls.append((package, target_dir)) or True,
    )

    features = module.phase_install_extras(
        ui,
        3,
        5,
        sys.executable,
        install_ai=True,
        install_sqlitevec=True,
        pip_target="/tmp/trw-pip",
    )

    assert pip_calls == [
        ("trw-mcp[ai]", "/tmp/trw-pip"),
        ("sentence-transformers>=2.0.0", "/tmp/trw-pip"),
        ("sqlite-vec", "/tmp/trw-pip"),
    ]
    assert features == ["AI/LLM", "embeddings", "sqlite-vec"]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_validate_pip_target_rejects_shell_metacharacters(installer_path: Path) -> None:
    module = _load_installer_module(installer_path)

    assert module.validate_pip_target("/tmp/trw-pip/subdir") == "/tmp/trw-pip/subdir"

    for invalid in ("", "/tmp/trw-pip;rm -rf /", "$(pwd)"):
        if invalid == "":
            assert module.validate_pip_target(invalid) == ""
        else:
            try:
                module.validate_pip_target(invalid)
            except ValueError as exc:
                assert "Invalid --pip-target" in str(exc)
            else:
                raise AssertionError(f"{invalid!r} should be rejected")


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_main_threads_pip_target_into_extras_phase_when_enabled(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_project_setup", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)

    def fake_phase_install_extras(ui, step, total, python, install_ai, install_sqlitevec, pip_target=""):
        observed.update(
            {
                "step": step,
                "total": total,
                "python": python,
                "install_ai": install_ai,
                "install_sqlitevec": install_sqlitevec,
                "pip_target": pip_target,
            }
        )
        return ["AI/LLM", "embeddings", "sqlite-vec"]

    monkeypatch.setattr(module, "phase_install_extras", fake_phase_install_extras)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--ai",
            "--sqlite-vec",
            "--pip-target",
            "/tmp/trw-pip",
            str(project_dir),
        ],
    )

    module.main()

    assert observed == {
        "step": 3,
        "total": 4,
        "python": sys.executable,
        "install_ai": True,
        "install_sqlitevec": True,
        "pip_target": "/tmp/trw-pip",
    }


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_find_trw_cmd_prefers_pip_target_wrapper(installer_path: Path, tmp_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    wrapper = tmp_path / "trw-pip" / "bin" / "trw-mcp"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/bash\n", encoding="utf-8")
    wrapper.chmod(0o755)

    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/local/bin/trw-mcp")

    assert module.find_trw_cmd(sys.executable, pip_target=str(tmp_path / "trw-pip")) == [str(wrapper)]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_parse_ide_argument_supports_comma_separated_multi_select(installer_path: Path) -> None:
    module = _load_installer_module(installer_path)

    assert module._parse_ide_argument("cursor-ide,codex,gemini") == ["cursor-ide", "codex", "gemini"]
    assert module._parse_ide_argument("all") == [
        "claude-code",
        "cursor-ide",
        "cursor-cli",
        "opencode",
        "codex",
        "copilot",
        "gemini",
        "aider",
    ]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_phase_project_setup_prefers_pip_target_wrapper_for_multi_client_setup(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    ui = MagicMock()
    target_dir = tmp_path / "project"
    wrapper = tmp_path / "trw-pip" / "bin" / "trw-mcp"
    target_dir.mkdir()
    (target_dir / ".git").mkdir()
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/bash\n", encoding="utf-8")
    wrapper.chmod(0o755)

    run_calls: list[list[str]] = []

    monkeypatch.setattr(module, "_detect_installed_clis", list)
    monkeypatch.setattr(module, "_detect_project_ides", lambda _path: ["cursor-ide", "codex"])
    monkeypatch.setattr(module, "run_with_progress", lambda _ui, _label, cmd: run_calls.append(cmd) or True)

    selected = module.phase_project_setup(
        ui,
        3,
        4,
        sys.executable,
        target_dir,
        False,
        interactive=False,
        ide=["cursor-ide", "codex"],
        pip_target=str(tmp_path / "trw-pip"),
    )

    assert selected == ["cursor-ide", "codex"]
    assert run_calls == [
        [str(wrapper), "init-project", str(target_dir), "--ide", "cursor-ide"],
        [str(wrapper), "update-project", str(target_dir), "--ide", "codex"],
    ]


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_main_threads_pip_target_into_project_setup(installer_path: Path, tmp_path: Path, monkeypatch) -> None:
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_install_extras", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)
    monkeypatch.setattr(
        module,
        "phase_project_setup",
        lambda ui, step, total, python, target_dir, upgrade_only, interactive=False, ide=None, pip_target="": (
            observed.update(
                {
                    "step": step,
                    "total": total,
                    "python": python,
                    "target_dir": target_dir,
                    "upgrade_only": upgrade_only,
                    "interactive": interactive,
                    "ide": ide,
                    "pip_target": pip_target,
                }
            )
        ),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--pip-target",
            "/tmp/trw-pip",
            str(project_dir),
        ],
    )

    module.main()

    assert observed == {
        "step": 3,
        "total": 3,
        "python": sys.executable,
        "target_dir": project_dir,
        "upgrade_only": False,
        "interactive": False,
        "ide": None,
        "pip_target": "/tmp/trw-pip",
    }


@pytest.mark.parametrize("installer_path", _INSTALLER_PATHS, ids=["template", "artifact"])
def test_main_parses_multi_client_ide_argument_for_project_setup(
    installer_path: Path, tmp_path: Path, monkeypatch
) -> None:
    module = _load_installer_module(installer_path)
    project_dir = tmp_path / "project"
    scratch_dir = tmp_path / "scratch"
    project_dir.mkdir()
    scratch_dir.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(module, "show_banner", lambda ui: None)
    monkeypatch.setattr(module, "show_success_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_prior_config", lambda target_dir, ui=None: {})
    monkeypatch.setattr(module, "check_python_version", lambda ui: sys.executable)
    monkeypatch.setattr(
        module, "phase_extract_wheels", lambda ui, step, total, tmpdir: (tmp_path / "m.whl", tmp_path / "c.whl")
    )
    monkeypatch.setattr(module, "phase_install_packages", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "phase_install_extras", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "_restart_mcp_servers", lambda target_dir, ui: None)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda prefix="": str(scratch_dir))
    monkeypatch.setattr(module.shutil, "rmtree", lambda path: None)
    monkeypatch.setattr(
        module,
        "phase_project_setup",
        lambda ui, step, total, python, target_dir, upgrade_only, interactive=False, ide=None, pip_target="": (
            observed.update(
                {
                    "step": step,
                    "total": total,
                    "python": python,
                    "target_dir": target_dir,
                    "upgrade_only": upgrade_only,
                    "interactive": interactive,
                    "ide": ide,
                    "pip_target": pip_target,
                }
            )
            or ["cursor-ide", "codex", "gemini"]
        ),
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install-trw.py",
            "--script",
            "--ide",
            "cursor-ide,codex,gemini",
            str(project_dir),
        ],
    )

    module.main()

    assert observed["ide"] == ["cursor-ide", "codex", "gemini"]
