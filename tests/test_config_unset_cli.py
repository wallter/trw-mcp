"""``trw-mcp config unset``: removes a key or dotted sub-key through the typed writer; the retired-key warning names it."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.tools._config_cli import run_config

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    for key in tuple(os.environ):
        if key.startswith("TRW_") and key != "TRW_FRAMEWORK_PATH":
            monkeypatch.delenv(key)
    home, project = tmp_path / "home", tmp_path / "project"
    (home / ".trw").mkdir(parents=True)
    (project / ".trw").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    return home, project


def _unset(project: Path, key: str, capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str, str]:
    args = _build_arg_parser().parse_args(["config", "unset", key, "--target-dir", str(project), *extra])
    try:
        run_config(args)
    except SystemExit as exc:
        code = int(exc.code or 0)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_retired_key_is_removed_at_machine_scope_and_everything_else_is_kept(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    cfg = home / ".trw" / "config.yaml"
    cfg.write_text('# my notes\nuser_tier_enabled: true\ndispatch_default_client: "codex"\n', encoding="utf-8")
    code, out, _err = _unset(project, "user_tier_enabled", capsys, "--scope", "machine")
    assert code == 0 and "removed" in out and str(cfg) in out
    assert cfg.read_text(encoding="utf-8") == '# my notes\ndispatch_default_client: "codex"\n'


def test_set_still_refuses_a_retired_key_so_unset_is_the_only_way_out(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    args = _build_arg_parser().parse_args(["config", "set", "user_tier_enabled", "true", "--target-dir", str(project)])
    with pytest.raises(SystemExit) as exc:
        run_config(args)
    assert exc.value.code == 2
    assert "unknown" in capsys.readouterr().err


def test_a_dotted_key_removes_one_entry_and_keeps_siblings(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    cfg = home / ".trw" / "config.yaml"
    cfg.write_text("dispatch_default_efforts:\n  claude: high\n  codex: medium\n", encoding="utf-8")
    code, _out, _err = _unset(project, "dispatch_default_efforts.codex", capsys, "--scope", "machine")
    assert code == 0
    assert cfg.read_text(encoding="utf-8") == "dispatch_default_efforts:\n  claude: high\n"
    assert _unset(project, "dispatch_default_efforts.claude", capsys, "--scope", "machine")[0] == 0
    assert "dispatch_default_efforts" not in cfg.read_text(encoding="utf-8")  # the emptied map goes too


def test_an_absent_key_is_reported_and_the_file_is_untouched(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    cfg = home / ".trw" / "config.yaml"
    cfg.write_text("debug: true\n", encoding="utf-8")
    before, stamp = _sha(cfg), cfg.stat().st_mtime_ns
    code, out, _err = _unset(project, "user_tier_enabled", capsys, "--scope", "machine")
    assert code == 0 and "already absent" in out
    assert (_sha(cfg), cfg.stat().st_mtime_ns) == (before, stamp)


def test_an_absent_file_is_absent_and_is_not_created(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    home, project = env
    code, out, _err = _unset(project, "user_tier_enabled", capsys, "--scope", "machine")
    assert code == 0 and "already absent" in out and not (home / ".trw" / "config.yaml").exists()


def test_quotes_flow_maps_and_layout_survive_an_unrelated_removal(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    seed = "dispatch_default_client: \"codex\"\nproject_name: 'it''s'\nstale_knob: 1\ndispatch_default_models: {codex: gpt-x, claude: \"opus\"}\nbeta:\n    - x\n"
    cfg.write_text(seed, encoding="utf-8")
    assert _unset(project, "stale_knob", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == seed.replace("stale_knob: 1\n", "")


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("platform_api_key", "secret"),
        ("backend_api_key", "secret"),
        ("debug.x", "not a mapping"),
        ("dispatch_default_efforts.", "empty"),
    ],
)
def test_refusals_exit_2_with_one_line_and_no_write(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], key: str, reason: str
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("debug: true\n", encoding="utf-8")
    before = _sha(cfg)
    code, out, err = _unset(project, key, capsys)
    assert code == 2 and out == "" and len(err.strip().splitlines()) == 1 and reason in err
    assert _sha(cfg) == before


def test_a_symlinked_config_is_refused(
    env: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    outside = tmp_path / "outside.yaml"
    outside.write_text("stale_knob: 1\n", encoding="utf-8")
    (project / ".trw" / "config.yaml").symlink_to(outside)
    code, _out, err = _unset(project, "stale_knob", capsys)
    assert code == 2 and "symlink" in err and outside.read_text(encoding="utf-8") == "stale_knob: 1\n"


def test_removing_a_companion_that_breaks_a_cross_field_pair_is_refused(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _home, project = env
    cfg = project / ".trw" / "config.yaml"
    cfg.write_text("comms_body_max_bytes: 20000\ncomms_response_max_bytes: 262144\n", encoding="utf-8")
    before = _sha(cfg)
    code, _out, err = _unset(project, "comms_response_max_bytes", capsys)
    assert code == 2 and "comms" in err and _sha(cfg) == before


def test_reviewer_role_is_refused(env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.server._cli_replacements import enforce_state_changing_guard

    _home, project = env
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    args = _build_arg_parser().parse_args(["config", "unset", "stale", "--target-dir", str(project)])
    with pytest.raises(SystemExit) as exc:
        enforce_state_changing_guard("config", args)
    assert exc.value.code != 0


# -- the post-render invariant: an edit changes ONLY its target ------------------------------------------------------


def _cfg(env: tuple[Path, Path], text: str) -> Path:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_text(text, encoding="utf-8")
    return cfg


def test_an_aliased_sibling_is_not_removed_with_the_anchor(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "old: &models {codex: gpt-x, claude: opus}\ndispatch_default_models: *models\n")
    before = _sha(cfg)
    code, out, err = _unset(env[1], "old.codex", capsys)
    assert code == 2 and out == "" and "other keys" in err and len(err.strip().splitlines()) == 1
    assert _sha(cfg) == before


def test_a_key_inherited_through_a_merge_key_is_refused_not_reported_removed(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "old: &old {user_tier_enabled: true}\n<<: *old\nx: 1\n")
    before = _sha(cfg)
    code, out, err = _unset(env[1], "user_tier_enabled", capsys)
    assert code == 2 and out == "" and "merge" in err
    assert _sha(cfg) == before


def test_removing_an_explicit_override_that_would_expose_the_merged_value_is_refused(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "base: &b {debug: false}\nstale_knob: {debug: true, <<: *b}\n")
    before = _sha(cfg)
    code, _out, err = _unset(env[1], "stale_knob.debug", capsys)
    assert code == 2 and "merge" in err and _sha(cfg) == before


def test_an_unrelated_aliased_file_still_edits_cleanly(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "old: &models {codex: gpt-x}\ndispatch_default_models: *models\nstale_knob: 1\n")
    assert _unset(env[1], "stale_knob", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "old: &models {codex: gpt-x}\ndispatch_default_models: *models\n"


# -- comments belong to the key AFTER them: removing a key keeps its following comment ---------------------------------


def test_removing_the_first_key_keeps_the_comment_that_describes_the_next_key(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "user_tier_enabled: true\n# This describes debug\ndebug: true\n")
    assert _unset(env[1], "user_tier_enabled", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "# This describes debug\ndebug: true\n"


def test_removing_a_middle_key_keeps_the_next_keys_comment_and_drops_its_own_line(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "a: 1\n# about stale\nstale_knob: 2  # inline\n# about debug\ndebug: true\n")
    assert _unset(env[1], "stale_knob", capsys)[0] == 0
    # byte-preserving surgery: only the key's own line goes; the comment ABOVE it is untouched
    assert cfg.read_text(encoding="utf-8") == "a: 1\n# about stale\n# about debug\ndebug: true\n"


def test_a_header_comment_stays_above_the_comment_moved_to_the_map_start(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "# header\nuser_tier_enabled: true\n# about debug\ndebug: true\n")
    assert _unset(env[1], "user_tier_enabled", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "# header\n# about debug\ndebug: true\n"


def test_removing_the_last_key_keeps_header_and_footer_comments(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "# header\nuser_tier_enabled: true\n# footer\n")
    assert _unset(env[1], "user_tier_enabled", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "# header\n# footer\n"


def test_removing_the_only_key_of_an_uncommented_file_leaves_an_empty_file(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = _cfg(env, "user_tier_enabled: true\n")
    assert _unset(env[1], "user_tier_enabled", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("rendered", "removing", "wanted", "expect_ok"),
    [
        ("m: {b: 2}\n", True, None, True),  # only the target went
        ("m: {a: 1, b: 2}\n", True, None, False),  # target still there
        ("m: {b: 3}\n", True, None, False),  # a sibling changed with it
        ("m: {a: 9, b: 2}\n", False, 9, True),  # set reached its value, siblings intact
        ("m: {a: 9, b: 3}\n", False, 9, False),  # set also changed a sibling
        ("m: {b: 2}\nz: 1\n", True, None, False),  # only an unrelated top-level key differs
    ],
)
def test_the_post_render_invariant_checks_the_target_and_every_sibling(
    rendered: str, removing: bool, wanted: object, expect_ok: bool
) -> None:
    from trw_mcp.tools._config_edit_guard import check_only_target_changed

    problem = check_only_target_changed(rendered, "m: {a: 1, b: 2}\n", "m", "a", wanted, removing=removing)
    assert (problem == "") is expect_ok, problem


# -- byte-preserving removal in block style ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "key", "before", "after"),
    [
        ("scalar", "stale", "a: 1\nstale: 2\ndebug: true\n", "a: 1\ndebug: true\n"),
        ("block map", "old", "old:\n  x: 1\n  y: 2\n# about debug\ndebug: true\n", "# about debug\ndebug: true\n"),
        ("list at key indent", "old", "old:\n- a\n- b\n# about debug\ndebug: true\n", "# about debug\ndebug: true\n"),
        (
            "indented list",
            "old",
            "old:\n  - a\n  - b\n\n# about debug\ndebug: true\n",
            "\n# about debug\ndebug: true\n",
        ),
        (
            "block scalar with a # line",
            "old",
            "old: |\n  text\n  # not a comment\n  more\n# about debug\ndebug: true\n",
            "# about debug\ndebug: true\n",
        ),
        (
            "first key",
            "user_tier_enabled",
            "user_tier_enabled: true\n# This describes debug\ndebug: true\n",
            "# This describes debug\ndebug: true\n",
        ),
        ("last key", "stale", "debug: true\nstale: 1\n", "debug: true\n"),
        ("last key keeps footer", "stale", "debug: true\nstale: 1\n# footer\n", "debug: true\n# footer\n"),
        ("header and footer", "old", "# header\nold: {}\n# footer\n", "# header\n# footer\n"),
        ("only key, no comments", "old", "old: 1\n", ""),
        (
            "misplacement",
            "stale",
            "a:\n  b: 1\nstale: 2\n# about debug\ndebug: true\n",
            "a:\n  b: 1\n# about debug\ndebug: true\n",
        ),
        ("inline comment goes with its key", "stale", "a: 1\nstale: 2  # why\ndebug: true\n", "a: 1\ndebug: true\n"),
        (
            "blank lines and indentation elsewhere untouched",
            "stale",
            "a:\n    - x\n\nstale: 2\n\n\ndebug:   true\n",
            "a:\n    - x\n\n\n\ndebug:   true\n",
        ),
        ("CRLF", "stale", "a: 1\r\nstale: 2\r\ndebug: true\r\n", "a: 1\r\ndebug: true\r\n"),
    ],
)
def test_block_style_removal_is_byte_preserving(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], label: str, key: str, before: str, after: str
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_bytes(before.encode())
    assert _unset(env[1], key, capsys)[0] == 0, label
    assert cfg.read_bytes() == after.encode(), label


def test_a_block_sub_key_is_removed_by_line_and_a_nested_last_key_stops_at_the_next_top_level_comment(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_text(
        "dispatch_default_efforts:\n  claude: high\n  codex: medium\n# about debug\ndebug: true\n", encoding="utf-8"
    )
    assert _unset(env[1], "dispatch_default_efforts.codex", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "dispatch_default_efforts:\n  claude: high\n# about debug\ndebug: true\n"
    assert _unset(env[1], "dispatch_default_efforts.claude", capsys)[0] == 0  # the emptied map goes with its last entry
    assert cfg.read_text(encoding="utf-8") == "# about debug\ndebug: true\n"


def test_flow_style_containers_fall_back_to_the_round_trip(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_text("dispatch_default_models: {codex: gpt-x, claude: opus}\ndebug: true\n", encoding="utf-8")
    assert _unset(env[1], "dispatch_default_models.codex", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "dispatch_default_models: {claude: opus}\ndebug: true\n"


def test_an_unrelated_edit_survives_a_tagged_scalar(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_text("project_namespace: !!str 123\nstale: 1\n", encoding="utf-8")
    assert _unset(env[1], "stale", capsys)[0] == 0
    assert cfg.read_text(encoding="utf-8") == "project_namespace: !!str 123\n"


# -- round 3 ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "before", "after"),
    [
        ("document-end marker and footer", "debug: true\nstale: 1\n# footer\n...\n", "debug: true\n# footer\n...\n"),
        ("leading document-start marker", "---\nstale: 1\n# footer\n", "---\n# footer\n"),
        ("footer at EOF without a marker", "debug: true\nstale: 1\n# footer\n", "debug: true\n# footer\n"),
        ("BOM on the first key's line", "﻿stale: 1\n# about debug\ndebug: true\n", "﻿# about debug\ndebug: true\n"),
        ("BOM with the only key", "﻿stale: 1\n", "﻿"),
    ],
)
def test_document_markers_footers_and_the_bom_survive_a_removal(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], label: str, before: str, after: str
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_bytes(before.encode("utf-8"))
    assert _unset(env[1], "stale", capsys)[0] == 0, label
    assert cfg.read_bytes() == after.encode("utf-8"), label


@pytest.mark.parametrize(
    ("label", "before", "after"),
    [
        ("numeric sibling", "old: 1\n123: foo\ndebug: true\n", "123: foo\ndebug: true\n"),
        ("numeric sibling after the last key", "debug: true\nold: 1\n123: foo\n", "debug: true\n123: foo\n"),
    ],
)
def test_a_non_string_sibling_key_falls_back_instead_of_crashing(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], label: str, before: str, after: str
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    cfg.write_text(before, encoding="utf-8")
    code, _out, err = _unset(env[1], "old", capsys)
    assert code == 0 and "TypeError" not in err, (label, err)
    assert cfg.read_text(encoding="utf-8") == after


def test_a_complex_sibling_key_never_crashes_and_never_corrupts(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    seed = "old: 1\n? [a, b]\n: v\ndebug: true\n"
    cfg.write_text(seed, encoding="utf-8")
    code, _out, err = _unset(env[1], "old", capsys)
    assert "Traceback" not in err and "TypeError" not in err
    assert code in (0, 2)
    if code == 2:
        assert cfg.read_text(encoding="utf-8") == seed  # refused by the guard: nothing written
    else:
        assert "old:" not in cfg.read_text(encoding="utf-8") and "debug: true" in cfg.read_text(encoding="utf-8")


def test_a_recursive_alias_gives_a_controlled_refusal_or_a_clean_edit_never_a_crash(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    cfg = env[1] / ".trw" / "config.yaml"
    seed = "old: &a [*a]\ndebug: true\n"
    cfg.write_text(seed, encoding="utf-8")
    for key in ("old", "debug"):
        code, out, err = _unset(env[1], key, capsys)
        assert "RecursionError" not in err and "Traceback" not in err, key
        assert code in (0, 2), key
        if code == 2:
            assert len(err.strip().splitlines()) == 1 and out == ""
    # an unrelated edit next to it
    code, _out, err = _unset(env[1], "nothing_here", capsys)
    assert "RecursionError" not in err and code in (0, 2)
