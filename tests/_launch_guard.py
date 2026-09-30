"""DISPATCH-TEST-LAUNCH-GUARD: no test launches a real, installed dispatch client unless it is marked ``live``.

Loaded from ``tests/conftest.py`` via ``pytest_plugins``. Found the hard way (L-PS6J, 2026-09-30): a red test
for a "falls through to the default client" bug ran real ``codex`` four times, because the code under test
launched through a path the test had not stubbed. A per-file stub cannot close that class, so this guard
sits under every path at once: :meth:`subprocess.Popen._execute_child`, which ``subprocess.run``, the
dispatch runner, the fan-out runner and the detached job runner all reach.

A launch is refused when its program is a registered client binary (``ClientSpec.binary_names``) that
resolves OUTSIDE the temp directory and this ``tests/`` tree -- i.e. a real install, not a fake a test
wrote into ``tmp_path``. A pure version/help probe (``codex --version``) and the read-only ``<client> mcp
list``/``mcp get`` config lookups are allowed: neither reaches a model. ``@pytest.mark.live`` is the only opt-out.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_PROBE_ARGS = frozenset({"--version", "-v", "-V", "version", "--help", "-h"})


@cache
def client_binaries() -> frozenset[str]:
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS

    return frozenset(name for spec in CLIENT_SPECS.values() for name in spec.binary_names)


_BASETEMP: list[Path] = []  # pytest's basetemp for this session, set by the fixture below


def safe_roots() -> tuple[Path, ...]:
    """Where a test's own fake client binaries live: pytest's basetemp, the temp dir and this tests tree.

    The conftest redirects ``tempfile.tempdir`` during the run, so ``gettempdir()`` alone does not cover
    ``tmp_path``; the session's basetemp does.
    """
    return (*_BASETEMP, Path(tempfile.gettempdir()).resolve(), _TESTS_DIR)


def _env_path(env: Mapping[object, object] | None) -> str | None:
    if env is None:
        return os.environ.get("PATH")
    value = env.get("PATH", env.get(b"PATH"))
    return value.decode() if isinstance(value, bytes) else (value if isinstance(value, str) else None)


def real_client_binary(argv: Sequence[str], env: Mapping[object, object] | None = None) -> Path | None:
    """The real installed client *argv* would run, or ``None`` when it is not one (or only a probe)."""
    if len(argv) == 1 and " " in argv[0]:  # shell=True: _execute_child gets the command string itself
        argv = shlex.split(argv[0]) or [""]
    if len(argv) >= 3 and Path(argv[0]).name in {"sh", "bash", "zsh"} and argv[1] == "-c":
        argv = shlex.split(argv[2]) or [""]
    program = argv[0] if argv else ""
    if Path(program).name not in client_binaries() or (len(argv) > 1 and set(argv[1:]) <= _PROBE_ARGS):
        return None
    if argv[1:2] == ["mcp"] and argv[2:3] in (["list"], ["get"]):  # read-only config lookups: no model call
        return None
    found = program if os.sep in program else shutil.which(program, path=_env_path(env))
    if found is None:
        return None
    resolved = Path(found).resolve()
    return None if any(resolved.is_relative_to(root) for root in safe_roots()) else resolved


@pytest.fixture(autouse=True)
def _no_real_client_launch(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live") is not None:
        return
    if not _BASETEMP:
        _BASETEMP.append(request.config._tmp_path_factory.getbasetemp().resolve())  # type: ignore[attr-defined]
    real_execute = subprocess.Popen._execute_child  # type: ignore[attr-defined]

    def guarded(self: subprocess.Popen[bytes], args: object, executable: object, *rest: object, **kw: object) -> object:
        argv = [os.fsdecode(a) for a in ([args] if isinstance(args, (str, bytes, os.PathLike)) else args)]  # type: ignore[union-attr]
        if executable is not None:
            argv = [os.fsdecode(executable), *argv[1:]]  # type: ignore[arg-type]
        env = rest[4] if len(rest) > 4 else kw.get("env")
        hit = real_client_binary(argv, env if isinstance(env, Mapping) else None)
        if hit is not None:
            raise AssertionError(
                f"a test tried to launch the real client {hit} ({' '.join(argv)[:200]}); stub the launch, "
                "or mark the test @pytest.mark.live (DISPATCH-TEST-LAUNCH-GUARD)"
            )
        return real_execute(self, args, executable, *rest, **kw)

    monkeypatch.setattr(subprocess.Popen, "_execute_child", guarded)
