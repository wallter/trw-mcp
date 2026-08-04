"""One Cursor bootstrap pass must not deregister the other's hooks.

``smart_merge_cursor_json`` stripped every handler whose command started with the
identity prefix ``.cursor/hooks/trw-`` from **every** event in ``hooks.json``.
Two callers pass that same prefix with **disjoint** event maps — the cursor-ide
pass writes 8 events, the cursor-cli pass 5. So on a project listing both Cursor
surfaces the second pass cleared all 8 and re-added only its own 5, silently
unregistering the other surface while the installer reported success.

The direction that matters most: it could leave ``beforeShellExecution`` as ``[]``
— the ``failClosed: true`` secret-scan gate deregistered entirely. That is the one
hook where "did nothing" and "scanned and allowed" are the same observable.

An existing test (``test_cursor_cli_bootstrap_hooks.py::TestCliHooksPreservesIdeEvents``)
was supposed to cover this and could not: it seeded ``user-*.sh`` commands that do
not match the TRW prefix, and asserted key *presence* rather than list contents, so
it passed against ``[]``. This module seeds real TRW commands and asserts the
commands survive.
"""

from __future__ import annotations

import json
from pathlib import Path


def _merge(path: Path, entries: dict[str, object]) -> None:
    from trw_mcp.bootstrap._cursor_hooks_io import smart_merge_cursor_json

    smart_merge_cursor_json(path, entries, identity_prefix=".cursor/hooks/trw-")


def _ide_entries() -> dict[str, object]:
    return {
        "hooks": {
            "beforeShellExecution": [{"command": ".cursor/hooks/trw-before-shell.sh"}],
            "sessionStart": [{"command": ".cursor/hooks/trw-session-start.sh"}],
            "afterFileEdit": [{"command": ".cursor/hooks/trw-after-file-edit.sh"}],
        }
    }


def _cli_entries() -> dict[str, object]:
    """Deliberately DISJOINT from the IDE map — that disjointness is the defect."""
    return {"hooks": {"beforeSubmitPrompt": [{"command": ".cursor/hooks/trw-before-submit-prompt.sh"}]}}


def _commands(path: Path, event: str) -> list[str]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return [h.get("command", "") for h in doc.get("hooks", {}).get(event, [])]


def test_the_second_pass_registers_its_own_hooks(tmp_path: Path) -> None:
    """Non-vacuity control. If the CLI pass wrote nothing, the survival assertion
    below would pass for the wrong reason."""
    target = tmp_path / "hooks.json"
    _merge(target, _ide_entries())
    _merge(target, _cli_entries())

    assert _commands(target, "beforeSubmitPrompt") == [".cursor/hooks/trw-before-submit-prompt.sh"]


def test_the_second_pass_does_not_deregister_the_first(tmp_path: Path) -> None:
    """The defect. RED before scoping: all three IDE events became []."""
    target = tmp_path / "hooks.json"
    _merge(target, _ide_entries())
    _merge(target, _cli_entries())

    assert _commands(target, "beforeShellExecution") == [".cursor/hooks/trw-before-shell.sh"], (
        "the failClosed secret-scan gate was deregistered by the sibling Cursor pass"
    )
    assert _commands(target, "sessionStart") == [".cursor/hooks/trw-session-start.sh"]
    assert _commands(target, "afterFileEdit") == [".cursor/hooks/trw-after-file-edit.sh"]


def test_a_pass_still_replaces_its_own_stale_entry(tmp_path: Path) -> None:
    """Precision control. Scoping must not turn the merge into append-only —
    re-running the same pass has to replace, not duplicate."""
    target = tmp_path / "hooks.json"
    _merge(target, _ide_entries())
    _merge(target, _ide_entries())

    assert _commands(target, "beforeShellExecution") == [".cursor/hooks/trw-before-shell.sh"], (
        "re-running a pass duplicated its own handler"
    )


def test_a_user_authored_handler_is_never_stripped(tmp_path: Path) -> None:
    """The prefix exists to protect user hooks; that must still hold."""
    target = tmp_path / "hooks.json"
    target.write_text(
        json.dumps({"hooks": {"beforeShellExecution": [{"command": ".cursor/hooks/user-audit.sh"}]}}),
        encoding="utf-8",
    )
    _merge(target, _ide_entries())

    assert ".cursor/hooks/user-audit.sh" in _commands(target, "beforeShellExecution")
