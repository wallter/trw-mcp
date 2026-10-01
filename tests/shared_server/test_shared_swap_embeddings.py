"""``swap --version`` gives the env venv the embeddings extra, or fails saying what to fetch (SWAP-VENV-EXTRAS).

2026-09-30, first stable hot-swap: the version venv held trw-mcp's base dependencies only. With no
sentence-transformers or torch the new daemon logged ``embedder_unavailable`` and every session's recall
silently fell back to keyword ranking. The swap now installs ``trw-memory[all]`` at the trw-memory version the
venv already has, an install that cannot be done says which package is missing and how to fetch it, and
doctor and ``trw_status`` flag an env that lacks the extra.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server import _autoswap, _cli, _doctor, _embeddings, _ops
from trw_mcp.shared_server._records import SharedPaths, SharedServerError, set_env_python

pytestmark = pytest.mark.integration

_UV_MISSING = (
    "error: No solution found when resolving dependencies\n  cause: Because sentence-transformers was not found "
    "in the cache and trw-memory[all]==5.1.2 depends on sentence-transformers>=2.0.0, we can conclude that "
    "your requirements are unsatisfiable."
)


@pytest.fixture
def paths(tmp_path: Path) -> SharedPaths:
    return SharedPaths.resolve(tmp_path / ".trw", SharedMcpConfig(envs_dir=str(tmp_path / "envs")))


@pytest.fixture
def config(tmp_path: Path) -> SharedMcpConfig:
    house = tmp_path / "wheelhouse"
    house.mkdir()
    (house / "trw_mcp-8.0.0-py3-none-any.whl").touch()
    return SharedMcpConfig(wheelhouse=str(house))


class _Venv:
    """A stub ``_ops._run``: ``uv venv`` makes the dir; probes answer from the flags; installs are recorded."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, *, has_embeddings: bool = False, fail: str | None = None
    ) -> None:
        self.installs: list[list[str]] = []
        self.argvs: list[list[str]] = []
        self.has_embeddings, self.fail = has_embeddings, fail
        monkeypatch.setattr(_ops, "_run", self)

    def __call__(self, argv: list[str], *, env: dict[str, str] | None = None) -> str:
        self.argvs.append(argv)
        if argv[:2] == ["uv", "venv"]:
            (Path(argv[-1]) / "bin").mkdir(parents=True)
            (Path(argv[-1]) / "bin" / "python").touch()
            return ""
        if argv[:3] == ["uv", "pip", "install"]:
            self.installs.append(argv)
            if self.fail and any(a.startswith("trw-memory[") for a in argv):
                raise SharedServerError(f"`{' '.join(argv)}` failed (2): {self.fail}")
            return ""
        code = argv[2]
        if "sentence_transformers" in code:
            if not self.has_embeddings:
                raise SharedServerError("`python -c ...` failed (1): ")
            return ""
        if "trw_memory._version" in code:
            return "5.1.2"
        if "importlib.metadata" in code:  # trw-distill's version: not installed
            raise SharedServerError("no dist")
        return ""  # `import trw_mcp` and friends

    def extras(self) -> list[list[str]]:
        return [i for i in self.installs if any(a.startswith("trw-memory[") for a in i)]


def _old_venv(paths: SharedPaths) -> Path:
    venv = paths.envs_dir / "test" / "venv-8.0.0"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").touch()
    return venv


def test_a_fresh_version_venv_gets_the_embeddings_extra_at_the_installed_memory_version(
    paths: SharedPaths, config: SharedMcpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Venv(monkeypatch)
    python = _ops.build_version_venv(paths, "test", "8.0.0", config)

    (extra,) = run.extras()
    assert "trw-memory[all]==5.1.2" in extra, "the extra is added at the version the venv has, never a new one"
    assert ["--python", str(python)] == extra[extra.index("--python") : extra.index("--python") + 2]
    assert "--offline" in extra and "--find-links" in extra and "--no-index" not in extra
    assert not any(a.startswith("trw-mcp==") for a in extra), "trw-mcp's own install stays its own step"


def test_embeddings_off_builds_the_venv_without_the_extra_or_a_probe_for_it(
    paths: SharedPaths, config: SharedMcpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _Venv(monkeypatch)
    _ops.build_version_venv(paths, "test", "8.0.0", config, embeddings=False)

    assert run.extras() == []
    assert not [a for a in run.argvs if len(a) > 2 and "sentence_transformers" in a[2]]


def test_a_failed_extra_names_the_missing_package_and_how_to_fetch_it_and_removes_the_new_venv(
    paths: SharedPaths, config: SharedMcpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _Venv(monkeypatch, fail=_UV_MISSING)

    with pytest.raises(SharedServerError) as raised:
        _ops.build_version_venv(paths, "test", "8.0.0", config)

    message = str(raised.value)
    assert "sentence-transformers" in message, "what is missing, as the resolver named it"
    assert f"pip download 'trw-memory[all]==5.1.2' -d {config.wheelhouse}" in message
    assert "embeddings_enabled: false" in message, "the deliberate keyword-only way out"
    assert "keyword" in message
    assert not (paths.envs_dir / "test" / "venv-8.0.0").exists(), "a half-built env is never left to be reused"


def test_a_reused_venv_without_embeddings_gets_them_in_place(
    paths: SharedPaths, config: SharedMcpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    run = _Venv(monkeypatch)
    # trw_mcp must import for the venv to be reused at all
    plain = _ops._run
    monkeypatch.setattr(
        _ops, "_run", lambda argv, *, env=None: "" if argv[2:3] == ["import trw_mcp"] else plain(argv, env=env)
    )
    _ops.build_version_venv(paths, "test", "8.0.0", config)

    assert not [a for a in run.argvs if a[:2] == ["uv", "venv"]], "the venv is not rebuilt"
    (extra,) = run.extras()
    assert "trw-memory[all]==5.1.2" in extra


def test_a_reused_venv_that_already_has_embeddings_installs_nothing(
    paths: SharedPaths, config: SharedMcpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _old_venv(paths)
    run = _Venv(monkeypatch, has_embeddings=True)
    plain = _ops._run
    monkeypatch.setattr(
        _ops, "_run", lambda argv, *, env=None: "" if argv[2:3] == ["import trw_mcp"] else plain(argv, env=env)
    )
    _ops.build_version_venv(paths, "test", "8.0.0", config)

    assert run.installs == []


def test_a_reused_venv_whose_extra_cannot_be_installed_is_kept_and_the_swap_refused(
    paths: SharedPaths, config: SharedMcpConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv = _old_venv(paths)
    _Venv(monkeypatch, fail=_UV_MISSING)
    plain = _ops._run
    monkeypatch.setattr(
        _ops, "_run", lambda argv, *, env=None: "" if argv[2:3] == ["import trw_mcp"] else plain(argv, env=env)
    )
    with pytest.raises(SharedServerError, match=r"sentence-transformers.*pip download"):
        _ops.build_version_venv(paths, "test", "8.0.0", config)

    assert (venv / "bin" / "python").exists(), "an existing venv is never deleted"


def test_swap_cli_follows_the_projects_embeddings_setting(
    paths: SharedPaths, config: SharedMcpConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[dict[str, Any]] = []

    def build(_paths: Any, _env: str, _version: str, _config: Any, **kwargs: Any) -> Path:
        built.append(kwargs)
        return Path(sys.executable)

    monkeypatch.setattr(_ops, "build_version_venv", build)
    monkeypatch.setattr(_ops, "swap", lambda *a, **k: "swapped")
    args = argparse.Namespace(
        env="lane", python=None, src=None, swap_version="8.0.0", daemon=False, with_distill=None, expect_version=None
    )
    for wanted in (True, False):
        project = type("C", (), {"shared_mcp": config, "embeddings_enabled": wanted})()
        monkeypatch.setattr(_cli, "_paths", lambda project=project: (paths, project, tmp_path))
        _cli.run_swap(args)

    assert [k["embeddings"] for k in built] == [True, False]


# --------------------------------------------------------------------------- doctor and trw_status


def _env_without_embeddings(paths: SharedPaths, monkeypatch: pytest.MonkeyPatch, *, present: bool) -> None:
    set_env_python(paths, "stable", Path(sys.executable))
    monkeypatch.setattr(_embeddings, "embeddings_present", lambda *_a, **_k: present)
    monkeypatch.setattr(_embeddings, "memory_version", lambda *_a, **_k: "5.1.2")


def test_doctor_warns_with_the_fix_when_an_envs_interpreter_has_no_embeddings(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env_without_embeddings(paths, monkeypatch, present=False)

    status, message = _doctor.doctor_row(paths, SharedMcpConfig(enabled=True))

    assert status == "WARN"
    assert "embeddings unavailable" in message and "stable" in message
    assert f"uv pip install --python {sys.executable} 'trw-memory[all]==5.1.2'" in message
    assert "trw-mcp swap --env stable --daemon" in message


@pytest.mark.parametrize(("present", "wanted"), [(True, True), (False, False)], ids=["present", "switched-off"])
def test_doctor_is_quiet_when_the_extra_is_there_or_embeddings_are_off(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch, present: bool, wanted: bool
) -> None:
    _env_without_embeddings(paths, monkeypatch, present=present)

    status, message = _doctor.doctor_row(paths, SharedMcpConfig(enabled=True), embeddings=wanted)

    assert (status, "embeddings" in message) == ("PASS", False)


def test_doctor_names_an_interpreter_that_is_gone_instead_of_calling_it_embeddingless(
    paths: SharedPaths, tmp_path: Path
) -> None:
    set_env_python(paths, "stable", tmp_path / "venv-gone" / "bin" / "python")

    status, message = _doctor.doctor_row(paths, SharedMcpConfig(enabled=True))

    assert status == "WARN" and "venv-gone" in message and "does not exist" in message
    assert "embeddings unavailable" not in message


def test_trw_status_surface_flags_a_shared_server_without_embeddings_and_stays_silent_otherwise(
    paths: SharedPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: type("C", (), {"embeddings_enabled": True})())
    monkeypatch.setattr(_embeddings, "memory_version", lambda *_a, **_k: "5.1.2")
    _autoswap.activate("stable", paths, {"trw-mcp": "8.1.2"}, lambda: {"enabled": True})
    try:
        monkeypatch.setattr(_embeddings, "find_spec", lambda _name: None)
        flagged = _autoswap.surface_block()
        assert flagged is not None and "embeddings unavailable" in flagged["embeddings"]
        assert "trw-memory[all]==5.1.2" in flagged["embeddings"]

        monkeypatch.setattr(_embeddings, "find_spec", lambda _name: object())
        quiet = _autoswap.surface_block()
        assert quiet is not None and "embeddings" not in quiet
    finally:
        monkeypatch.setattr(_autoswap, "_ACTIVE", None)


def test_repair_commands_quote_paths_that_would_split_in_a_shell(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheel house"
    python = "/envs/my env/bin/python"

    refusal = _embeddings._failure("trw-memory[all]==5.1.2", wheelhouse, "x")
    fix = _embeddings.fix_command("stable", python, "5.1.2")

    assert f"-d '{wheelhouse}'" in refusal
    assert "--python '/envs/my env/bin/python' 'trw-memory[all]==5.1.2'" in fix


def test_the_refusal_names_the_package_uv_said_it_could_not_fetch_in_its_real_offline_wording() -> None:
    """Captured from `uv pip install --offline` against a wheelhouse without the extras' wheels (2026-10-01)."""
    real = (
        "error: No solution found when resolving dependencies\n"
        "  cause: Because torch>=2.13.0 needs to be downloaded from a registry and trw-memory[all]>=5.1.4.dev5 "
        "depends on torch>=2.13.0, we can conclude that trw-memory[all]>=5.1.4.dev5 cannot be used.\n"
        "         And because you require trw-memory[all]==5.1.4.dev5, we can conclude that your requirements are "
        "unsatisfiable.\n\nhint: Packages were unavailable because the network was disabled."
    )

    assert "(missing: torch)" in _embeddings._failure("trw-memory[all]==5.1.4.dev5", Path("/wh"), real)
