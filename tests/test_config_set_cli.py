"""PRD-INFRA-210 FR04/FR06/NFR03: ``trw-mcp config set`` through the real argparse tree and config cascade."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.tools._config_cli import PICKUP_LINE, run_config

pytestmark = pytest.mark.unit

SEED = "# operator notes\nalpha: 1\n\n# keep me\nbeta:\n  - x\n  - y\ndebug: false\n"


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """(home, project): HOME and the project live under tmp_path; no TRW_* variable leaks in."""
    for key in tuple(os.environ):
        if key.startswith("TRW_") and key not in {"TRW_FRAMEWORK_PATH"}:
            monkeypatch.delenv(key)
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".trw").mkdir(parents=True)
    (project / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    return home, project


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    args = _build_arg_parser().parse_args(["config", *argv])
    try:
        run_config(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    else:  # pragma: no cover - run_config always exits
        code = -1
    out = capsys.readouterr()
    return code, out.out, out.err


def _set(project: Path, key: str, value: str, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str, str]:
    return _run(["set", key, value, "--target-dir", str(project), *extra], capsys)


def test_project_scope_changes_one_key_and_keeps_comments_and_order(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    code, out, _err = _set(project, "debug", "true", capsys)
    assert code == 0
    assert cfg.read_text(encoding="utf-8") == SEED.replace("debug: false", "debug: true")
    assert str(cfg) in out


def test_machine_scope_creates_the_file_at_0644(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    home, project = env
    code, _out, _err = _set(project, "dispatch_tools_exposed", "true", capsys, "--scope", "machine")
    assert code == 0
    cfg = home / ".trw" / "config.yaml"
    assert cfg.read_text(encoding="utf-8") == "dispatch_tools_exposed: true\n"
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o644 & ~_umask()
    assert not (project / ".trw" / "config.yaml").exists()


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def test_dotted_key_sets_one_map_entry_and_keeps_siblings(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    cfg = home / ".trw" / "config.yaml"
    cfg.write_text("dispatch_default_efforts:\n  claude: high\n", encoding="utf-8")
    code, _out, _err = _set(project, "dispatch_default_efforts.codex", "medium", capsys, "--scope", "machine")
    assert code == 0
    assert cfg.read_text(encoding="utf-8") == "dispatch_default_efforts:\n  claude: high\n  codex: medium\n"


def test_flow_mapping_value_is_accepted(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _home, project = env
    code, _out, _err = _set(project, "dispatch_default_efforts", "{codex: medium}", capsys)
    assert code == 0
    assert "codex: medium" in (project / ".trw" / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("no_such_field", "1", "unknown"),
        ("platform_api_key", "sekret", "secret"),
        ("backend_api_key", "sekret", "secret"),
        ("dispatch_default_effort", "extreme", "dispatch_default_effort"),
        ("dispatch_default_efforts.codexx", "low", "dispatch_default_efforts"),
        ("dispatch_default_efforts.codex", "extreme", "dispatch_default_efforts"),
        ("dispatch_default_effort.codex", "low", "not a mapping"),
        ("debug", "", "empty"),
    ],
)
def test_refusals_exit_2_with_one_stderr_line_and_no_write(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], key: str, value: str, reason: str
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    before = _sha(cfg)
    code, out, err = _set(project, key, value, capsys)
    assert code == 2
    assert out == ""
    assert len(err.strip().splitlines()) == 1 and reason in err
    assert _sha(cfg) == before


def test_a_secret_value_is_never_echoed(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _home, project = env
    _code, out, err = _set(project, "platform_api_key", "sk-very-secret", capsys)
    assert "sk-very-secret" not in out + err


def test_non_mapping_file_is_refused_untouched(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("- a\n- b\n", encoding="utf-8")
    before = _sha(cfg)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert (code, _sha(cfg)) == (2, before)
    assert "mapping" in err


def test_symlinked_config_is_refused_and_target_untouched(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    outside = tmp_path / "outside.yaml"
    outside.write_text("alpha: 1\n", encoding="utf-8")
    (project / ".trw" / "config.yaml").symlink_to(outside)
    before = _sha(outside)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 2 and "symlink" in err
    assert _sha(outside) == before


def test_symlinked_trw_dir_is_refused(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    real = tmp_path / "real_trw"
    real.mkdir()
    (project / ".trw").rmdir()
    (project / ".trw").symlink_to(real)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 2 and "symlink" in err
    assert not (real / "config.yaml").exists()


def test_project_scope_without_trw_dir_is_refused(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bare = tmp_path / "bare"
    bare.mkdir()
    code, _out, err = _set(bare, "debug", "true", capsys)
    assert code == 2 and ".trw" in err
    assert not (bare / ".trw").exists()


def test_repeat_set_says_unchanged_and_does_not_rewrite(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    assert _set(project, "debug", "true", capsys)[0] == 0
    stamp = cfg.stat().st_mtime_ns
    code, out, _err = _set(project, "debug", "true", capsys)
    assert code == 0 and "unchanged" in out
    assert cfg.stat().st_mtime_ns == stamp


def test_comment_only_file_keeps_its_comments(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("# only a note\n", encoding="utf-8")
    assert _set(project, "debug", "true", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "# only a note\ndebug: true\n"


def test_report_has_effective_value_and_the_pickup_line(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    code, out, _err = _set(project, "dispatch_default_effort", "high", capsys)
    assert code == 0
    assert "effective: high" in out
    assert PICKUP_LINE in out
    assert "next TRW tool call" in PICKUP_LINE and "/mcp" in PICKUP_LINE


def test_env_shadow_is_reported_with_the_winning_value(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, project = env
    monkeypatch.setenv("TRW_DISPATCH_DEFAULT_EFFORT", "low")
    code, out, _err = _set(project, "dispatch_default_effort", "high", capsys)
    assert code == 0
    assert "TRW_DISPATCH_DEFAULT_EFFORT" in out and "effective: low" in out


def test_machine_write_warns_when_the_project_layer_overrides(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    (project / ".trw" / "config.yaml").write_text("dispatch_default_effort: low\n", encoding="utf-8")
    code, out, _err = _set(project, "dispatch_default_effort", "high", capsys, "--scope", "machine")
    assert code == 0
    assert "project" in out and "effective: low" in out


def test_connected_client_refresh_sees_the_write_without_reload(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The pickup line's claim: the surface-authority refresh path resolves the new value on its own."""
    from trw_mcp.models.config import get_config, reload_config
    from trw_mcp.models.config._loader import refresh_config_if_changed, set_config_override

    _home, project = env
    (project / ".trw" / "config.yaml").write_text("tool_resolution_mode: standard\n", encoding="utf-8")
    reload_config()
    try:
        assert get_config().dispatch_tools_exposed is False
        assert _set(project, "dispatch_tools_exposed", "true", capsys, "--scope", "project")[0] == 0
        assert refresh_config_if_changed() is True
        assert get_config().dispatch_tools_exposed is True
    finally:
        set_config_override(None)
        reload_config()


def test_reviewer_role_is_refused_and_writes_nothing(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server._cli_replacements import enforce_state_changing_guard
    from trw_mcp.server._cli_reviewer_policy import classified_command_paths

    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    assert not {"config set", "config dispatch"} & classified_command_paths()
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    args = _build_arg_parser().parse_args(["config", "set", "debug", "true", "--target-dir", str(project)])
    with pytest.raises(SystemExit) as exc:
        enforce_state_changing_guard("config", args)
    assert exc.value.code != 0
    assert cfg.read_text(encoding="utf-8") == SEED


def test_field_constraint_is_enforced_even_when_an_env_var_shadows_the_key(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """dispatch_max_concurrent_children is ge=0, le=64: 99 is refused, with or without a shadowing TRW_ variable."""
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    before = _sha(cfg)
    monkeypatch.setenv("TRW_DISPATCH_MAX_CONCURRENT_CHILDREN", "3")
    for value in ("99", "-1"):
        code, _out, err = _set(project, "dispatch_max_concurrent_children", value, capsys)
        assert code == 2 and "dispatch_max_concurrent_children" in err
    assert _sha(cfg) == before
    assert _set(project, "dispatch_max_concurrent_children", "8", capsys)[0] == 0


def test_concurrent_writers_do_not_lose_each_others_keys(
    env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    from trw_mcp.tools import _config_writer
    from trw_mcp.tools._config_cli import set_config_value

    _home, project = env
    (project / ".trw" / "config.yaml").write_text(SEED, encoding="utf-8")
    real_validate = _config_writer._validate

    def slow_validate(*a: object, **k: object) -> None:  # widen the read-to-write window
        time.sleep(0.05)
        real_validate(*a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(_config_writer, "_validate", slow_validate)
    keys = {"dispatch_default_efforts.codex": "medium", "dispatch_default_efforts.claude": "high", "debug": "true"}
    errors: list[BaseException] = []

    def work(key: str, value: str) -> None:
        try:
            set_config_value(key, value, scope="project", target_dir=project)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=work, args=kv) for kv in keys.items()]
    [t.start() for t in threads]
    [t.join() for t in threads]
    text = (project / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert not errors
    assert "codex: medium" in text and "claude: high" in text and "debug: true" in text and "# keep me" in text


def test_machine_value_shadowed_by_the_project_layer_is_still_validated(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    (project / ".trw" / "config.yaml").write_text("dispatch_default_effort: low\n", encoding="utf-8")
    machine = home / ".trw" / "config.yaml"
    code, _out, err = _set(project, "dispatch_default_effort", "extreme", capsys, "--scope", "machine")
    assert code == 2 and "dispatch_default_effort" in err
    assert not machine.exists()


def test_machine_dotted_key_shadowed_by_the_project_layer_is_still_validated(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    (project / ".trw" / "config.yaml").write_text("dispatch_default_efforts:\n  codex: low\n", encoding="utf-8")
    code, _out, err = _set(project, "dispatch_default_efforts.codex", "extreme", capsys, "--scope", "machine")
    assert code == 2 and "dispatch_default_efforts" in err
    assert not (home / ".trw" / "config.yaml").exists()


def test_unrelated_quoted_values_and_flow_maps_keep_their_bytes(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    seed = "dispatch_default_client: \"codex\"\nproject_name: 'it''s'\ndispatch_default_models: {codex: gpt-x, claude: \"opus\"}\ndebug: false\n"
    cfg.write_text(seed, encoding="utf-8")
    assert _set(project, "debug", "true", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == seed.replace("debug: false", "debug: true")


def test_a_valid_layered_combination_is_accepted_with_an_inherited_companion_field(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """comms_body_max_bytes=20000 needs comms_response_max_bytes >= 6*body+4096; the machine layer supplies it."""
    home, project = env
    (home / ".trw" / "config.yaml").write_text("comms_response_max_bytes: 262144\n", encoding="utf-8")
    code, _out, err = _set(project, "comms_body_max_bytes", "20000", capsys)
    assert code == 0, err
    assert "comms_body_max_bytes: 20000" in (project / ".trw" / "config.yaml").read_text(encoding="utf-8")
    # the reverse direction: the project holds the companion, the machine layer is written
    (project / ".trw" / "config.yaml").write_text("comms_body_max_bytes: 20000\n", encoding="utf-8")
    (home / ".trw" / "config.yaml").unlink()
    assert _set(project, "comms_response_max_bytes", "262144", capsys, "--scope", "machine")[0] == 0


def test_an_unrelated_key_is_not_blocked_by_a_valid_cross_field_pair(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    (project / ".trw" / "config.yaml").write_text(
        "comms_body_max_bytes: 20000\ncomms_response_max_bytes: 262144\n", encoding="utf-8"
    )
    assert _set(project, "debug", "true", capsys)[0] == 0


def test_a_cross_field_violation_in_the_merged_config_is_still_refused(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    code, _out, err = _set(project, "comms_body_max_bytes", "20000", capsys)  # response stays at the default
    assert code == 2 and "comms" in err and not cfg.exists()


@pytest.mark.parametrize("value", ["{123: foo}", "{true: foo}"])
def test_non_string_map_keys_are_refused_and_nothing_is_written(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], value: str
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    code, _out, err = _set(project, "dispatch_default_models", value, capsys)
    assert code == 2 and "key" in err and not cfg.exists()


def test_what_is_written_loads_through_the_production_loader(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import _read_yaml_overrides

    _home, project = env
    assert _set(project, "dispatch_default_models", "{codex: foo}", capsys)[0] == 0
    layer = _read_yaml_overrides(project / ".trw" / "config.yaml")
    assert TRWConfig(_env_file=None, **layer).dispatch_default_models == {"codex": "foo"}  # type: ignore[arg-type]


# -- validation builds the config exactly as the production loader does ------------------------------------------------


def _strict_production_load(project: Path) -> None:
    """The loader's own cascade, then TRWConfig, with failures raised (the loader itself would fall back)."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import resolve_config_overrides

    TRWConfig(**resolve_config_overrides(project / ".trw" / "config.yaml"))  # type: ignore[arg-type]


def test_machine_value_that_is_invalid_alone_is_refused_even_when_the_project_overrides_it(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    (project / ".trw" / "config.yaml").write_text("comms_body_max_bytes: 8192\n", encoding="utf-8")
    _strict_production_load(project)
    code, _out, err = _set(project, "comms_body_max_bytes", "65536", capsys, "--scope", "machine")
    assert code == 2 and "comms" in err
    assert not (home / ".trw" / "config.yaml").exists()
    _strict_production_load(project)


def test_an_env_shadowed_bad_value_does_not_block_a_valid_unrelated_write_and_the_loader_round_trips(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("debug: notbool\n", encoding="utf-8")
    monkeypatch.setenv("TRW_DEBUG", "true")
    _strict_production_load(project)  # production tolerates it: the env value shadows the file
    code, _out, err = _set(project, "comms_body_max_bytes", "8192", capsys)
    assert code == 0, err
    assert "debug: notbool" in cfg.read_text(encoding="utf-8")
    _strict_production_load(project)


def test_the_cross_field_check_still_runs_beside_an_env_shadowed_bad_value(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("debug: notbool\n", encoding="utf-8")
    monkeypatch.setenv("TRW_DEBUG", "true")
    before = cfg.read_bytes()
    code, _out, err = _set(project, "comms_body_max_bytes", "20000", capsys)  # production would refuse this load
    assert code == 2 and "comms" in err
    assert cfg.read_bytes() == before
    _strict_production_load(project)


def test_a_non_string_top_level_key_is_tolerated_and_preserved(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("123: foo\ndebug: false\n", encoding="utf-8")
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 0 and "TypeError" not in err
    assert cfg.read_text(encoding="utf-8") == "123: foo\ndebug: true\n"


# -- TOCTOU: the layer is read through a no-follow descriptor and its identity is rechecked before the write -----------

_PRIVATE = "platform_api_key: PRIVATE-CREDENTIAL-VALUE\nbackend_api_key: PRIVATE-BACKEND-VALUE\n"


def _private_file(tmp_path: Path) -> Path:
    secret = tmp_path / "credentials.yaml"
    secret.write_text(_PRIVATE, encoding="utf-8")
    return secret


def _nothing_leaked(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert "PRIVATE-" not in path.read_text(encoding="utf-8", errors="replace"), path


def test_a_symlink_planted_before_the_read_is_refused_and_nothing_leaks(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    secret = _private_file(tmp_path)
    (project / ".trw" / "config.yaml").symlink_to(secret)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 2 and "symlink" in err
    assert secret.read_text(encoding="utf-8") == _PRIVATE
    _nothing_leaked(project)
    _nothing_leaked(home)


def test_a_swap_between_the_check_and_the_read_is_refused_and_the_secret_never_reaches_the_written_bytes(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _config_writer

    home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    secret = _private_file(tmp_path)
    real = _config_writer._layer_path

    def check_then_swap(scope: str, target_dir: Path, home_dir: Path) -> tuple[Path, Path]:
        result = real(scope, target_dir, home_dir)  # the symlink check passes on the regular file...
        cfg.unlink()
        cfg.symlink_to(secret)  # ...then the file is swapped for a link to the private file
        return result

    real_validate = _config_writer._validate

    def restore_before_write(*a: object, **k: object) -> None:
        real_validate(*a, **k)  # type: ignore[arg-type]
        cfg.unlink()  # the attacker puts a regular file back before the publish
        cfg.write_text(SEED, encoding="utf-8")

    monkeypatch.setattr(_config_writer, "_layer_path", check_then_swap)
    monkeypatch.setattr(_config_writer, "_validate", restore_before_write)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 2 and "symlink" in err
    assert secret.read_text(encoding="utf-8") == _PRIVATE
    _nothing_leaked(project)
    _nothing_leaked(home)


def test_a_swap_that_is_restored_before_the_write_is_refused_by_the_identity_check(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _config_writer

    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    real_validate = _config_writer._validate

    pin = tmp_path / "pinned-original"

    def swap_after_read(*a: object, **k: object) -> None:
        real_validate(*a, **k)  # type: ignore[arg-type]
        # Hold the original inode with a hard link so the replacement cannot reuse its number
        # (Linux reuses a freed inode at once); a different regular file then takes the name.
        os.link(cfg, pin)
        cfg.unlink()
        cfg.write_text(SEED, encoding="utf-8")

    monkeypatch.setattr(_config_writer, "_validate", swap_after_read)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 2 and "changed" in err
    assert cfg.read_text(encoding="utf-8") == SEED


def test_an_in_place_edit_after_the_read_is_refused_by_the_identity_check(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools import _config_writer

    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text(SEED, encoding="utf-8")
    real_validate = _config_writer._validate
    edited = SEED + "# a concurrent edit to the same inode\n"

    def edit_after_read(*a: object, **k: object) -> None:
        real_validate(*a, **k)  # type: ignore[arg-type]
        with cfg.open("a", encoding="utf-8") as handle:  # same inode, new size and timestamps
            handle.write("# a concurrent edit to the same inode\n")

    monkeypatch.setattr(_config_writer, "_validate", edit_after_read)
    code, _out, err = _set(project, "debug", "true", capsys)
    assert code == 2 and "changed" in err
    assert cfg.read_text(encoding="utf-8") == edited


def test_a_new_file_is_never_wider_than_the_umask_allows(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    old = os.umask(0o077)
    try:
        assert _set(project, "debug", "true", capsys, "--scope", "machine")[0] == 0
    finally:
        os.umask(old)
    assert stat.S_IMODE((home / ".trw" / "config.yaml").stat().st_mode) == 0o600


def test_a_set_that_would_also_change_an_aliased_sibling_is_refused(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The same post-render invariant guards ``set``: it may change only its target."""
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("old: &m {codex: gpt-x}\ndispatch_default_models: *m\n", encoding="utf-8")
    before = _sha(cfg)
    code, _out, err = _set(project, "dispatch_default_models.codex", "gpt-y", capsys)
    assert code == 2 and "other keys" in err and _sha(cfg) == before


def test_an_unrelated_set_survives_a_tagged_scalar(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("project_namespace: !!str 123\n", encoding="utf-8")
    assert _set(project, "debug", "true", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "project_namespace: !!str 123\ndebug: true\n"


@pytest.mark.parametrize("value", [".nan", ".inf"])
def test_non_finite_floats_can_be_set_and_are_not_mistaken_for_an_alias(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], value: str
) -> None:
    _home, project = env
    code, _out, err = _set(project, "ambiguity_rate_max", value, capsys)
    assert "alias" not in err and "merge" not in err
    if code == 2:  # the field's own bounds may refuse a non-finite value; that must be its reason, not the guard's
        assert "ambiguity_rate_max rejected" in err
    else:
        assert value in (project / ".trw" / "config.yaml").read_text(encoding="utf-8")


def test_setting_a_tagged_value_is_accepted(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    """The requested value is normalised through the same safe load as the reload before they are compared."""
    _home, project = env
    code, _out, err = _set(project, "project_namespace", "!!str 123", capsys)
    assert code == 0, err
    assert (project / ".trw" / "config.yaml").read_text(encoding="utf-8") == "project_namespace: !!str 123\n"


def test_a_recursive_alias_next_to_a_set_is_a_controlled_outcome(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("old: &a [*a]\ndebug: false\n", encoding="utf-8")
    code, _out, err = _set(project, "debug", "true", capsys)
    assert "RecursionError" not in err and "Traceback" not in err and code in (0, 2)
