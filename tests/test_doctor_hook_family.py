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
