"""Drive the real ``install-trw.py`` ``main()`` with every side effect stubbed.

The installer's end-to-end decisions — which packages get installed, whether the
doctor runs, whether a missing credential aborts the whole install — live in
``main()``, not in any single helper. Testing them by grepping the template's
source text pins whitespace instead of behaviour; this harness runs the real
function instead, replacing only the phases that would touch the network, pip,
or the user's machine.

Everything the harness stubs is recorded in ``MainRun.calls`` so a test can
assert on what the flow decided (``calls["doctor"]``,
``calls["proprietary"]``, ``calls["project_setup"]``).
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

#: Environment that would leak the developer's own credentials/flags into a run.
_LEAKY_ENV = (
    "TRW_API_KEY",
    "TRW_PLATFORM_API_KEY",
    "TRW_LICENSE_KEY",
    "TRW_WITH_PROPRIETARY",
    "TRW_BACKEND_URL",
    "TRW_TARGET_PYTHON",
    "TRW_ALLOW_SYSTEM_PYTHON",
    "TRW_INSTALL_SQLITE_VEC",
    "TRW_INSTALL_EMBEDDINGS",
)


class MainRun:
    """The recorded outcome of one ``main()`` invocation."""

    def __init__(self) -> None:
        self.calls: dict[str, list[Any]] = {
            "install": [],
            "doctor": [],
            "proprietary": [],
            "project_setup": [],
            "configure": [],
            "warnings": [],
        }


def make_project(tmp_path: Path, *, targets: tuple[str, ...] = ("claude-code",)) -> Path:
    """A project directory that looks like a prior TRW install."""
    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)
    trw = target / ".trw"
    trw.mkdir()
    (trw / "installer-meta.yaml").write_text("framework_version: v26.2_TRW\n", encoding="utf-8")
    (trw / "config.yaml").write_text(
        "installation_id: proj\ntarget_platforms:\n" + "".join(f"  - {t}\n" for t in targets),
        encoding="utf-8",
    )
    return target


def drive_main(
    installer: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    target: Path,
    *,
    extra_argv: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> MainRun:
    """Run the real ``main()`` against *target* with all I/O phases stubbed.

    ``--script`` forces the non-interactive path (the ``curl … | bash`` shape).
    Raises ``SystemExit`` out to the caller — that is the exit code under test.
    """
    run = MainRun()

    for name in _LEAKY_ENV:
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)

    monkeypatch.setattr(installer.sys, "argv", ["install-trw.py", str(target), "--script", *extra_argv])

    def _record(key: str, result: Any) -> Any:
        def _stub(*args: Any, **kwargs: Any) -> Any:
            run.calls[key].append((args, kwargs))
            return result

        return _stub

    monkeypatch.setattr(installer, "check_python_version", lambda _ui: installer.sys.executable)
    monkeypatch.setattr(installer, "_detect_installed_extras", lambda _python: {})
    monkeypatch.setattr(
        installer,
        "phase_extract_wheels",
        lambda *_a, **_k: (target / "memory.whl", target / "mcp.whl"),
    )
    monkeypatch.setattr(installer, "phase_install_packages", _record("install", ""))
    monkeypatch.setattr(installer, "phase_install_extras", lambda *_a, **_k: [])
    monkeypatch.setattr(installer, "phase_install_proprietary", _record("proprietary", []))
    monkeypatch.setattr(installer, "_resolve_user_tier_consent", lambda *_a, **_k: False)
    monkeypatch.setattr(installer, "phase_project_setup", _record("project_setup", ["claude-code"]))
    monkeypatch.setattr(installer, "run_install_doctor", _record("doctor", None))
    monkeypatch.setattr(installer, "phase_configure", _record("configure", "offline"))
    monkeypatch.setattr(installer, "_restart_mcp_servers", lambda *_a, **_k: None)
    monkeypatch.setattr(installer, "_check_all_backends", lambda *_a, **_k: [])
    monkeypatch.setattr(installer, "show_success_banner", lambda *_a, **_k: None)
    monkeypatch.setattr(installer, "_emit_install_complete_event", lambda *_a, **_k: None)

    original_step_warn = installer.UI.step_warn

    def _capture_warn(self: Any, msg: str) -> None:
        run.calls["warnings"].append(msg)
        original_step_warn(self, msg)

    monkeypatch.setattr(installer.UI, "step_warn", _capture_warn)

    installer.main()
    return run
