"""FB-INSTALL-01: doctor FAILs a hook family that cannot work (a kept old lib, a broken or orphaned hook).

On the second Mac an update kept an edited ``lib-trw.sh`` (588 lines against 2,057 bundled) but replaced
the hooks that source it: about 20 functions were undefined, every hook exited 0, and doctor said PASS.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_BUNDLED = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"


def _deploy(target: Path) -> Path:
    hooks = target / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (target / ".trw").mkdir()
    for src in _BUNDLED.glob("*.sh"):
        shutil.copy2(src, hooks / src.name)
    return hooks


def _row(target: Path) -> tuple[str, str]:
    from trw_mcp.server._doctor_hook_family import hook_family_row

    return hook_family_row(target)


def test_a_coherent_bundled_family_passes(tmp_path: Path) -> None:
    _deploy(tmp_path)

    status, message = _row(tmp_path)

    assert status == "PASS", message


def test_a_kept_lib_missing_functions_its_hooks_call_fails_and_names_them(tmp_path: Path) -> None:
    hooks = _deploy(tmp_path)
    from trw_mcp.server._doctor_hook_family import defined_functions

    lib = hooks / "lib-trw.sh"
    bundled_defs = defined_functions(lib.read_text(encoding="utf-8"))
    # An old lib: keep only the first function definition, as an older release shipped far fewer.
    first = sorted(bundled_defs)[0]
    lib.write_text(f"#!/bin/sh\n{first}() {{ :; }}\n", encoding="utf-8")

    status, message = _row(tmp_path)

    assert status == "FAIL"
    assert "lib-trw.sh" in message and "undefined" in message
    assert any(name in message for name in bundled_defs - {first})


def test_a_name_only_in_a_comment_or_single_quoted_string_is_not_a_call(tmp_path: Path) -> None:
    """Codex KI3: an old lib is only a problem for names the hook really calls."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (tmp_path / ".trw").mkdir()
    from trw_mcp.server._doctor_hook_family import defined_functions

    bundled_lib = (_BUNDLED / "lib-trw.sh").read_text(encoding="utf-8")
    name = sorted(defined_functions(bundled_lib))[0]
    (hooks / "lib-trw.sh").write_text("#!/bin/sh\n", encoding="utf-8")  # an old lib defining nothing
    (hooks / "session-start.sh").write_text(
        f"#!/bin/sh\n. \"$_hook_dir/lib-trw.sh\"\n# {name} used to run here\necho '{name}'\n", encoding="utf-8"
    )

    status, message = _row(tmp_path)

    assert status == "PASS", message


def test_a_called_name_is_still_found_beside_a_comment(tmp_path: Path) -> None:
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (tmp_path / ".trw").mkdir()
    from trw_mcp.server._doctor_hook_family import defined_functions

    name = sorted(defined_functions((_BUNDLED / "lib-trw.sh").read_text(encoding="utf-8")))[0]
    (hooks / "lib-trw.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (hooks / "session-start.sh").write_text(
        f'#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\n{name} "x" # call it\n', encoding="utf-8"
    )

    status, message = _row(tmp_path)

    assert status == "FAIL" and name in message


def test_a_nested_quote_dirname_source_of_a_missing_lib_fails(tmp_path: Path) -> None:
    """Codex KI4: `. "$(dirname "$0")/lib-trw.sh"` must still be seen as sourcing lib-trw.sh."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (tmp_path / ".trw").mkdir()
    (hooks / "session-start.sh").write_text('#!/bin/sh\n. "$(dirname "$0")/lib-trw.sh"\n', encoding="utf-8")

    status, message = _row(tmp_path)

    assert status == "FAIL" and "lib-trw.sh" in message and "missing" in message


def test_a_hook_with_a_syntax_error_fails(tmp_path: Path) -> None:
    hooks = _deploy(tmp_path)
    victim = sorted(p for p in hooks.glob("*.sh") if not p.name.startswith("lib-"))[0]
    victim.write_text(victim.read_text(encoding="utf-8") + "\nif then fi (\n", encoding="utf-8")

    status, message = _row(tmp_path)

    assert status == "FAIL"
    assert victim.name in message and "syntax" in message


def test_a_hook_whose_lib_is_missing_fails(tmp_path: Path) -> None:
    hooks = _deploy(tmp_path)
    (hooks / "lib-trw.sh").unlink()

    status, message = _row(tmp_path)

    assert status == "FAIL"
    assert "lib-trw.sh" in message and "missing" in message


def test_no_claude_hooks_dir_skips(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()

    status, _message = _row(tmp_path)

    assert status == "SKIP"


def test_the_row_never_runs_a_deployed_hook(tmp_path: Path) -> None:
    """Doctor parses and compares; a checkout-controlled hook must never execute (PRD-FIX-156 boundary)."""
    hooks = _deploy(tmp_path)
    canary = tmp_path / "executed"
    lib = hooks / "lib-trw.sh"
    lib.write_text(lib.read_text(encoding="utf-8") + f"\ntouch '{canary}'\n", encoding="utf-8")

    _row(tmp_path)

    assert not canary.exists()


def test_the_row_is_registered() -> None:
    from trw_mcp.server._doctor_checks_registry import CHECKS

    assert ("hook_family", "_check_hook_family") in CHECKS


_HAD = {"lib-a.sh": {"old_helper"}, "lib-b.sh": set()}
_HAVE = {"lib-a.sh": set(), "lib-b.sh": set()}


@pytest.mark.parametrize(
    "call",
    ["time old_helper\n", "VALUE=1 old_helper\n", "command old_helper\n", "! old_helper\n", 'x="$(old_helper)"\n'],
)
def test_a_prefixed_or_substituted_call_is_still_a_call(call: str) -> None:
    """Codex KI2-r2: command-position matching missed `time f`, `X=1 f` and friends (the r1 parser caught them)."""
    from trw_mcp.server._doctor_hook_family import calls_missing

    text = "#!/bin/sh\n. ./lib-a.sh\n" + call
    assert calls_missing(text, ["lib-a.sh"], have=_HAVE, had=_HAD) == {"old_helper"}


@pytest.mark.parametrize(
    "fake",
    ["# if old_helper() { :; }\n", 'echo "if old_helper() { :; }"\n', "echo 'old_helper() { :; }'\n"],
)
def test_a_definition_in_a_comment_or_string_defines_nothing(fake: str) -> None:
    from trw_mcp.server._doctor_hook_family import calls_missing

    text = "#!/bin/sh\n. ./lib-a.sh\n" + fake + "old_helper\n"
    assert calls_missing(text, ["lib-a.sh"], have=_HAVE, had=_HAD) == {"old_helper"}


def test_a_lib_sourced_after_the_call_does_not_define_it_in_time() -> None:
    from trw_mcp.server._doctor_hook_family import calls_missing

    have = {"lib-a.sh": set(), "lib-b.sh": {"old_helper"}}
    text = "#!/bin/sh\n. ./lib-a.sh\nold_helper\n. ./lib-b.sh\n"
    assert calls_missing(text, ["lib-a.sh", "lib-b.sh"], have=have, had=_HAD) == {"old_helper"}


def test_a_lib_sourced_before_the_call_defines_it() -> None:
    from trw_mcp.server._doctor_hook_family import calls_missing

    have = {"lib-a.sh": set(), "lib-b.sh": {"old_helper"}}
    text = "#!/bin/sh\n. ./lib-a.sh\n. ./lib-b.sh\nold_helper\n"
    assert calls_missing(text, ["lib-a.sh", "lib-b.sh"], have=have, had=_HAD) == set()


@pytest.mark.parametrize(
    "before",
    [
        "cat <<EOF\nit's fine\nEOF\n",  # a heredoc body is data, apostrophe and all
        "cat <<'EOF'\nit's\nEOF\n",
        "cat <<-EOF\n\tit's\n\tEOF\n",
        "z=$'can\\'t'\n",  # ANSI-C quoting escapes its own quote
    ],
)
def test_literal_text_never_hides_a_later_call(before: str) -> None:
    """Codex KI2-r3 KI1 + W1 probe: a quote inside a heredoc or $'..' blanked every later line."""
    from trw_mcp.server._doctor_hook_family import calls_missing

    text = "#!/bin/sh\n. ./lib-a.sh\n" + before + "old_helper\n"
    assert calls_missing(text, ["lib-a.sh"], have=_HAVE, had=_HAD) == {"old_helper"}


def test_a_call_in_a_case_arm_is_a_call() -> None:
    from trw_mcp.server._doctor_hook_family import calls_missing

    text = "#!/bin/sh\n. ./lib-a.sh\ncase $x in\n  a) old_helper ;;\nesac\n"
    assert calls_missing(text, ["lib-a.sh"], have=_HAVE, had=_HAD) == {"old_helper"}


def test_a_parameter_expansion_pattern_never_erases_a_definition() -> None:
    """Codex KI2-r3 KI3: `${y# #}` started a comment that ate the definition after it."""
    from trw_mcp.server._doctor_hook_family import defined_functions

    assert "old_helper" in defined_functions("x=${y# #}; old_helper() { :; }\n")


def test_a_source_after_an_attached_separator_is_recorded() -> None:
    """Codex KI2-r3 KI5: `:;. ./lib-b.sh` was dropped, so the lib's definitions never counted."""
    from trw_mcp.server._doctor_hook_family import calls_missing

    have = {"lib-a.sh": set(), "lib-b.sh": {"old_helper"}}
    text = "#!/bin/sh\n. ./lib-a.sh\n:;. ./lib-b.sh\nold_helper\n"
    assert calls_missing(text, ["lib-a.sh", "lib-b.sh"], have=have, had=_HAD) == set()


def test_deeply_nested_substitutions_never_crash() -> None:
    """Codex KI2-r3 KI6: ~500 nested "$(..)" raised RecursionError during install and doctor."""
    from trw_mcp.server._doctor_hook_family import calls_missing, defined_functions

    deep = 'x="' + '$(echo "' * 600 + "y" + '")' * 600 + '"\n'
    text = "#!/bin/sh\n. ./lib-a.sh\n" + deep + "old_helper\n"
    assert defined_functions(text) == set()
    # No crash, and no false miss either: the over-deep construct is reported as unverifiable.
    assert calls_missing(text, ["lib-a.sh"], have=_HAVE, had=_HAD) == set()
    # Contrast: the same call without the nested construct is a definite miss.
    plain = "#!/bin/sh\n. ./lib-a.sh\nold_helper\n"
    assert calls_missing(plain, ["lib-a.sh"], have=_HAVE, had=_HAD) == {"old_helper"}


_DEEP = 'x="' + '$(echo "' * 600 + "y" + '")' * 600 + '"\n'


@pytest.mark.parametrize(
    ("body", "have"),
    [
        (_DEEP + "old_helper\n", _HAVE),  # codex KI2-r3 KI6: was a RecursionError
        ('echo "$(case x in x) :;; esac; old_helper)"\n', _HAVE),  # KI2-r3 KI2
        ("run() { old_helper; }\n. ./lib-b.sh\nrun\n", {"lib-a.sh": set(), "lib-b.sh": {"old_helper"}}),  # KI4
    ],
)
def test_shell_the_checker_does_not_model_is_uncertain_never_a_guess(body: str, have: dict[str, set[str]]) -> None:
    """Lead ruling on KI2-r3: unmodelled shell gives a reason (keep + warn), never a crash or a false miss."""
    from trw_mcp.server._doctor_hook_family import verify_calls

    missing, reason = verify_calls("#!/bin/sh\n. ./lib-a.sh\n" + body, ["lib-a.sh", "lib-b.sh"], have=have, had=_HAD)
    assert reason and missing == set()


def test_a_fully_modelled_miss_is_definite() -> None:
    from trw_mcp.server._doctor_hook_family import verify_calls

    assert verify_calls("#!/bin/sh\n. ./lib-a.sh\nold_helper\n", ["lib-a.sh"], have=_HAVE, had=_HAD) == (
        {"old_helper"},
        None,
    )


def test_doctor_warns_rather_than_fails_on_a_hook_it_cannot_verify(tmp_path: Path) -> None:
    hooks = _deploy(tmp_path)
    hook = hooks / "session-start.sh"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n" + _DEEP, encoding="utf-8")

    status, message = _row(tmp_path)

    assert status == "WARN", message
    assert "session-start.sh" in message and "could not verify" in message
