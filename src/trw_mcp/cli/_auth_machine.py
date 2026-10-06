"""Machine-level sign-in for the TRW auth CLI: one key for every project on this computer.

Belongs to the ``auth.py`` facade. The store and its owner-only read rule live in
``models/config/_credentials.py`` (``~/.trw/credentials.yaml``); this module holds only the
operator-facing steps: asking before saving there after a sign-in, ``auth login --machine`` /
``--no-machine``, ``auth promote`` (copy this project's key up) and ``auth logout --machine``.

Nothing here prints a key: messages name the file and the layer, never the value.
"""

from __future__ import annotations

import sys
from pathlib import Path

from trw_memory.exceptions import UnsafeWriteError

from trw_mcp.models.config._credentials import (
    MACHINE_CREDENTIALS_LABEL,
    credentials_path_for,
    machine_credentials_path,
    read_key_from_file,
    remove_credentials_key,
    write_machine_key,
)


def _ask_machine_save() -> bool:
    """Ask once whether to keep this sign-in for every project (default yes). No terminal: no."""
    if not (hasattr(sys.stdin, "isatty") and sys.stdin.isatty()):
        return False
    try:
        answer = input(
            f"  Use this sign-in for every TRW project on this computer? "
            f"It is saved to {MACHINE_CREDENTIALS_LABEL} (owner-only, 0600). [Y/n] "
        )
    # trw-fail-silent-allow: no answer is no consent; the key then stays project-only (the documented default)
    except EOFError:
        return False
    return answer.strip().lower() in ("", "y", "yes")


def save_machine_key(api_key: str) -> bool:
    """Write *api_key* to the machine store and say where it went. False (with a message) on refusal."""
    try:
        path = write_machine_key(api_key)
    # trw-fail-silent-allow: the refusal is printed to the operator and False makes the caller keep the project copy
    except (UnsafeWriteError, OSError, ValueError) as exc:
        print(f"  Could not save to {MACHINE_CREDENTIALS_LABEL}: {type(exc).__name__}: {exc}")
        return False
    print(f"  Saved for every project on this computer: {path} (mode 0600)")
    print("  Undo: trw-mcp auth logout --machine")
    return True


def maybe_save_machine_key(api_key: str, machine: bool | None) -> bool:
    """After a sign-in: save to the machine store when *machine* is True, or when None and the operator says yes."""
    wanted = _ask_machine_save() if machine is None else machine
    return save_machine_key(api_key) if wanted else False


def run_auth_promote(config_path: Path) -> int:
    """``trw-mcp auth promote``: copy this project's key to the machine store. Returns an exit code."""
    key = read_key_from_file(credentials_path_for(config_path))
    if not key:
        print(f"  No API key in {credentials_path_for(config_path)} to promote. Run: trw-mcp auth login")
        return 1
    return 0 if save_machine_key(key) else 1


def run_machine_logout() -> int:
    """``trw-mcp auth logout --machine``: delete the machine store. Projects with their own key keep it."""
    path = machine_credentials_path()
    if remove_credentials_key(path):
        print(f"  Removed the computer-wide sign-in ({path}). Projects with their own .trw/credentials.yaml keep it.")
    else:
        print(f"  No computer-wide sign-in found ({path}).")
    return 0
